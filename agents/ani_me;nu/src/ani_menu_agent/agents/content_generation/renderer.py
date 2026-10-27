"""Compose crop-safe, feed-ready PNG cards from approved image assets and card copy."""

from __future__ import annotations

import colorsys
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from ani_menu_agent.models import (
    CONTENT_TYPE_LABELS,
    CardSpec,
    ContentPackage,
    ContentType,
    ImageAsset,
)

OUTPUT_WIDTH = 327
OUTPUT_HEIGHT = 408
RENDER_SCALE = 4
CARD_WIDTH = OUTPUT_WIDTH * RENDER_SCALE
CARD_HEIGHT = OUTPUT_HEIGHT * RENDER_SCALE
OUTPUT_IMAGE_SIZE = (CARD_WIDTH, CARD_HEIGHT)
_ACCENT = (255, 104, 67)
_GRAPHIC_ACCENTS = ((84, 223, 217), (255, 205, 84), (161, 174, 255), (255, 135, 177))
_WHITE = (250, 249, 247)
_MUTED = (224, 222, 224)
_TEXT_PANEL = (9, 12, 18)


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
    # Keep the image's own colors and shapes present across the whole canvas.
    # A flat palette/gradient used to make the source look like a thumbnail
    # pasted above a separate black panel. The blurred extension is derived
    # from the same artwork, so it reads as an intentional full-bleed treatment.
    blurred = ImageOps.fit(
        source.convert("RGB"),
        (CARD_WIDTH, CARD_HEIGHT),
        method=Image.Resampling.LANCZOS,
    ).filter(ImageFilter.GaussianBlur(radius=68))
    palette = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (*dark_palette, 255))
    if style == "palette":
        return Image.blend(blurred, palette, 0.22).convert("RGBA")
    if style == "gradient":
        canvas = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 255))
        draw = ImageDraw.Draw(canvas)
        top = tuple(
            round(channel * 0.66 + shade * 0.34)
            for channel, shade in zip(mean, (22, 28, 42), strict=True)
        )
        middle = tuple(
            round(channel * 0.35 + shade * 0.65)
            for channel, shade in zip(mean, (12, 17, 27), strict=True)
        )
        bottom = (8, 11, 17)
        for row in range(CARD_HEIGHT):
            position = row / max(1, CARD_HEIGHT - 1)
            if position < 0.58:
                ratio = position / 0.58
                color = tuple(
                    round(a * (1 - ratio) + b * ratio)
                    for a, b in zip(top, middle, strict=True)
                )
            else:
                ratio = (position - 0.58) / 0.42
                color = tuple(
                    round(a * (1 - ratio) + b * ratio)
                    for a, b in zip(middle, bottom, strict=True)
                )
            draw.line((0, row, CARD_WIDTH, row), fill=(*color, 255))
        # A light image-derived wash prevents a hard seam around the sharp art
        # while the gradient still protects the HTML copy zone.
        return Image.blend(blurred, canvas.convert("RGB"), 0.36).convert("RGBA")

    return Image.blend(blurred, palette, 0.16).convert("RGBA")


