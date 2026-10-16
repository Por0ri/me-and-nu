"""Compose crop-safe, feed-ready PNG cards from approved image assets and card copy."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from ani_menu_agent.models import CardSpec, ContentPackage, ImageAsset

OUTPUT_WIDTH = 327
OUTPUT_HEIGHT = 408
RENDER_SCALE = 4
CARD_WIDTH = OUTPUT_WIDTH * RENDER_SCALE
CARD_HEIGHT = OUTPUT_HEIGHT * RENDER_SCALE
OUTPUT_IMAGE_SIZE = (CARD_WIDTH, CARD_HEIGHT)
_ACCENT = (255, 104, 67)
_WHITE = (250, 249, 247)
_MUTED = (224, 222, 224)


def _font_path(*, bold: bool) -> Path:
    candidates = (
        [
            Path("C:/Windows/Fonts/malgunbd.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
            Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
        ]
        if bold
        else [
            Path("C:/Windows/Fonts/malgun.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    weight = "bold" if bold else "regular"
    raise RuntimeError(
        f"한글 카드 렌더용 {weight} 폰트를 찾지 못했습니다. "
        "Windows는 맑은 고딕, Linux는 Noto Sans CJK KR 폰트를 설치해 주세요."
    )


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_font_path(bold=bold)), size=size)


def _image_from_bytes(binary: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(binary)) as opened:
            opened.seek(0)
            return ImageOps.exif_transpose(opened).convert("RGBA")
    except Exception as exc:
        raise ValueError("카드에 사용할 원본 이미지를 열 수 없습니다.") from exc


def _contained(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = min(width / image.width, height / image.height)
    target = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(target, Image.Resampling.LANCZOS)


def _background(image: Image.Image, style: str = "blur") -> Image.Image:
    """Make a continuous background; only this decorative layer may fill/crop the canvas."""
    source = image.convert("RGBA")
    alpha = source.getchannel("A")
    if alpha.getextrema()[0] < 255:
        base = Image.new("RGBA", source.size, (25, 27, 38, 255))
        source = Image.alpha_composite(base, source)
    mean = ImageStat.Stat(source.convert("RGB")).mean
    dark_palette = tuple(
        round(channel * 0.42 + shade * 0.58)
        for channel, shade in zip(mean, (12, 16, 25), strict=True)
    )
    if style == "palette":
        return Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (*dark_palette, 255))

    backdrop = ImageOps.fit(
        source.convert("RGB"),
        (CARD_WIDTH, CARD_HEIGHT),
        method=Image.Resampling.LANCZOS,
    ).filter(ImageFilter.GaussianBlur(radius=82))
    backdrop = Image.blend(
        backdrop,
        Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (*dark_palette, 255)),
        0.52,
    )
    return backdrop.convert("RGBA")


def _bottom_scrim() -> Image.Image:
    """Add a full-height, continuous scrim; never start it at a visible horizontal edge."""
    gradient = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)
    for row in range(CARD_HEIGHT):
        progress = row / max(1, CARD_HEIGHT - 1)
        alpha = int(184 * progress**1.8)
        draw.line((0, row, CARD_WIDTH, row), fill=(5, 7, 10, alpha))
    return gradient


def _paste_uncropped(
    canvas: Image.Image,
    image: Image.Image,
    box: tuple[int, int, int, int],
) -> None:
    left, top, width, height = box
    contained = _contained(image, width, height)
    x = left + (width - contained.width) // 2
    y = top + (height - contained.height) // 2

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rectangle(
        (x - 9, y - 9, x + contained.width + 9, y + contained.height + 9),
        fill=(0, 0, 0, 118),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=18))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(contained, (x, y))
    ImageDraw.Draw(canvas).rectangle(
        (x, y, x + contained.width - 1, y + contained.height - 1),
        outline=(255, 255, 255, 105),
        width=2,
    )


def _paste_cutout(
    canvas: Image.Image,
    image: Image.Image,
    box: tuple[int, int, int, int],
) -> bool:
    """Composite a transparent subject, trimming only invisible padding, never artwork."""
    alpha = image.getchannel("A")
    if alpha.getextrema()[0] == 255:
        return False
    visible_box = alpha.getbbox()
    if visible_box is None:
        return False
    subject = image.crop(visible_box)
    left, top, width, height = box
    contained = _contained(subject, width, height)
    x = left + (width - contained.width) // 2
    y = top + (height - contained.height) // 2

    shadow_alpha = contained.getchannel("A").filter(ImageFilter.GaussianBlur(radius=18))
    shadow = Image.new("RGBA", contained.size, (0, 0, 0, 118))
    shadow.putalpha(shadow_alpha)
    canvas.alpha_composite(shadow, (x, y))
    canvas.alpha_composite(contained, (x, y))
    return True


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bounds = draw.textbbox((0, 0), text, font=font, stroke_width=0)
    return bounds[2] - bounds[0]


def _wrap_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    width: int,
) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if _text_width(draw, candidate, font) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if _text_width(draw, word, font) <= width:
            current = word
            continue
        fragment = ""
        for character in word:
            if fragment and _text_width(draw, fragment + character, font) > width:
                lines.append(fragment)
                fragment = character
            else:
                fragment += character
        current = fragment
    if current:
        lines.append(current)
    return lines


def _copy_box(card: CardSpec) -> tuple[int, int, int, int]:
    box = card.layout.copy_box
    return (
        int(box.x * CARD_WIDTH),
        int(box.y * CARD_HEIGHT),
        int(box.width * CARD_WIDTH),
        int(box.height * CARD_HEIGHT),
    )


def _draw_copy(canvas: Image.Image, card: CardSpec, package: ContentPackage) -> None:
    draw = ImageDraw.Draw(canvas)
    x, y, width, height = _copy_box(card)
    compact = card.layout.template.startswith(("side_", "cutout_"))
    inset = 8 if card.layout.template == "split" else 4
    x += inset
    width -= inset * 2
    if card.layout.template == "split":
        panel = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        panel_draw = ImageDraw.Draw(panel)
        panel_draw.rounded_rectangle(
            (x - 18, y - 18, x + width + 18, y + height + 18),
            radius=18,
            fill=(9, 12, 16, 104),
            outline=(255, 255, 255, 28),
            width=1,
        )
        canvas.alpha_composite(panel)
        draw = ImageDraw.Draw(canvas)

    eyebrow_font = _font(10 * RENDER_SCALE, bold=True)
    title_font_size = (
        15 if compact else 20 if card.layout.template != "split" else 16
    ) * RENDER_SCALE
    body_font_size = (8 if compact else 11 if card.layout.template != "split" else 9) * RENDER_SCALE
    eyebrow = card.eyebrow.strip() or card.role.strip()
    draw.text((x, y), eyebrow, font=eyebrow_font, fill=(*_ACCENT, 255))
    title_y = y + 14 * RENDER_SCALE
    text_gap = max(8, int(title_font_size * 0.16))

    while title_font_size >= 38:
        title_font = _font(title_font_size, bold=True)
        wrapped = [
            line
            for original in card.title_lines
            for line in _wrap_line(draw, original, title_font, width)
        ]
        title_line_height = int(title_font_size * 1.12)
        title_height = title_line_height * len(wrapped)
        remaining = y + height - title_y - title_height - text_gap - 14
        if remaining >= max(18 * RENDER_SCALE, len(card.body_sentences) * 11 * RENDER_SCALE):
            break
        title_font_size -= 2

    for index, line in enumerate(wrapped):
        color = _ACCENT if index == len(wrapped) - 1 and len(wrapped) > 1 else _WHITE
        draw.text(
            (x, title_y + index * title_line_height),
            line,
            font=title_font,
            fill=(*color, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 150),
        )

    body_y = title_y + title_height + text_gap
    if card.body_sentences:
        body_font = _font(body_font_size)
        body_lines = [
            line
            for sentence in card.body_sentences
            for line in _wrap_line(draw, sentence, body_font, width)
        ]
        line_height = int(body_font_size * 1.35)
        max_body_lines = max(1, (y + height - body_y - 16) // line_height)
        if len(body_lines) > max_body_lines:
            # The copy must remain legible and complete; fail instead of silently clipping it.
            while body_font_size > 18:
                body_font_size -= 1
                body_font = _font(body_font_size)
                body_lines = [
                    line
                    for sentence in card.body_sentences
                    for line in _wrap_line(draw, sentence, body_font, width)
                ]
                line_height = int(body_font_size * 1.35)
                max_body_lines = max(1, (y + height - body_y - 16) // line_height)
                if len(body_lines) <= max_body_lines:
                    break
        if len(body_lines) > max_body_lines:
            raise ValueError(f"{card.page}장 본문이 텍스트 영역에 들어가지 않습니다.")
        for index, line in enumerate(body_lines):
            draw.text(
                (x, body_y + index * line_height),
                line,
                font=body_font,
                fill=(*_MUTED, 255),
                stroke_width=1,
                stroke_fill=(0, 0, 0, 110),
            )


def render_card_png(
    package: ContentPackage,
    card: CardSpec,
    image: ImageAsset,
    image_bytes: bytes,
) -> bytes:
    """Render one crop-safe 1308×1632 feed card at 4× the logical canvas size."""
    foreground = _image_from_bytes(image_bytes)
    canvas = _background(foreground, card.layout.background_style)
    draw = ImageDraw.Draw(canvas)

    box = card.layout.image_box
    pixel_box = (
        int(box.x * CARD_WIDTH),
        int(box.y * CARD_HEIGHT),
        int(box.width * CARD_WIDTH),
        int(box.height * CARD_HEIGHT),
    )
    if not card.layout.template.startswith("cutout_") or not _paste_cutout(
        canvas, foreground, pixel_box
    ):
        _paste_uncropped(canvas, foreground, pixel_box)

    # Keep the scrim continuous from the top so it cannot read as a separate text panel.
    canvas.alpha_composite(_bottom_scrim())

    draw = ImageDraw.Draw(canvas)
    brand_font = _font(10 * RENDER_SCALE, bold=True)
    small_font = _font(6 * RENDER_SCALE)
    label = "INFORMATION" if package.content_kind.value == "information" else "HUMOR"
    label_font = _font(6 * RENDER_SCALE, bold=True)
    draw.text(
        (14 * RENDER_SCALE, 10 * RENDER_SCALE),
        "me;nu",
        font=brand_font,
        fill=(255, 255, 255, 245),
    )
    label_width = _text_width(draw, label, label_font)
    pill = (
        CARD_WIDTH - 14 * RENDER_SCALE - label_width - 10 * RENDER_SCALE,
        10 * RENDER_SCALE,
        CARD_WIDTH - 14 * RENDER_SCALE,
        23 * RENDER_SCALE,
    )
    draw.rounded_rectangle(
        pill,
        radius=6 * RENDER_SCALE,
        fill=(8, 10, 13, 128),
        outline=(*_ACCENT, 210),
        width=RENDER_SCALE,
    )
    draw.text(
        (pill[0] + 5 * RENDER_SCALE, 12 * RENDER_SCALE),
        label,
        font=label_font,
        fill=(*_ACCENT, 255),
    )

    _draw_copy(canvas, card, package)

    sources = {source.id: source for source in package.sources}
    related = [
        sources[fact_source_id]
        for fact in package.facts
        if fact.id in card.fact_ids
        for fact_source_id in fact.source_ids
        if fact_source_id in sources
    ]
    publisher = (
        related[0].publisher
        if related
        else package.sources[0].publisher
        if package.sources
        else "공식 출처"
    )
    footer_draw = ImageDraw.Draw(canvas)
    footer_draw.text(
        (14 * RENDER_SCALE, CARD_HEIGHT - 14 * RENDER_SCALE),
        f"출처: {publisher[:28]}",
        font=small_font,
        fill=(238, 235, 231, 205),
    )
    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_package_cards(
    package: ContentPackage,
    *,
    get_image_bytes,
) -> dict[int, bytes]:
    """Render every card, failing the package if an assigned source asset is unavailable."""
    images = {image.id: image for image in package.images}
    rendered: dict[int, bytes] = {}
    for card in package.cards:
        image = images.get(card.image_id)
        if image is None:
            raise ValueError(f"{card.page}장에 연결된 원본 이미지가 없습니다: {card.image_id}")
        stored = get_image_bytes(image.storage_key) if image.storage_key else None
        if stored is not None:
            image_bytes, _media_type = stored
        elif image.content_bytes is not None:
            image_bytes = image.content_bytes
        elif image.local_path and Path(image.local_path).is_file():
            image_bytes = Path(image.local_path).read_bytes()
        else:
            raise ValueError(f"{card.page}장 원본 이미지가 DB에 없습니다: {image.id}")
        rendered[card.page] = render_card_png(package, card, image, image_bytes)
    if len(rendered) != len(package.cards):
        raise ValueError("모든 카드 PNG를 생성하지 못했습니다.")
    return rendered
