"""이미지 원본을 자르지 않고 카드에 맞추는 결정적 레이아웃 엔진."""

from ani_menu_agent.models import CardSpec, CopyCard, ImageAsset, LayoutSpec, NormalizedBox
from ani_menu_agent.policy import POLICY


def _aspect(image: ImageAsset) -> float:
    return image.width / image.height if image.width and image.height else 1.0


def _has_dense_visible_text(image: ImageAsset) -> bool:
    """Use image-agent OCR metadata when available to distinguish posters from artwork."""
    lines = [line.strip() for line in image.visible_text if line.strip()]
    character_count = sum(len(line) for line in lines)
    return image.text_confidence >= 0.35 and (len(lines) >= 7 or character_count >= 120)


def _is_dense_portrait(image: ImageAsset, aspect: float) -> bool:
    # Very narrow tall artwork is intrinsically poster-like; OCR also catches denser
    # infographics that are not quite as extreme in aspect ratio.
    return aspect < 0.45 or (aspect < 0.90 and _has_dense_visible_text(image))


def _cover_layout(image: ImageAsset) -> LayoutSpec:
    aspect = _aspect(image)
    if aspect < 0.90:
        image_side = image.subject_hint if image.subject_hint in {"left", "right"} else "left"
        if _is_dense_portrait(image, aspect):
            # Dense posters are legible as a complete reference, not as a thumbnail hero.
            return _poster_side_layout(image_side)
        return _portrait_side_layout(image_side)
    if aspect >= 1.18:
        return _wide_layout("top")
    # 세로·정방형은 원본을 확대하지 않고 중앙 배치합니다. 제목은 이미지 아래 그라데이션에 둡니다.
    return LayoutSpec(
        template="full_bleed",
        fit_mode="contain_blur",
        background_style="blur",
        image_box=NormalizedBox(x=0.04, y=0.04, width=0.92, height=0.54),
        copy_box=NormalizedBox(x=0.05, y=0.61, width=0.90, height=0.31),
        image_scale=1.0,
        image_offset_y=0.0,
        contrast_mode="scrim_required",
        blur_background=True,
    )


def _image_led_cover_layout(image: ImageAsset) -> LayoutSpec:
    """Feature source art without placing headlines over its embedded credits."""
    aspect = _aspect(image)
    if aspect >= 1.18:
        # A landscape image is matched to a shallow top hero, avoiding a large
        # empty band above it while preserving the complete source frame.
        return LayoutSpec(
            template="full_bleed",
            fit_mode="contain_blur",
            background_style="blur",
            image_box=NormalizedBox(x=0.02, y=0.045, width=0.96, height=0.52),
            copy_box=NormalizedBox(x=0.055, y=0.55, width=0.89, height=0.39),
            image_scale=1.0,
            image_offset_y=0.0,
            contrast_mode="scrim_required",
            blur_background=True,
        )
    if aspect < 0.45:
        # Dense, very tall artwork needs the poster treatment to stay readable.
        return _poster_side_layout("left")
    return LayoutSpec(
        template="full_bleed",
        fit_mode="contain_blur",
        background_style="blur",
        image_box=NormalizedBox(x=0.02, y=0.045, width=0.96, height=0.50),
        copy_box=NormalizedBox(x=0.055, y=0.59, width=0.89, height=0.34),
        image_scale=1.0,
        image_offset_y=0.0,
        contrast_mode="scrim_required",
        blur_background=True,
    )


def _wide_layout(position: str) -> LayoutSpec:
    """Two landscape compositions; the foreground is always fully contained."""
    if position == "bottom":
        return LayoutSpec(
            template="wide_bottom",
            fit_mode="contain_blur",
            background_style="blur",
            image_box=NormalizedBox(x=0.04, y=0.41, width=0.92, height=0.54),
            copy_box=NormalizedBox(x=0.06, y=0.08, width=0.88, height=0.27),
            contrast_mode="scrim_required",
            blur_background=True,
        )
    return LayoutSpec(
        template="wide_top",
        fit_mode="contain_blur",
        background_style="blur",
        image_box=NormalizedBox(x=0.04, y=0.05, width=0.92, height=0.52),
        copy_box=NormalizedBox(x=0.05, y=0.62, width=0.90, height=0.30),
        contrast_mode="scrim_required",
        blur_background=True,
    )


def _stacked_layout(*, palette: bool = False) -> LayoutSpec:
    return LayoutSpec(
        template="stacked",
        fit_mode="contain_blur",
        background_style="palette" if palette else "blur",
        image_box=NormalizedBox(x=0.05, y=0.06, width=0.90, height=0.47),
        copy_box=NormalizedBox(x=0.07, y=0.58, width=0.86, height=0.34),
        contrast_mode="scrim_required",
        blur_background=True,
    )


def _side_layout(image_side: str, *, cutout: bool = False) -> LayoutSpec:
    image_left = image_side == "left"
    image_x = 0.04 if image_left else 0.53
    copy_x = 0.51 if image_left else 0.04
    template = f"{'cutout' if cutout else 'side'}_{image_side}"
    return LayoutSpec(
        template=template,
        fit_mode="contain_blur",
        background_style="palette" if cutout else "blur",
        image_box=NormalizedBox(x=image_x, y=0.12, width=0.43, height=0.76),
        copy_box=NormalizedBox(x=copy_x, y=0.21, width=0.45, height=0.56),
        image_scale=1.0,
        image_offset_y=0.0,
        contrast_mode="scrim_required",
        blur_background=True,
    )