def _editorial_graphic_background(page: int, role: str, copy_text: str = "") -> Image.Image:
    """Legacy entry point retained only to fail closed; anime cards require source art."""
    raise ValueError("Anime content cards require a real, relevant source image.")

    # Unreachable historical experiment; production must never generate placeholders.
    accents = _GRAPHIC_ACCENTS
    accent = accents[(page - 1) % len(accents)]
    top = (24 + page * 3 % 15, 27 + page * 5 % 13, 39 + page * 7 % 18)
    bottom = (9, 13, 20)
    canvas = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (*bottom, 255))
    gradient_draw = ImageDraw.Draw(canvas, "RGBA")
    for y in range(CARD_HEIGHT):
        ratio = y / max(1, CARD_HEIGHT - 1)
        color = tuple(round(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        gradient_draw.line((0, y, CARD_WIDTH, y), fill=(*color, 255))
    draw = ImageDraw.Draw(canvas, "RGBA")
    motif = (page - 1) % 5
    normalized_copy = copy_text.casefold()
    location_terms = ("장소", "위치", "현장", "도쿄", "오사카", "이케부쿠로")
    if any(term in normalized_copy for term in location_terms):
        # Map-like streets and a pin make location details legible without
        # pretending to depict a real venue or map.
        road = (255, 255, 255, 48)
        for points in (
            ((18, 112), (98, 76), (174, 113), (244, 68), (320, 98)),
            ((34, 183), (105, 148), (190, 181), (268, 137), (320, 155)),
            ((90, 35), (118, 96), (102, 155), (137, 231)),
            ((244, 28), (214, 89), (239, 148), (205, 226)),
        ):
            draw.line(
                [(x * RENDER_SCALE, y * RENDER_SCALE) for x, y in points],
                fill=road,
                width=9 * RENDER_SCALE,
                joint="curve",
            )
        pin_x, pin_y = 164 * RENDER_SCALE, 92 * RENDER_SCALE
        draw.ellipse(
            (pin_x - 30 * RENDER_SCALE, pin_y - 30 * RENDER_SCALE,
             pin_x + 30 * RENDER_SCALE, pin_y + 30 * RENDER_SCALE),
            fill=(*accent, 235),
            outline=(255, 255, 255, 230),
            width=4 * RENDER_SCALE,
        )
        draw.polygon(
            ((pin_x - 20 * RENDER_SCALE, pin_y + 15 * RENDER_SCALE),
             (pin_x + 20 * RENDER_SCALE, pin_y + 15 * RENDER_SCALE),
             (pin_x, pin_y + 58 * RENDER_SCALE)),
            fill=(*accent, 235),
        )
        draw.ellipse(
            (pin_x - 8 * RENDER_SCALE, pin_y - 8 * RENDER_SCALE,
             pin_x + 8 * RENDER_SCALE, pin_y + 8 * RENDER_SCALE),
            fill=(255, 255, 255, 245),
        )
    elif any(term in normalized_copy for term in ("굿즈", "상품", "가격", "구성", "세트", "구매")):
        # A small set of abstract package silhouettes suggests a product spread,
        # never a specific licensed item that the source did not show.
        for index, (x, y, width, height) in enumerate(
            ((52, 72, 68, 92), (138, 43, 82, 121), (240, 84, 60, 80))
        ):
            fill = (*_GRAPHIC_ACCENTS[(page + index) % len(_GRAPHIC_ACCENTS)], 218)
            rect = (x * RENDER_SCALE, y * RENDER_SCALE,
                    (x + width) * RENDER_SCALE, (y + height) * RENDER_SCALE)
            draw.rounded_rectangle(rect, radius=13 * RENDER_SCALE, fill=fill,
                                   outline=(255, 255, 255, 180), width=2 * RENDER_SCALE)
            draw.rounded_rectangle(
                ((x + 13) * RENDER_SCALE, (y + 17) * RENDER_SCALE,
                 (x + width - 13) * RENDER_SCALE, (y + 22) * RENDER_SCALE),
                radius=3 * RENDER_SCALE, fill=(255, 255, 255, 220),
            )
            draw.ellipse(
                ((x + width // 2 - 8) * RENDER_SCALE, (y + 40) * RENDER_SCALE,
                 (x + width // 2 + 8) * RENDER_SCALE, (y + 56) * RENDER_SCALE),
                fill=(255, 255, 255, 195),
            )
    elif any(term in normalized_copy for term in ("팸플릿", "티켓", "좌석", "입장객")):
        # An event pass is a useful visual metaphor for ticket/gift details, not a blank frame.
        panel = (38 * RENDER_SCALE, 48 * RENDER_SCALE, 289 * RENDER_SCALE, 194 * RENDER_SCALE)
        draw.rounded_rectangle(panel, radius=18 * RENDER_SCALE, fill=(*accent, 225))
        split_x = 224 * RENDER_SCALE
        for y in range(61, 185, 14):
            draw.ellipse(
                (split_x - 3 * RENDER_SCALE, y * RENDER_SCALE,
                 split_x + 3 * RENDER_SCALE, (y + 6) * RENDER_SCALE),
                fill=(*top, 255),
            )
        for x, y, radius in ((78, 85, 17), (118, 85, 17), (98, 118, 17)):
            r = radius * RENDER_SCALE
            draw.ellipse(
                (x * RENDER_SCALE-r, y * RENDER_SCALE-r,
                 x * RENDER_SCALE+r, y * RENDER_SCALE+r),
                fill=(255, 255, 255, 225),
            )
        draw.rounded_rectangle(
            (59 * RENDER_SCALE, 151 * RENDER_SCALE, 204 * RENDER_SCALE, 166 * RENDER_SCALE),
            radius=7 * RENDER_SCALE,
            fill=(255, 255, 255, 185),
        )
        draw.rounded_rectangle(
            (59 * RENDER_SCALE, 173 * RENDER_SCALE, 179 * RENDER_SCALE, 182 * RENDER_SCALE),
            radius=4 * RENDER_SCALE,
            fill=(255, 255, 255, 130),
        )
        draw.ellipse(
            (246 * RENDER_SCALE, 91 * RENDER_SCALE, 276 * RENDER_SCALE, 121 * RENDER_SCALE),
            fill=(*top, 255),
        )
        draw.rounded_rectangle(
            (242 * RENDER_SCALE, 134 * RENDER_SCALE, 280 * RENDER_SCALE, 171 * RENDER_SCALE),
            radius=8 * RENDER_SCALE,
            fill=(255, 255, 255, 205),
        )
    elif any(term in normalized_copy for term in ("성우", "출연", "캐스트", "voice actor")):
        # A compact ensemble pictogram makes cast pages feel intentional without
        # fabricating or borrowing character artwork.
        colors = (accent, (255, 255, 255), (128, 218, 207), (255, 194, 126))
        for index in range(8):
            row, column = divmod(index, 4)
            x = (62 + column * 68) * RENDER_SCALE
            y = (76 + row * 72) * RENDER_SCALE
            color = colors[index % len(colors)]
            radius = 13 * RENDER_SCALE
            draw.ellipse(
                (x-radius, y-radius, x+radius, y+radius),
                fill=(*color, 225),
            )
            draw.rounded_rectangle(
                (x-21*RENDER_SCALE, y+16*RENDER_SCALE,
                 x+21*RENDER_SCALE, y+39*RENDER_SCALE),
                radius=10*RENDER_SCALE,
                fill=(*color, 165),
            )
            badge_radius = 7 * RENDER_SCALE
            draw.ellipse(
                (x+13*RENDER_SCALE-badge_radius, y-25*RENDER_SCALE-badge_radius,
                 x+13*RENDER_SCALE+badge_radius, y-25*RENDER_SCALE+badge_radius),
                fill=(*top, 255),
            )
    elif any(
        term in normalized_copy
        for term in ("일정", "기간", "날짜", "공연", "개최", "시작", "입장")
    ):
        # Schedule cards receive a timeline motif instead of generic decoration.
        line_y = 122 * RENDER_SCALE
        draw.rounded_rectangle(
            (45 * RENDER_SCALE, line_y-3*RENDER_SCALE,
             282 * RENDER_SCALE, line_y+3*RENDER_SCALE),
            radius=3 * RENDER_SCALE,
            fill=(*accent, 190),
        )
        for x, y_offset in ((75, 0), (164, -24), (253, 0)):
            radius = 22 * RENDER_SCALE
            y = line_y + y_offset * RENDER_SCALE
            draw.ellipse(
                (x*RENDER_SCALE-radius, y-radius, x*RENDER_SCALE+radius, y+radius),
                fill=(*accent, 225),
                outline=(255, 255, 255, 210),
                width=3 * RENDER_SCALE,
            )
            draw.ellipse(
                (x*RENDER_SCALE-5*RENDER_SCALE, y-5*RENDER_SCALE,
                 x*RENDER_SCALE+5*RENDER_SCALE, y+5*RENDER_SCALE),
                fill=(*top, 255),
            )
    elif motif == 0:  # slanted motion lines
        spacing = 62 * RENDER_SCALE
        offset = (page * 29 * RENDER_SCALE) % spacing
        for x in range(-CARD_HEIGHT, CARD_WIDTH + CARD_HEIGHT, spacing):
            draw.line(
                (x + offset, 0, x + offset - CARD_HEIGHT // 3, CARD_HEIGHT // 3),
                fill=(*accent, 24),
                width=2 * RENDER_SCALE,
            )
    elif motif == 1:  # evidence timeline
        y = int(CARD_HEIGHT * 0.24)
        draw.line(
            (36 * RENDER_SCALE, y, CARD_WIDTH - 36 * RENDER_SCALE, y),
            fill=(*accent, 48),
            width=3 * RENDER_SCALE,
        )
        for x in (72, 150, 228):
            radius = 7 * RENDER_SCALE
            draw.ellipse(
                (x * RENDER_SCALE - radius, y - radius, x * RENDER_SCALE + radius, y + radius),
                fill=(*accent, 86),
            )
    elif motif == 2:  # meaningful editorial art, never empty image placeholders
        voice_topic = any(
            term in copy_text.casefold()
            for term in ("성우", "목소리", "음성", "더빙", "보이스", "voice actor", "voice cast")
        )
        if voice_topic:
            # A microphone is used only when the copy is actually about voices/voice actors.
            mic_x, mic_y = 164 * RENDER_SCALE, 108 * RENDER_SCALE
            draw.rounded_rectangle(
                (
                    mic_x - 13 * RENDER_SCALE,
                    mic_y - 34 * RENDER_SCALE,
                    mic_x + 13 * RENDER_SCALE,
                    mic_y + 11 * RENDER_SCALE,
                ),
                radius=13 * RENDER_SCALE,
                fill=(*accent, 235),
            )
            draw.arc(
                (
                    mic_x - 27 * RENDER_SCALE,
                    mic_y - 17 * RENDER_SCALE,
                    mic_x + 27 * RENDER_SCALE,
                    mic_y + 31 * RENDER_SCALE,
                ),
                start=0,
                end=180,
                fill=(*accent, 220),
                width=4 * RENDER_SCALE,
            )
            draw.line(
                (mic_x, mic_y + 7 * RENDER_SCALE, mic_x, mic_y + 43 * RENDER_SCALE),
                fill=(*accent, 220),
                width=4 * RENDER_SCALE,
            )
            draw.line(
                (
                    mic_x - 18 * RENDER_SCALE,
                    mic_y + 43 * RENDER_SCALE,
                    mic_x + 18 * RENDER_SCALE,
                    mic_y + 43 * RENDER_SCALE,
                ),
                fill=(*accent, 220),
                width=4 * RENDER_SCALE,
            )
            for direction in (-1, 1):
                for radius in (34, 48, 62):
                    x = mic_x + direction * radius * RENDER_SCALE
                    draw.arc(
                        (
                            x - 8 * RENDER_SCALE,
                            mic_y - 24 * RENDER_SCALE,
                            x + 8 * RENDER_SCALE,
                            mic_y + 24 * RENDER_SCALE,
                        ),
                        start=270 if direction < 0 else 90,
                        end=90 if direction < 0 else 270,
                        fill=(*accent, max(70, 180 - radius)),
                        width=3 * RENDER_SCALE,
                    )
        else:
            # Filled color blocks and editorial rules give the page visual weight
            # without pretending that the missing asset is a photograph.
            draw.polygon(
                (
                    (35 * RENDER_SCALE, 126 * RENDER_SCALE),
                    (102 * RENDER_SCALE, 45 * RENDER_SCALE),
                    (190 * RENDER_SCALE, 75 * RENDER_SCALE),
                    (120 * RENDER_SCALE, 164 * RENDER_SCALE),
                ),
                fill=(*accent, 170),
            )
            draw.rounded_rectangle(
                (151 * RENDER_SCALE, 54 * RENDER_SCALE, 287 * RENDER_SCALE, 145 * RENDER_SCALE),
                radius=18 * RENDER_SCALE,
                fill=(*accent, 105),
            )
            for bar in range(4):
                y = (74 + bar * 17) * RENDER_SCALE
                draw.rounded_rectangle(
                    (170 * RENDER_SCALE, y, (260 - bar * 11) * RENDER_SCALE, y + 5 * RENDER_SCALE),
                    radius=2 * RENDER_SCALE,
                    fill=(*accent, 205 - bar * 28),
                )
    elif motif == 3:  # focal rings
        center = (CARD_WIDTH - 42 * RENDER_SCALE, 58 * RENDER_SCALE)
        for radius in (32, 52, 72, 92):
            r = radius * RENDER_SCALE
            draw.ellipse(
                (center[0] - r, center[1] - r, center[0] + r, center[1] + r),
                outline=(*accent, 34),
                width=2 * RENDER_SCALE,
            )
    else:  # quiet dot field
        for x in range(22, 310, 24):
            for y in range(28, 250, 24):
                draw.ellipse(
                    (
                        x * RENDER_SCALE,
                        y * RENDER_SCALE,
                        (x + 2) * RENDER_SCALE,
                        (y + 2) * RENDER_SCALE,
                    ),
                    fill=(*accent, 35),
                )
    return canvas


def _content_badge(package: ContentPackage) -> str:
    """Return the feed's actual taxonomy label, not the generation pipeline kind."""
    if package.content_type:
        try:
            return CONTENT_TYPE_LABELS.get(ContentType(package.content_type), "")
        except ValueError:
            return ""
    return "유머" if package.content_kind.value == "humor" else "소식"


def _bottom_scrim() -> Image.Image:
    """Fade artwork into a legible but still image-connected HTML copy area."""
    gradient = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)
    fade_start = 0.48
    for row in range(CARD_HEIGHT):
        progress = max(0.0, (row / max(1, CARD_HEIGHT - 1) - fade_start) / (1 - fade_start))
        alpha = int(222 * progress**0.9)
        draw.line((0, row, CARD_WIDTH, row), fill=(5, 7, 10, alpha))
    return gradient


def _copy_scrim(layout) -> Image.Image:
    """Add a soft, directional matte under the frontend-owned HTML copy zone."""
    box = layout.text_overlay_box
    gradient = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)
    left = int(box.x * CARD_WIDTH)
    top = int(box.y * CARD_HEIGHT)
    right = int((box.x + box.width) * CARD_WIDTH)
    bottom = int(box.bottom * CARD_HEIGHT)
    color = (5, 7, 10)

    if box.y >= 0.50:
        fade_start = max(0, top - int(0.17 * CARD_HEIGHT))
        span = max(1, CARD_HEIGHT - fade_start)
        for y in range(fade_start, CARD_HEIGHT):
            progress = (y - fade_start) / span
            alpha = int(218 * progress**0.82)
            draw.line((0, y, CARD_WIDTH, y), fill=(*color, alpha))
    elif box.bottom <= 0.43:
        fade_end = min(CARD_HEIGHT, bottom + int(0.18 * CARD_HEIGHT))
        span = max(1, fade_end)
        for y in range(fade_end):
            progress = 1 - y / span
            alpha = int(205 * max(0.0, progress) ** 0.85)
            draw.line((0, y, CARD_WIDTH, y), fill=(*color, alpha))
    elif box.x < 0.30:
        fade_end = min(CARD_WIDTH, right + int(0.22 * CARD_WIDTH))
        span = max(1, fade_end)
        for x in range(fade_end):
            progress = 1 - x / span
            alpha = int(210 * max(0.0, progress) ** 0.82)
            draw.line((x, 0, x, CARD_HEIGHT), fill=(*color, alpha))
    elif box.x + box.width > 0.70:
        fade_start = max(0, left - int(0.22 * CARD_WIDTH))
        span = max(1, CARD_WIDTH - fade_start)
        for x in range(fade_start, CARD_WIDTH):
            progress = (x - fade_start) / span
            alpha = int(210 * progress**0.82)
            draw.line((x, 0, x, CARD_HEIGHT), fill=(*color, alpha))
    else:
        # Fallback for centered copy zones: a vertical matte localized to its band.
        for y in range(top, bottom):
            alpha = int(198 * min(1.0, (y - top + 1) / max(1, bottom - top) * 3))
            draw.line((0, y, CARD_WIDTH, y), fill=(*color, alpha))
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

    # Blend the sharp artwork into its same-source bleed instead of outlining
    # it like a rectangular photo tile. A proportional, gently feathered mask
    # scales with the image while leaving its central detail crisp.
    feather = max(1, min(min(contained.size) // 3, max(24, min(contained.size) // 10)))
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
    contained.putalpha(edge_mask)
    canvas.alpha_composite(contained, (x, y))


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


def _contrast_ratio(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    def luminance(color: tuple[int, int, int]) -> float:
        channels = [value / 255 for value in color]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _artwork_accent(image: Image.Image | None, page: int = 1) -> tuple[int, int, int]:
    """Choose a bright accent in the artwork's hue family that stays legible on the copy panel."""
    if image is None:
        return _GRAPHIC_ACCENTS[(page - 1) % len(_GRAPHIC_ACCENTS)]
    sample = image.convert("RGB").resize((64, 64), Image.Resampling.LANCZOS).quantize(colors=12)
    palette = sample.getpalette() or []
    counts = sample.getcolors(64 * 64) or []
    candidates: list[tuple[int, tuple[int, int, int]]] = []
    for count, index in counts:
        offset = index * 3
        if offset + 2 >= len(palette):
            continue
        color = tuple(palette[offset : offset + 3])
        hue, lightness, saturation = colorsys.rgb_to_hls(*(channel / 255 for channel in color))
        if saturation >= 0.18 and lightness >= 0.12:
            # Preserve the artwork hue but lift it to a readable editorial highlight.
            accent = tuple(
                round(channel * 255)
                for channel in colorsys.hls_to_rgb(hue, 0.80, max(0.68, saturation))
            )
            if _contrast_ratio(accent, _TEXT_PANEL) >= 4.5:
                candidates.append((count, accent))
    if not candidates:
        return _ACCENT
    return max(candidates, key=lambda item: item[0])[1]


def _wrap_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    width: int,
    protected_phrases: list[str] | None = None,
) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    # Keep Korean counters ("여섯 명") and short determiner+noun hooks
    # ("두 이야기") together rather than leaving a dangling first word.
    number_words = {
        "한",
        "두",
        "세",
        "네",
        "하나",
        "둘",
        "셋",
        "넷",
        "다섯",
        "여섯",
        "일곱",
        "여덟",
        "아홉",
        "열",
        "몇",
    }
    counters = {
        "명",
        "개",
        "장",
        "편",
        "화",
        "회",
        "곡",
        "권",
        "부",
        "팀",
        "차",
        "곳",
        "마리",
        "잔",
        "벌",
        "종",
        "개교",
    }
    units: list[str] = []
    phrase_tokens = sorted(
        (phrase.split() for phrase in (protected_phrases or []) if phrase.strip()),
        key=len,
        reverse=True,
    )
    index = 0
    while index < len(words):
        matched_phrase = next(
            (
                tokens
                for tokens in phrase_tokens
                if words[index : index + len(tokens)] == tokens
                and _text_width(draw, " ".join(tokens), font) <= width
            ),
            None,
        )
        if matched_phrase:
            units.append(" ".join(matched_phrase))
            index += len(matched_phrase)
            continue
        if index + 1 < len(words) and words[index] in number_words:
            next_word = words[index + 1]
            if next_word in counters or words[index] in {"한", "두", "세", "네", "몇"}:
                units.append(f"{words[index]} {next_word}")
                index += 2
                continue
        units.append(words[index])
        index += 1
    # Greedy wrapping can leave a dangling final eojeol (e.g. "성우가 함께 / 선다").
    # Choose breaks globally so a short orphan line is avoided when a balanced
    # alternative fits. Eojeols and grouped number-counter units remain indivisible.
    count = len(units)
    infinity = float("inf")
    costs = [infinity] * (count + 1)
    paths: list[list[str] | None] = [None] * (count + 1)
    costs[count] = 0
    paths[count] = []
    for start in range(count - 1, -1, -1):
        line = ""
        for end in range(start, count):
            line = f"{line} {units[end]}".strip()
            measured = _text_width(draw, line, font)
            if measured > width:
                break
            tail = paths[end + 1]
            if tail is None:
                continue
            is_last = end + 1 == count
            slack_ratio = (width - measured) / max(width, 1)
            # A mild line-count cost discourages unnecessarily fragmented titles;
            # final lines get less raggedness cost, but not enough to permit orphans.
            line_cost = 0.10 + slack_ratio**2 * (0.32 if is_last else 1.0)
            token_count = end - start + 1
            if not is_last and token_count == 1:
                line_cost += 1.5
            if is_last and token_count == 1 and count > 1:
                line_cost += 2.0
            elif measured < width * 0.34 and count > token_count:
                line_cost += 0.75
            candidate_cost = costs[end + 1] + line_cost
            if candidate_cost < costs[start]:
                costs[start] = candidate_cost
                paths[start] = [line, *tail]

    # A single word may exceed the available width; preserve it intact so the
    # renderer's existing font-fit check can shrink or reject the card safely.
    return paths[0] or units


def _copy_box(card: CardSpec) -> tuple[int, int, int, int]:
    box = card.layout.copy_box
    return (
        int(box.x * CARD_WIDTH),
        int(box.y * CARD_HEIGHT),
        int(box.width * CARD_WIDTH),
        int(box.height * CARD_HEIGHT),
    )


def _draw_copy(
    canvas: Image.Image,
    card: CardSpec,
    package: ContentPackage,
    image_accent: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(canvas)
    x, y, width, height = _copy_box(card)
    compact = card.layout.template.startswith(("side_", "cutout_"))
    inset = 8 if card.layout.template == "split" else 4
    x += inset
    width -= inset * 2

    is_cover = card.role == "cover"
    title_font_size = (
        28 if is_cover else 15 if compact else 20 if card.layout.template != "split" else 16
    ) * RENDER_SCALE
    body_font_size = (
        9 if is_cover else 8 if compact else 11 if card.layout.template != "split" else 9
    ) * RENDER_SCALE
    eyebrow = card.eyebrow.strip() or card.role.strip()
    eyebrow_font_size = (11 if is_cover else 10) * RENDER_SCALE
    eyebrow_font = _font(eyebrow_font_size, bold=True)
    while _text_width(draw, eyebrow, eyebrow_font) > width and eyebrow_font_size > 24:
        eyebrow_font_size -= 2
        eyebrow_font = _font(eyebrow_font_size, bold=True)
    if _text_width(draw, eyebrow, eyebrow_font) > width:
        raise ValueError(f"{card.page}장 상단 라벨이 텍스트 영역을 벗어납니다.")
    title_y = y + (16 if is_cover else 14) * RENDER_SCALE
    text_gap = max(8, int(title_font_size * 0.16))

    title_fits = False
    while title_font_size >= 38:
        title_font = _font(title_font_size, bold=True)
        # Model-proposed line breaks are hints; reflow the complete phrase to
        # avoid orphaning counters, loanwords, and short Korean noun phrases.
        wrapped = _wrap_line(
            draw,
            " ".join(card.title_lines),
            title_font,
            width,
            card.protected_phrases,
        )
        title_line_height = int(title_font_size * 1.12)
        title_height = title_line_height * len(wrapped)
        remaining = y + height - title_y - title_height - text_gap - 14
        if remaining >= max(
            18 * RENDER_SCALE, len(card.body_sentences) * 11 * RENDER_SCALE
        ) and all(_text_width(draw, line, title_font) <= width for line in wrapped):
            title_fits = True
            break
        title_font_size -= 2
    if not title_fits:
        raise ValueError(f"{card.page}장 제목을 줄바꿈·축소해도 텍스트 영역에 넣을 수 없습니다.")

    body_y = title_y + title_height + text_gap
    body_lines: list[str] = []
    body_font: ImageFont.FreeTypeFont | None = None
    line_height = 0
    if card.body_sentences:
        body_font = _font(body_font_size)
        body_lines = [
            line
            for sentence in card.body_sentences
            for line in _wrap_line(draw, sentence, body_font, width, card.protected_phrases)
        ]
        line_height = int(body_font_size * 1.35)
        max_body_lines = max(1, (y + height - body_y - 16) // line_height)
        if len(body_lines) > max_body_lines or any(
            _text_width(draw, line, body_font) > width for line in body_lines
        ):
            # The copy must remain legible and complete; fail instead of silently clipping it.
            while body_font_size > 24:
                body_font_size -= 1
                body_font = _font(body_font_size)
                body_lines = [
                    line
                    for sentence in card.body_sentences
                    for line in _wrap_line(
                        draw, sentence, body_font, width, card.protected_phrases
                    )
                ]
                line_height = int(body_font_size * 1.35)
                max_body_lines = max(1, (y + height - body_y - 16) // line_height)
                if len(body_lines) <= max_body_lines and all(
                    _text_width(draw, line, body_font) <= width for line in body_lines
                ):
                    break
        if len(body_lines) > max_body_lines or any(
            _text_width(draw, line, body_font) > width for line in body_lines
        ):
            raise ValueError(f"{card.page}장 본문이 텍스트 영역에 들어가지 않습니다.")

    # Protect only the actual copy block, not the whole large copy box. The
    # matte isolates letters from bright/detailed artwork without making a
    # giant empty panel dominate the card.
    visible_widths = [_text_width(draw, eyebrow, eyebrow_font)]
    visible_widths.extend(_text_width(draw, line, title_font) for line in wrapped)
    if body_font is not None:
        visible_widths.extend(_text_width(draw, line, body_font) for line in body_lines)
    copy_width = min(width, max(visible_widths, default=width))
    copy_bottom = title_y + title_height
    if body_lines and body_font is not None:
        body_bbox = draw.textbbox((0, 0), body_lines[-1], font=body_font)
        copy_bottom = max(copy_bottom, body_y + (len(body_lines) - 1) * line_height + body_bbox[3])
    padding = 12 * RENDER_SCALE
    panel = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        (x - padding, y - 7 * RENDER_SCALE, x + copy_width + padding, copy_bottom + padding),
        radius=14 * RENDER_SCALE,
        fill=(*_TEXT_PANEL, 210),
    )
    panel = panel.filter(ImageFilter.GaussianBlur(radius=8 * RENDER_SCALE))
    canvas.alpha_composite(panel)
    draw = ImageDraw.Draw(canvas)

    draw.text((x, y), eyebrow, font=eyebrow_font, fill=(*image_accent, 255))
    for index, line in enumerate(wrapped):
        color = image_accent if index == len(wrapped) - 1 and len(wrapped) > 1 else _WHITE
        draw.text(
            (x, title_y + index * title_line_height),
            line,
            font=title_font,
            fill=(*color, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 150),
        )
    if body_font is not None:
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
    image: ImageAsset | None,
    image_bytes: bytes | None,
) -> bytes:
    """Render a text-free 1308×1632 image plate for frontend HTML copy overlays."""
    if card.visual_mode == "editorial_graphic":
        raise ValueError("Decorative placeholder graphics are not valid anime content imagery.")
    foreground = _image_from_bytes(image_bytes) if image_bytes is not None else None
    canvas = (
        _background(foreground, card.layout.background_style)
        if foreground is not None
        else None
    )
    if canvas is None:
        raise ValueError(f"{card.page}장에 표시할 이미지 또는 편집 그래픽이 없습니다.")
    box = card.layout.image_box
    pixel_box = (
        int(box.x * CARD_WIDTH),
        int(box.y * CARD_HEIGHT),
        int(box.width * CARD_WIDTH),
        int(box.height * CARD_HEIGHT),
    )
    if foreground is not None:
        if not card.layout.template.startswith("cutout_") or not _paste_cutout(
            canvas, foreground, pixel_box
        ):
            _paste_uncropped(canvas, foreground, pixel_box)

    # The PNG carries art and a directional contrast treatment only; HTML owns all copy.
    canvas.alpha_composite(_copy_scrim(card.layout))

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
        if card.visual_mode == "editorial_graphic":
            raise ValueError(f"Decorative placeholder graphics are not valid for card {card.page}.")
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
