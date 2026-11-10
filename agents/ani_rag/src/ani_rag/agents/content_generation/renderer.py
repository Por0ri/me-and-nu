"""Page-level, text-free PNG plates with separate HTML copy/layout manifests."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from ani_rag.content import ContentDraft, ImageAsset

LOGICAL_WIDTH = 327
LOGICAL_HEIGHT = 408
RENDER_SCALE = 4
WIDTH = LOGICAL_WIDTH * RENDER_SCALE
HEIGHT = LOGICAL_HEIGHT * RENDER_SCALE


@dataclass(frozen=True)
class RenderedCard:
    card_id: str
    image_png: bytes
    frontend_copy: dict[str, str]
    frontend_layout: dict[str, object]
    width: int = WIDTH
    height: int = HEIGHT


def render_text_free_cards(draft: ContentDraft, assets: dict[str, ImageAsset]) -> tuple[RenderedCard, ...]:
    """Compose artwork only; copy and normalized HTML overlay remain page-specific data."""
    rendered: list[RenderedCard] = []
    used: set[str] = set()
    for card in draft.cards:
        asset = assets.get(card.image_asset_id)
        if card.image_asset_id in used:
            raise ValueError("A card deck cannot reuse an image asset")
        used.add(card.image_asset_id)
        if asset is None or not asset.local_path:
            raise ValueError(f"Missing local image asset {card.image_asset_id!r}")
        with Image.open(asset.local_path) as source:
            foreground = ImageOps.exif_transpose(source).convert("RGBA")
        background = _source_background(foreground, card.layout.background_style).convert("RGBA")
        art_box = card.layout.image_box
        art_area = (int(WIDTH * art_box.width), int(HEIGHT * art_box.height))
        _paste_uncropped(background, foreground, (
            int(WIDTH * art_box.x), int(HEIGHT * art_box.y),
            art_area[0], art_area[1],
        ))
        if card.layout.background_style == "gradient":
            fade = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            pixels = fade.load()
            y0 = int(HEIGHT * card.layout.text_overlay_box.y)
            for row in range(y0, HEIGHT):
                alpha = round(150 * (row - y0) / max(1, HEIGHT - y0 - 1))
                for column in range(WIDTH):
                    pixels[column, row] = (9, 12, 18, alpha)
            background = Image.alpha_composite(background, fade).convert("RGB")
        output = BytesIO()
        background.save(output, format="PNG", optimize=True)
        rendered.append(
            RenderedCard(
                card_id=card.card_id,
                image_png=output.getvalue(),
                frontend_copy={"role": card.role, "headline": card.headline, "body": card.body},
                frontend_layout={
                    "template": card.layout.template,
                    "image_emphasis": card.layout.image_emphasis,
                    "background_style": card.layout.background_style,
                    "image_box": {
                        "x": card.layout.image_box.x, "y": card.layout.image_box.y,
                        "width": card.layout.image_box.width, "height": card.layout.image_box.height,
                    },
                    "text_overlay_box": {
                        "x": card.layout.text_overlay_box.x, "y": card.layout.text_overlay_box.y,
                        "width": card.layout.text_overlay_box.width, "height": card.layout.text_overlay_box.height,
                    },
                    "copy_rendering": "html",
                    "png_contains_copy": False,
                },
            )
        )
    return tuple(rendered)


def _source_background(image: Image.Image, style: str) -> Image.Image:
    """Build the same-image blur bleed and source-derived dark palette used by me;nu."""
    source = image.convert("RGBA")
    alpha = source.getchannel("A")
    if alpha.getextrema()[0] < 255:
        source = Image.alpha_composite(Image.new("RGBA", source.size, (25, 27, 38, 255)), source)
    mean = ImageStat.Stat(source.convert("RGB")).mean
    dark_palette = tuple(
        round(channel * 0.42 + shade * 0.58)
        for channel, shade in zip(mean, (12, 16, 25), strict=True)
    )
    blurred = ImageOps.fit(
        source.convert("RGB"), (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS,
    ).filter(ImageFilter.GaussianBlur(radius=RENDER_SCALE * 17))
    palette = Image.new("RGB", (WIDTH, HEIGHT), (*dark_palette, 255))
    blend = 0.22 if style == "palette" else 0.10 if style == "gradient" else 0.12
    return Image.blend(blurred, palette, blend)


def _paste_uncropped(
    canvas: Image.Image,
    image: Image.Image,
    box: tuple[int, int, int, int],
) -> None:
    """Keep the full source artwork and softly blend its edges into the blur bleed."""
    left, top, width, height = box
    if width <= 0 or height <= 0:
        raise ValueError("Image layout box must have positive dimensions")
    contained = ImageOps.contain(image.convert("RGBA"), (width, height), method=Image.Resampling.LANCZOS)
    x = left + (width - contained.width) // 2
    y = top + (height - contained.height) // 2

    # The central artwork stays crisp. Only its outer edge fades into the same
    # image's enlarged, blurred background, avoiding a hard rectangular tile.
    feather = max(1, min(min(contained.size) // 3, max(48, min(contained.size) // 5)))
    edge_mask = Image.new("L", contained.size, 255)
    mask_draw = ImageDraw.Draw(edge_mask)
    for inset in range(feather):
        progress = inset / max(1, feather - 1)
        alpha = round(255 * progress**0.72)
        mask_draw.rectangle(
            (inset, inset, contained.width - inset - 1, contained.height - inset - 1),
            fill=alpha,
        )
    edge_mask = edge_mask.filter(ImageFilter.GaussianBlur(radius=max(3, feather // 7)))
    art = contained
    art.putalpha(ImageChops.multiply(art.getchannel("A"), edge_mask))
    canvas.alpha_composite(art, (x, y))


def render_review_preview(png: bytes, card) -> bytes:
    """Build a temporary HTML-overlay approximation for visual review only.

    The persisted PNG remains text-free. The preview displays the same copy in
    its normalized lower-page area so the reviewer can judge image/copy balance.
    """

    candidates = (
        Path("C:/Windows/Fonts/malgun.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    )
    font_path = next((item for item in candidates if item.is_file()), None)
    if font_path is None:
        raise RuntimeError("A Korean font is required to create reviewer overlay previews")
    with Image.open(BytesIO(png)) as source:
        canvas = source.convert("RGBA")
    box = card.layout.text_overlay_box
    x, y = round(box.x * WIDTH), round(box.y * HEIGHT)
    width, height = round(box.width * WIDTH), round(box.height * HEIGHT)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    pad = 28
    draw.rounded_rectangle((x - pad, y - pad, x + width + pad, y + height + pad),
                           radius=24, fill=(9, 12, 18, 218))
    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)
    label_font = ImageFont.truetype(str(font_path), 34)
    draw.text((x, y), card.role, font=label_font, fill=(255, 150, 105, 255))

    def wrap(text: str, font: ImageFont.FreeTypeFont) -> list[str]:
        lines: list[str] = []
        # Keep model-requested line breaks as real layout boundaries. Treating a
        # newline as a printable character made the preview's measured line count
        # differ from PIL's rendered height, so body copy could overlap a headline.
        for paragraph in text.splitlines() or [""]:
            line = ""
            for character in paragraph:
                candidate = line + character
                if line and draw.textbbox((0, 0), candidate, font=font)[2] > width:
                    lines.append(line)
                    line = character
                else:
                    line = candidate
            if line:
                lines.append(line)
        return lines

    title_size = 76
    title_font = ImageFont.truetype(str(font_path), title_size)
    title_lines = wrap(card.headline, title_font)
    while len(title_lines) > 2 and title_size > 44:
        title_size -= 4
        title_font = ImageFont.truetype(str(font_path), title_size)
        title_lines = wrap(card.headline, title_font)
    body_size = 43
    body_font = ImageFont.truetype(str(font_path), body_size)
    body_lines = wrap(card.body, body_font)
    while len(body_lines) > 3 and body_size > 30:
        body_size -= 3
        body_font = ImageFont.truetype(str(font_path), body_size)
        body_lines = wrap(card.body, body_font)
    if len(title_lines) > 2 or len(body_lines) > 3:
        raise ValueError(f"Review preview copy for card {card.card_id!r} does not fit its HTML overlay box")
    title_y = y + 52
    for index, value in enumerate(title_lines):
        draw.text((x, title_y + index * int(title_size * 1.18)), value, font=title_font,
                  fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 180))
    body_y = title_y + len(title_lines) * int(title_size * 1.18) + 12
    body_line_height = int(body_size * 1.30)
    if body_y + len(body_lines) * body_line_height > y + height:
        raise ValueError(f"Review preview copy for card {card.card_id!r} exceeds its HTML overlay box")
    for index, value in enumerate(body_lines):
        draw.text((x, body_y + index * body_line_height), value, font=body_font,
                  fill=(231, 229, 229, 255), stroke_width=1, stroke_fill=(0, 0, 0, 150))
    output = BytesIO()
    canvas.convert("RGB").save(output, format="JPEG", quality=84, optimize=True)
    return output.getvalue()
