"""이미지 원본을 자르지 않고 카드에 맞추는 결정적 레이아웃 엔진."""

import hashlib

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


def _cover_layout_for_resolution(image: ImageAsset, hero: LayoutSpec) -> LayoutSpec:
    """Shrink low-resolution cover art before it becomes visibly soft."""
    if image.width <= 0 or image.height <= 0:
        return hero
    target_width = (
        POLICY.logical_card_width * hero.image_box.width * POLICY.image_quality_target_dpr
    )
    target_height = (
        POLICY.logical_card_height * hero.image_box.height * POLICY.image_quality_target_dpr
    )
    if min(target_width / image.width, target_height / image.height) <= POLICY.max_image_upscale:
        return hero
    side = _side_layout("left")
    target_width = (
        POLICY.logical_card_width * side.image_box.width * POLICY.image_quality_target_dpr
    )
    target_height = (
        POLICY.logical_card_height * side.image_box.height * POLICY.image_quality_target_dpr
    )
    if min(target_width / image.width, target_height / image.height) <= POLICY.max_image_upscale:
        return side
    return LayoutSpec(
        template="cover_inset_left",
        fit_mode="contain_blur",
        background_style="palette",
        image_box=NormalizedBox(x=0.04, y=0.26, width=0.24, height=0.48),
        copy_box=NormalizedBox(x=0.32, y=0.14, width=0.64, height=0.72),
        contrast_mode="scrim_required",
        blur_background=True,
    )