def _poster_side_layout(image_side: str) -> LayoutSpec:
    """Give narrow, information-dense posters as much height as possible without cropping."""
    image_left = image_side == "left"
    image_x = 0.03 if image_left else 0.51
    copy_x = 0.52 if image_left else 0.04
    copy_width = 0.44 if image_left else 0.42
    return LayoutSpec(
        template=f"poster_{image_side}",
        fit_mode="contain_blur",
        background_style="palette",
        image_box=NormalizedBox(x=image_x, y=0.03, width=0.46, height=0.94),
        copy_box=NormalizedBox(x=copy_x, y=0.19, width=copy_width, height=0.62),
        image_scale=1.0,
        image_offset_y=0.0,
        contrast_mode="scrim_required",
        blur_background=True,
    )


def _portrait_side_layout(image_side: str) -> LayoutSpec:
    """Enlarge normal portrait artwork while preserving its whole uncropped frame."""
    image_left = image_side == "left"
    image_x = 0.03 if image_left else 0.39
    copy_x = 0.63 if image_left else 0.03
    return LayoutSpec(
        template=f"portrait_{image_side}",
        fit_mode="contain_blur",
        background_style="palette" if image_side == "left" else "blur",
        image_box=NormalizedBox(x=image_x, y=0.05, width=0.58, height=0.90),
        copy_box=NormalizedBox(x=copy_x, y=0.20, width=0.34, height=0.60),
        image_scale=1.0,
        image_offset_y=0.0,
        contrast_mode="scrim_required",
        blur_background=True,
    )


def _portrait_side_layout_for_resolution(image: ImageAsset, image_side: str) -> LayoutSpec:
    """Use a compact portrait placement when the source cannot support a hero crop."""
    large_layout = _portrait_side_layout(image_side)
    target_width = (
        POLICY.logical_card_width
        * large_layout.image_box.width
        * POLICY.image_quality_target_dpr
    )
    target_height = (
        POLICY.logical_card_height
        * large_layout.image_box.height
        * POLICY.image_quality_target_dpr
    )
    required_scale = min(target_width / image.width, target_height / image.height)
    if required_scale <= POLICY.max_image_upscale:
        return large_layout
    # A narrower, still tall image area keeps a 500×750 official asset sharp at 4× output.
    return _side_layout(image_side)


def _detail_layout(image: ImageAsset, page: int, copy: CopyCard) -> LayoutSpec:
    aspect = _aspect(image)
    copy_length = len("".join(copy.title_lines)) + sum(map(len, copy.body_sentences))
    compact_copy = copy_length <= 110
    image_side = (
        image.subject_hint
        if image.subject_hint in {"left", "right"}
        else ("left" if page % 2 == 0 else "right")
    )
    if image.has_alpha and image.subject_box and compact_copy:
        return _side_layout(image_side, cutout=True)
    if compact_copy and _is_dense_portrait(image, aspect):
        return _poster_side_layout(image_side)
    if compact_copy and aspect < 0.90:
        return _portrait_side_layout_for_resolution(image, image_side)
    if compact_copy and aspect < 1.18:
        return _side_layout(image_side)
    if aspect >= 1.18 and compact_copy:
        return _wide_layout("top" if page % 2 == 0 else "bottom")
    return _stacked_layout(palette=page % 3 == 0)


def _ending_layout(image: ImageAsset, page: int, copy: CopyCard) -> LayoutSpec:
    aspect = _aspect(image)
    if _is_dense_portrait(image, aspect):
        return _poster_side_layout("right" if page % 2 else "left")
    if aspect < 0.90:
        return _portrait_side_layout_for_resolution(
            image, "right" if page % 2 else "left"
        )
    if aspect >= 1.18:
        return _wide_layout("bottom")
    return _stacked_layout(palette=True)


def _editorial_graphic_layout(page: int) -> LayoutSpec:
    """Reserve a calm, full-card composition for original text-led visual storytelling."""
    return LayoutSpec(
        template="editorial_graphic",
        fit_mode="contain",
        background_style="palette",
        image_box=NormalizedBox(x=0.08, y=0.12, width=0.84, height=0.30),
        copy_box=NormalizedBox(x=0.08, y=0.44, width=0.84, height=0.43),
        contrast_mode="light_on_dark",
        blur_background=False,
    )


def build_cards(copy_cards: list[CopyCard], images: list[ImageAsset]) -> list[CardSpec]:
    image_by_id = {image.id: image for image in images}
    cards: list[CardSpec] = []
    last_page = max(card.page for card in copy_cards)
    for copy in sorted(copy_cards, key=lambda item: item.page):
        if copy.visual_mode == "editorial_graphic":
            cards.append(
                CardSpec(
                    **copy.model_dump(),
                    layout=_editorial_graphic_layout(copy.page),
                )
            )
            continue
        image = image_by_id.get(copy.image_id)
        if image is None:
            # 품질 게이트가 IMAGE_MISSING으로 잡을 수 있도록 안전한 더미 치수를 사용합니다.
            image = ImageAsset(
                id=copy.image_id,
                url="https://invalid.example/missing.jpg",
                source_url="https://invalid.example/",
            )
        layout = (
            _image_led_cover_layout(image)
            if copy.page == 1
            else _ending_layout(image, copy.page, copy)
            if copy.page == last_page
            else _detail_layout(image, copy.page, copy)
        )
        cards.append(CardSpec(**copy.model_dump(), layout=layout))
    return cards
