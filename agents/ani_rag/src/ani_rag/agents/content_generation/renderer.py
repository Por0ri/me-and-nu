"""Page-level, text-free PNG plates with separate HTML copy/layout manifests."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import file_digest
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat

from ani_rag.content import DESIGNED_COVERS, CardDraft, ContentDraft, ImageAsset

LOGICAL_WIDTH = 327
LOGICAL_HEIGHT = 408
RENDER_SCALE = 4
WIDTH = LOGICAL_WIDTH * RENDER_SCALE
HEIGHT = LOGICAL_HEIGHT * RENDER_SCALE
_GRID_COLOR = (17, 17, 21, 255)


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
    _validate_deck_images(draft, assets)
    for card in draft.cards:
        foreground = _open_art(assets[card.image_asset_id])
        panorama_crop = False
        if card.layout.template in DESIGNED_COVERS:
            background = _cover_from_sources(card, foreground, assets)
        else:
            background = _source_background(foreground, card.layout.background_style).convert("RGBA")
            box = card.layout.image_box
            placement = (int(WIDTH * box.x), int(HEIGHT * box.y), int(WIDTH * box.width), int(HEIGHT * box.height))
            panorama_crop = foreground.width / foreground.height >= 2.4 and card.layout.image_emphasis == "dominant"
            if panorama_crop:
                _paste_panorama_crop(background, foreground, placement)
            else:
                _paste_uncropped(background, foreground, placement)
        background = _soften_copy_zone(background, card.layout)
        background = Image.alpha_composite(
            background.convert("RGBA"), _copy_scrim(card.layout)
        ).convert("RGB")
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
                    "source_image_width": foreground.width,
                    "source_image_height": foreground.height,
                    "panorama_crop": panorama_crop,
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


def _validate_deck_images(draft: ContentDraft, assets: dict[str, ImageAsset]) -> None:
    used: set[tuple[str, str]] = set()
    for index, card in enumerate(draft.cards):
        if index and card.layout.template in DESIGNED_COVERS:
            raise ValueError("A cover design can only be applied to the first card")
        for asset_id in card.source_asset_ids:
            asset = assets.get(asset_id)
            if asset is None or not asset.local_path:
                raise ValueError(f"Missing local image asset {asset_id!r}")
            keys = asset.source_identity_keys
            if not asset.content_sha256:
                with open(asset.local_path, "rb") as image_file:
                    keys = keys | {("sha256", file_digest(image_file, "sha256").hexdigest())}
            if keys & used:
                raise ValueError("A card deck cannot reuse an image asset")
            used.update(keys)


def _open_art(asset: ImageAsset) -> Image.Image:
    with Image.open(asset.local_path) as source:
        return ImageOps.exif_transpose(source).convert("RGBA")


def compose_cover_art(card: CardDraft, assets: dict[str, ImageAsset]) -> Image.Image:
    """Give the layout reviewer the same composed artwork as the published plate."""
    return _cover_from_sources(card, _open_art(assets[card.image_asset_id]), assets)


def _cover_from_sources(card: CardDraft, primary: Image.Image, assets: dict[str, ImageAsset]) -> Image.Image:
    template = card.layout.template
    copy_top = round(HEIGHT * card.layout.text_overlay_box.y)
    if template == "beginner_guide":
        canvas = _source_background(primary, card.layout.background_style).convert("RGBA")
        poster = _fit_poster(primary, WIDTH - 32, copy_top - 32)
        poster.putalpha(ImageChops.multiply(poster.getchannel("A"), _feather_mask(poster.size)))
        canvas.alpha_composite(poster, ((WIDTH - poster.width) // 2, (copy_top - poster.height) // 2))
        return canvas
    if template == "cast_intro_grid":
        return _cast_grid(card, primary, assets, copy_top)
    if template == "fan_recommendation":
        return _recommendation_cover(card, primary, assets, copy_top)
    raise ValueError(f"Unsupported cover design {template}")


def _cast_grid(card: CardDraft, primary: Image.Image, assets: dict[str, ImageAsset], copy_top: int) -> Image.Image:
    count = len(card.source_asset_ids)
    cols, rows = (2, 2) if count == 4 else (3, 1)
    gap = 8
    band_height = HEIGHT if count == 4 else min(round(HEIGHT * 0.66), copy_top)
    band = Image.new("RGBA", (WIDTH, band_height), _GRID_COLOR)
    for index, asset_id in enumerate(card.source_asset_ids):
        portrait = primary if index == 0 else _open_art(assets[asset_id])
        row, col = divmod(index, cols)
        left = col * (WIDTH + gap) // cols
        top = row * (band_height + gap) // rows
        right = (col + 1) * (WIDTH + gap) // cols - gap
        bottom = (row + 1) * (band_height + gap) // rows - gap
        face = ImageOps.fit(
            portrait, (right - left, bottom - top), Image.Resampling.LANCZOS, centering=(0.5, 0.18),
        )
        band.alpha_composite(face, (left, top))
    if count == 4:
        return band
    bleed = band.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    canvas = ImageEnhance.Brightness(bleed).enhance(0.55)
    canvas.paste(band, (0, 0))
    return canvas


def _fit_poster(image: Image.Image, max_width: int, max_height: int) -> Image.Image:
    scale = min(max_width / image.width, max_height / image.height, 2.0)
    size = (max(1, round(scale * image.width)), max(1, round(scale * image.height)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _recommendation_cover(card: CardDraft, primary: Image.Image, assets: dict[str, ImageAsset], copy_top: int) -> Image.Image:
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), _GRID_COLOR)
    cell_w, cell_h = WIDTH // 2, HEIGHT // 2
    for index, asset_id in enumerate(card.component_image_asset_ids):
        tile = _fit_poster(_open_art(assets[asset_id]), cell_w - 16, cell_h - 16)
        row, col = divmod(index, 2)
        x = col * cell_w + (cell_w - tile.width) // 2
        y = row * cell_h + (cell_h - tile.height) // 2
        canvas.alpha_composite(tile, (x, y))
    canvas = ImageEnhance.Brightness(canvas).enhance(0.55)
    border = 12
    max_height = min(round((cell_h - 16) * 1.18), copy_top - 2 * border - 32)
    art = _fit_poster(primary, round((cell_w - 16) * 1.18), max_height)
    framed = Image.new("RGBA", (art.width + 2 * border, art.height + 2 * border), "white")
    framed.alpha_composite(art, (border, border))
    left = (WIDTH - framed.width) // 2
    top = min(round(HEIGHT * 0.42) - framed.height // 2, copy_top - framed.height - 16)
    shadow = Image.new("RGBA", canvas.size)
    ImageDraw.Draw(shadow).rounded_rectangle(
        (left + 10, top + 18, left + framed.width + 10, top + framed.height + 18), radius=18, fill=(0, 0, 0, 170)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))
    canvas.alpha_composite(framed, (left, top))
    return canvas


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


def _copy_scrim(layout) -> Image.Image:
    """Fade the artwork under the HTML copy without adding a hard text panel."""
    box = layout.text_overlay_box
    left = round(box.x * WIDTH)
    top = round(box.y * HEIGHT)
    right = round((box.x + box.width) * WIDTH)
    bottom = round((box.y + box.height) * HEIGHT)
    color = (5, 7, 10)
    gradient = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)

    if box.y >= 0.50:
        fade_start = max(0, top - round(0.17 * HEIGHT))
        span = max(1, HEIGHT - fade_start)
        for y in range(fade_start, HEIGHT):
            alpha = round(150 * ((y - fade_start) / span) ** 0.90)
            draw.line((0, y, WIDTH, y), fill=(*color, alpha))
    elif box.y + box.height <= 0.43:
        fade_end = min(HEIGHT, bottom + round(0.18 * HEIGHT))
        span = max(1, fade_end)
        for y in range(fade_end):
            alpha = round(205 * max(0.0, 1 - y / span) ** 0.85)
            draw.line((0, y, WIDTH, y), fill=(*color, alpha))
    elif box.x < 0.30:
        fade_end = min(WIDTH, right + round(0.22 * WIDTH))
        span = max(1, fade_end)
        for x in range(fade_end):
            alpha = round(210 * max(0.0, 1 - x / span) ** 0.82)
            draw.line((x, 0, x, HEIGHT), fill=(*color, alpha))
    elif box.x + box.width > 0.70:
        fade_start = max(0, left - round(0.22 * WIDTH))
        span = max(1, WIDTH - fade_start)
        for x in range(fade_start, WIDTH):
            alpha = round(210 * ((x - fade_start) / span) ** 0.82)
            draw.line((x, 0, x, HEIGHT), fill=(*color, alpha))
    else:
        span = max(1, bottom - top)
        for y in range(max(0, top), min(HEIGHT, bottom)):
            alpha = round(198 * min(1.0, (y - top + 1) / span * 3))
            draw.line((0, y, WIDTH, y), fill=(*color, alpha))
    return gradient


def _soften_copy_zone(canvas: Image.Image, layout) -> Image.Image:
    """Apply a faint blur only behind lower copy; keep the source artwork visible."""
    box = layout.text_overlay_box
    if box.y < 0.50:
        return canvas
    top = max(0, round((box.y - 0.14) * HEIGHT))
    copy_top = max(top + 1, round(box.y * HEIGHT))
    mask = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(mask)
    span = max(1, copy_top - top)
    for y in range(top, HEIGHT):
        alpha = round(76 * min(1.0, (y - top) / span))
        draw.line((0, y, WIDTH, y), fill=alpha)
    softened = canvas.filter(ImageFilter.GaussianBlur(radius=max(10, HEIGHT // 90)))
    canvas.paste(softened, (0, 0), mask)
    return canvas


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

    # Smooth gradients avoid the inner rectangular outline made by nested frames.
    edge_mask = _feather_mask(contained.size)
    art = contained
    art.putalpha(ImageChops.multiply(art.getchannel("A"), edge_mask))
    canvas.alpha_composite(art, (x, y))


def _paste_panorama_crop(
    canvas: Image.Image,
    image: Image.Image,
    box: tuple[int, int, int, int],
) -> None:
    """Enlarge an ultra-wide image for a dominant card over full-source blur bleed."""
    left, top, width, height = box
    if width <= 0 or height <= 0:
        raise ValueError("Image layout box must have positive dimensions")
    cropped = ImageOps.fit(
        image.convert("RGBA"), (width, height), method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    cropped.putalpha(ImageChops.multiply(cropped.getchannel("A"), _feather_mask(cropped.size)))
    canvas.alpha_composite(cropped, (left, top))


def _feather_mask(size: tuple[int, int]) -> Image.Image:
    """Create a smooth four-edge mask without a visible rectangular inner frame."""
    width, height = size
    if width <= 1 or height <= 1:
        return Image.new("L", size, 255)
    fade_x = max(1, min(width // 4, max(32, width // 12)))
    fade_y = max(1, min(height // 4, max(28, height // 9)))
    horizontal = Image.new("L", size, 255)
    vertical = Image.new("L", size, 255)
    horizontal_draw = ImageDraw.Draw(horizontal)
    vertical_draw = ImageDraw.Draw(vertical)

    def smooth(distance: int, fade: int) -> int:
        t = min(1.0, max(0.0, distance / fade))
        return round(255 * t * t * (3 - 2 * t))

    for x in range(fade_x):
        alpha = smooth(x, fade_x)
        horizontal_draw.line((x, 0, x, height - 1), fill=alpha)
        horizontal_draw.line((width - 1 - x, 0, width - 1 - x, height - 1), fill=alpha)
    for y in range(fade_y):
        alpha = smooth(y, fade_y)
        vertical_draw.line((0, y, width - 1, y), fill=alpha)
        vertical_draw.line((0, height - 1 - y, width - 1, height - 1 - y), fill=alpha)
    return ImageChops.multiply(horizontal, vertical).filter(
        ImageFilter.GaussianBlur(radius=max(2, min(fade_x, fade_y) // 12))
    )


class ReviewCopyOverflow(ValueError):
    """Only a measured overlay overflow is an editorial correction."""


def render_review_preview(png: bytes, card) -> bytes:
    """Build a temporary HTML-overlay approximation for visual review only.

    The persisted PNG remains text-free. The preview displays the same copy in
    its normalized lower-page area so the reviewer can judge image/copy balance.
    """

    candidates = (
        Path("C:/Windows/Fonts/malgun.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
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

    def wrap(text: str, font: ImageFont.FreeTypeFont, stroke: int) -> list[str]:
        lines: list[str] = []
        # Keep model-requested line breaks as real layout boundaries. Treating a
        # newline as a printable character made the preview's measured line count
        # differ from PIL's rendered height, so body copy could overlap a headline.
        for paragraph in text.splitlines() or [""]:
            line = ""
            for character in paragraph:
                candidate = line + character
                bounds = draw.textbbox((0, 0), candidate, font=font, stroke_width=stroke)
                if line and bounds[2] - bounds[0] > width:
                    lines.append(line)
                    line = character
                else:
                    line = candidate
            if line:
                lines.append(line)
        return lines

    title_y = y + 52
    for step in range(9):
        title_size = max(44, 76 - 4 * step)
        body_size = max(30, 43 - 3 * step)
        title_font = ImageFont.truetype(str(font_path), title_size)
        body_font = ImageFont.truetype(str(font_path), body_size)
        title_lines = wrap(card.headline, title_font, 2)
        body_lines = wrap(card.body, body_font, 1)
        title_step = int(title_size * 1.18)
        body_step = int(body_size * 1.30)
        body_y = title_y + len(title_lines) * title_step + 12
        if body_y + len(body_lines) * body_step <= y + height:
            break
    else:
        raise ReviewCopyOverflow(f"Card {card.card_id!r} exceeds the overlay at the minimum readable font sizes")
    for index, value in enumerate(title_lines):
        draw.text((x, title_y + index * title_step), value, font=title_font, anchor="lt",
                  fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 180))
    for index, value in enumerate(body_lines):
        draw.text((x, body_y + index * body_step), value, font=body_font, anchor="lt",
                  fill=(231, 229, 229, 255), stroke_width=1, stroke_fill=(0, 0, 0, 150))
    output = BytesIO()
    canvas.convert("RGB").save(output, format="JPEG", quality=84, optimize=True)
    return output.getvalue()