def _image_led_cover_layout(image: ImageAsset) -> LayoutSpec:
    """Feature source art without placing headlines over its embedded credits."""
    aspect = _aspect(image)
    if aspect >= 1.18:
        # A landscape image is matched to a shallow top hero, avoiding a large
        # empty band above it while preserving the complete source frame.
        hero = LayoutSpec(
            template="full_bleed",
            fit_mode="contain_blur",
            background_style="blur",
            image_box=NormalizedBox(x=0.03, y=0.045, width=0.94, height=0.52),
            copy_box=NormalizedBox(x=0.055, y=0.55, width=0.89, height=0.39),
            image_scale=1.0,
            image_offset_y=0.0,
            contrast_mode="scrim_required",
            blur_background=True,
        )
        target_width = (
            POLICY.logical_card_width * hero.image_box.width * POLICY.image_quality_target_dpr
        )
        target_height = (
            POLICY.logical_card_height * hero.image_box.height * POLICY.image_quality_target_dpr
        )
        if (
            min(target_width / image.width, target_height / image.height)
            <= POLICY.max_image_upscale
        ):
            return hero
        # Keep small landscape key art crisp instead of stretching it across a
        # full-width hero. A balanced side-by-side cover still gives the image
        # and headline enough visual weight at the 1308x1632 export size.
        side = _side_layout("left")
        target_width = (
            POLICY.logical_card_width * side.image_box.width * POLICY.image_quality_target_dpr
        )
        target_height = (
            POLICY.logical_card_height * side.image_box.height * POLICY.image_quality_target_dpr
        )
        if (
            min(target_width / image.width, target_height / image.height)
            <= POLICY.max_image_upscale
        ):
            return side
        return LayoutSpec(
            template="cover_inset_left",
            fit_mode="contain_blur",
            background_style="palette",
            image_box=NormalizedBox(x=0.04, y=0.26, width=0.24, height=0.48),
            copy_box=NormalizedBox(x=0.32, y=0.14, width=0.64, height=0.72),
            contrast_mode="scrim_required",
            blur_background=True,
        )
    if aspect < 0.45:
        # Dense, very tall artwork needs the poster treatment to stay readable.
        return _cover_layout_for_resolution(image, _poster_side_layout("left"))
    hero = LayoutSpec(
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
    return _cover_layout_for_resolution(image, hero)


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


def _wide_layout_for_resolution(image: ImageAsset, position: str) -> LayoutSpec:
    """Reduce a small landscape image's placement rather than enlarging pixels."""
    wide = _wide_layout(position)
    target_width = (
        POLICY.logical_card_width * wide.image_box.width * POLICY.image_quality_target_dpr
    )
    target_height = (
        POLICY.logical_card_height * wide.image_box.height * POLICY.image_quality_target_dpr
    )
    if min(target_width / image.width, target_height / image.height) <= POLICY.max_image_upscale:
        return wide
    # A side-by-side box can make long Korean copy overflow. Keep the image
    # modestly sized above the copy and reserve most of the card for readable text.
    return LayoutSpec(
        template="wide_inset_top",
        fit_mode="contain_blur",
        background_style="blur",
        image_box=NormalizedBox(x=0.26, y=0.05, width=0.48, height=0.22),
        copy_box=NormalizedBox(x=0.07, y=0.31, width=0.86, height=0.62),
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
        POLICY.logical_card_width * large_layout.image_box.width * POLICY.image_quality_target_dpr
    )
    target_height = (
        POLICY.logical_card_height * large_layout.image_box.height * POLICY.image_quality_target_dpr
    )
    required_scale = min(target_width / image.width, target_height / image.height)
    if required_scale <= POLICY.max_image_upscale:
        return large_layout
    # A narrower, still tall image area keeps a 500×750 official asset sharp at 4× output.
    side_layout = _side_layout(image_side)
    target_width = (
        POLICY.logical_card_width * side_layout.image_box.width * POLICY.image_quality_target_dpr
    )
    target_height = (
        POLICY.logical_card_height * side_layout.image_box.height * POLICY.image_quality_target_dpr
    )
    required_scale = min(target_width / image.width, target_height / image.height)
    if required_scale <= POLICY.max_image_upscale:
        return side_layout
    image_left = image_side == "left"
    return LayoutSpec(
        template=f"portrait_inset_{image_side}",
        fit_mode="contain_blur",
        background_style="blur",
        image_box=NormalizedBox(
            x=0.04 if image_left else 0.64,
            y=0.20,
            width=0.32,
            height=0.52,
        ),
        copy_box=NormalizedBox(
            x=0.40 if image_left else 0.04,
            y=0.16,
            width=0.55,
            height=0.68,
        ),
        contrast_mode="scrim_required",
        blur_background=True,
    )


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
        cutout_layout = _side_layout(image_side, cutout=True)
        cutout_target_width = (
            POLICY.logical_card_width
            * cutout_layout.image_box.width
            * POLICY.image_quality_target_dpr
        )
        cutout_target_height = (
            POLICY.logical_card_height
            * cutout_layout.image_box.height
            * POLICY.image_quality_target_dpr
        )
        cutout_scale = min(
            cutout_target_width / image.width,
            cutout_target_height / image.height,
        )
        if cutout_scale <= POLICY.max_image_upscale:
            return cutout_layout
    if compact_copy and _is_dense_portrait(image, aspect):
        return _poster_side_layout(image_side)
    if compact_copy and aspect < 0.90:
        return _portrait_side_layout_for_resolution(image, image_side)
    if compact_copy and aspect < 1.18:
        return _side_layout(image_side)
    if aspect >= 1.18 and compact_copy:
        return _wide_layout_for_resolution(image, "top" if page % 2 == 0 else "bottom")
    return _stacked_layout(palette=page % 3 == 0)


def _ending_layout(image: ImageAsset, page: int, copy: CopyCard) -> LayoutSpec:
    aspect = _aspect(image)
    if _is_dense_portrait(image, aspect):
        return _poster_side_layout("right" if page % 2 else "left")
    if aspect < 0.90:
        return _portrait_side_layout_for_resolution(image, "right" if page % 2 else "left")
    if aspect >= 1.18:
        return _wide_layout_for_resolution(image, "bottom")
    return _stacked_layout(palette=True)


def _editorial_graphic_layout(page: int) -> LayoutSpec:
    """Legacy entry point retained only to fail closed; anime cards require source art."""
    raise ValueError(f"Card {page} cannot use a decorative placeholder instead of anime art.")


def _integrated_artwork_box(image: ImageAsset) -> NormalizedBox:
    """Place full source art into a portrait canvas while preserving lower-third copy space.

    Portrait/near-square artwork can bleed behind the lower gradient for an integrated
    editorial look. Wide artwork sits in the upper area and very tall posters stay
    narrower so their important content is not reduced to an unreadable strip.
    """
    aspect = _aspect(image)
    if image.has_alpha and image.subject_box is not None:
        return NormalizedBox(x=0.05, y=0.015, width=0.90, height=0.66)
    if _has_dense_visible_text(image):
        return NormalizedBox(x=0.05, y=0.02, width=0.90, height=0.62)
    if aspect >= 1.25:
        return NormalizedBox(x=0.025, y=0.015, width=0.95, height=0.62)
    if aspect < 0.62:
        return NormalizedBox(x=0.16, y=0.015, width=0.68, height=0.64)
    return NormalizedBox(x=0.0, y=0.0, width=1.0, height=1.0)


def _reserve_html_copy_space(
    layout: LayoutSpec,
    image: ImageAsset | None,
    page: int,
    *,
    cover: bool = False,
    copy_length: int = 0,
    preferred_text_position: str | None = None,
    preferred_image_emphasis: str | None = None,
    preferred_background_style: str | None = None,
) -> LayoutSpec:
    """Assign an explicit, page-specific HTML region and a complementary art area."""
    if image is None:
        safe_box = NormalizedBox(x=0.07, y=0.65, width=0.86, height=0.30)
        return layout.model_copy(
            update={
                "image_box": NormalizedBox(x=0.04, y=0.03, width=0.92, height=0.62),
                "copy_box": safe_box,
                "text_overlay_box": safe_box,
                "background_style": "gradient",
                "blur_background": False,
            }
        )

    seed = f"{image.id}|{image.fingerprint or image.url or ''}".encode()
    treatment = (hashlib.sha256(seed).digest()[0] + page) % 4
    aspect = _aspect(image)
    dense = _has_dense_visible_text(image)

    if cover:
        # Keep the lead card's familiar editorial hierarchy: image first, HTML below.
        artwork_box = NormalizedBox(x=0.035, y=0.025, width=0.93, height=0.68)
        safe_box = NormalizedBox(x=0.06, y=0.68, width=0.88, height=0.28)
        template = ("cover_blend", "cover_immersive", "cover_soft_frame", "cover_hero")[treatment]
        if image.width > 0 and image.height > 0 and not image.has_alpha:
            target_width = (
                POLICY.logical_card_width
                * artwork_box.width
                * POLICY.image_quality_target_dpr
            )
            target_height = (
                POLICY.logical_card_height
                * artwork_box.height
                * POLICY.image_quality_target_dpr
            )
            upscale = min(target_width / image.width, target_height / image.height)
            if upscale > POLICY.max_image_upscale:
                width = min(
                    0.92,
                    image.width
                    * POLICY.max_low_res_spotlight_upscale
                    / (POLICY.logical_card_width * POLICY.image_quality_target_dpr),
                )
                height = min(
                    0.60,
                    image.height
                    * POLICY.max_low_res_spotlight_upscale
                    / (POLICY.logical_card_height * POLICY.image_quality_target_dpr),
                )
                artwork_box = NormalizedBox(
                    x=(1.0 - width) / 2,
                    y=max(0.035, safe_box.y - height - POLICY.max_cover_vertical_gap),
                    width=width,
                    height=height,
                )
                template = "low_res_spotlight"
        return layout.model_copy(update={
            "image_box": artwork_box, "copy_box": safe_box, "text_overlay_box": safe_box,
            "template": template, "fit_mode": "contain_blur",
            "background_style": (
                preferred_background_style
                if preferred_background_style in {"blur", "palette", "gradient"}
                else "blur"
            ),
            "image_scale": 1.0, "image_offset_y": 0.0, "blur_background": True,
        })

    # Long copy and text-heavy source art get the wider horizontal reading lane.
    # Otherwise, vary side and vertical compositions while keeping the two boxes
    # disjoint; the frontend can place its HTML precisely from text_region.
    requested_position = (
        preferred_text_position
        if preferred_text_position in {"bottom", "left", "right"}
        else None
    )
    dominant_image = preferred_image_emphasis == "dominant"
    if dense or aspect >= 1.22 or copy_length > 160:
        mode = "copy_bottom"
    elif requested_position:
        mode = f"copy_{requested_position}"
    elif aspect < 0.72:
        mode = (
            "copy_left"
            if treatment % 3 == 0
            else "copy_right"
            if treatment % 3 == 1
            else "copy_bottom"
        )
    else:
        mode = ("copy_left", "copy_right", "copy_bottom", "copy_bottom")[treatment]

    if mode == "copy_left":
        artwork_box = NormalizedBox(
            x=0.52 if dominant_image else 0.545,
            y=0.05 if dominant_image else 0.08,
            width=0.45 if dominant_image else 0.42,
            height=0.90 if dominant_image else 0.84,
        )
        safe_box = NormalizedBox(
            x=0.035 if dominant_image else 0.04,
            y=0.16,
            width=0.44 if dominant_image else 0.43,
            height=0.68,
        )
    elif mode == "copy_right":
        artwork_box = NormalizedBox(
            x=0.025 if dominant_image else 0.035,
            y=0.05 if dominant_image else 0.08,
            width=0.45 if dominant_image else 0.42,
            height=0.90 if dominant_image else 0.84,
        )
        safe_box = NormalizedBox(
            x=0.52 if dominant_image else 0.53,
            y=0.16,
            width=0.44 if dominant_image else 0.43,
            height=0.68,
        )
    else:
        artwork_box = (
            NormalizedBox(x=0.025, y=0.035, width=0.95, height=0.58)
            if dominant_image
            else NormalizedBox(x=0.045, y=0.045, width=0.91, height=0.55)
        )
        safe_box = NormalizedBox(x=0.07, y=0.65, width=0.86, height=0.30)

    template = f"editorial_{mode}_{'dense' if dense else 'art'}"
    if image.width > 0 and image.height > 0 and not image.has_alpha:
        target_width = (
            POLICY.logical_card_width * artwork_box.width * POLICY.image_quality_target_dpr
        )
        target_height = (
            POLICY.logical_card_height * artwork_box.height * POLICY.image_quality_target_dpr
        )
        if min(target_width / image.width, target_height / image.height) > POLICY.max_image_upscale:
            max_width = min(
                artwork_box.width,
                image.width
                * POLICY.max_low_res_spotlight_upscale
                / (POLICY.logical_card_width * POLICY.image_quality_target_dpr),
            )
            max_height = min(
                artwork_box.height,
                image.height
                * POLICY.max_low_res_spotlight_upscale
                / (POLICY.logical_card_height * POLICY.image_quality_target_dpr),
            )
            normalized_aspect = (
                aspect * POLICY.logical_card_height / POLICY.logical_card_width
            )
            width = min(max_width, max_height * normalized_aspect)
            height = width / normalized_aspect
            artwork_box = NormalizedBox(
                x=artwork_box.x + (artwork_box.width - width) / 2,
                y=artwork_box.y + (artwork_box.height - height) / 2,
                width=width,
                height=height,
            )
            template = "low_res_spotlight"
    background_style = (
        preferred_background_style
        if preferred_background_style in {"blur", "palette", "gradient"}
        else ("blur", "palette", "gradient", "blur")[treatment]
    )
    return layout.model_copy(
        update={
            "image_box": artwork_box,
            "copy_box": safe_box,
            "text_overlay_box": safe_box,
            "template": template,
            "fit_mode": "contain_blur",
            "background_style": background_style,
            "image_scale": 1.0,
            "image_offset_y": 0.0,
            "blur_background": True,
        }
    )


def build_cards(
    copy_cards: list[CopyCard],
    images: list[ImageAsset],
    *,
    art_directions: dict[int, dict[str, str]] | None = None,
) -> list[CardSpec]:
    image_by_id = {image.id: image for image in images}
    cards: list[CardSpec] = []
    last_page = max(card.page for card in copy_cards)
    for copy in sorted(copy_cards, key=lambda item: item.page):
        if copy.visual_mode == "editorial_graphic":
            raise ValueError(
                f"Card {copy.page} cannot use a decorative placeholder instead of anime art."
            )
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
        copy_length = len("".join(copy.title_lines)) + sum(map(len, copy.body_sentences))
        direction = (art_directions or {}).get(copy.page, {})
        layout = _reserve_html_copy_space(
            layout,
            image,
            copy.page,
            cover=copy.page == 1,
            copy_length=copy_length,
            preferred_text_position=direction.get("copy_position"),
            preferred_image_emphasis=direction.get("image_emphasis"),
            preferred_background_style=direction.get("background_style"),
        )
        cards.append(CardSpec(**copy.model_dump(), layout=layout))
    return cards


def reflow_for_composition(
    cards: list[CardSpec], images: list[ImageAsset], pages: set[int]
) -> list[CardSpec]:
    """Reflow only the image in its existing editorial slot after composition feedback."""
    image_by_id = {image.id: image for image in images}
    revised: list[CardSpec] = []
    for card in cards:
        image = image_by_id.get(card.image_id)
        if card.page not in pages or image is None or image.width <= 0 or image.height <= 0:
            revised.append(card)
            continue
        max_upscale = POLICY.max_low_res_spotlight_upscale
        slot = card.layout.image_box
        max_width = min(
            slot.width,
            image.width
            * max_upscale
            / (POLICY.logical_card_width * POLICY.image_quality_target_dpr),
        )
        max_height = min(
            slot.height,
            image.height
            * max_upscale
            / (POLICY.logical_card_height * POLICY.image_quality_target_dpr),
        )
        aspect = _aspect(image)
        width = min(max_width, max_height * aspect)
        height = width / aspect
        artwork_height = max(0.08, min(slot.height, height))
        artwork_width = max(0.08, min(slot.width, width))
        layout = card.layout.model_copy(
            update={
                "fit_mode": "contain",
                "image_box": NormalizedBox(
                    x=slot.x + (slot.width - artwork_width) / 2,
                    y=slot.y + (slot.height - artwork_height) / 2,
                    width=artwork_width,
                    height=artwork_height,
                ),
                "image_scale": 1.0,
                "image_offset_y": 0.0,
            }
        )
        revised.append(card.model_copy(update={"layout": layout}))
    return revised
