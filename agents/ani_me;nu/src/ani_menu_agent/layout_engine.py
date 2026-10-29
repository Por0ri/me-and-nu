"""이미지 원본을 자르지 않고 카드에 맞추는 결정적 레이아웃 엔진."""

from ani_menu_agent.models import CardSpec, CopyCard, ImageAsset, LayoutSpec, NormalizedBox


def _aspect(image: ImageAsset) -> float:
    return image.width / image.height if image.width and image.height else 1.0


def _immersive_layout(*, cover: bool = False) -> LayoutSpec:
    template = "cover_immersive" if cover else "immersive_full_bleed"
    return LayoutSpec(
        template=template,
        fit_mode="contain_blur",
        background_style="blur",
        image_box=NormalizedBox(x=0.015, y=0.015, width=0.97, height=0.97),
        copy_box=NormalizedBox(x=0.06, y=0.66, width=0.88, height=0.28),
        image_scale=1.0,
        image_offset_y=0.0,
        contrast_mode="scrim_required",
        blur_background=True,
    )


def _wide_layout() -> LayoutSpec:
    """Place landscape artwork above the HTML copy band."""
    return LayoutSpec(
        template="wide_image_top",
        fit_mode="contain_blur",
        background_style="blur",
        image_box=NormalizedBox(x=0.025, y=0.025, width=0.95, height=0.57),
        copy_box=NormalizedBox(x=0.06, y=0.66, width=0.88, height=0.28),
        contrast_mode="scrim_required",
        blur_background=True,
    )


def _cover_layout(image: ImageAsset) -> LayoutSpec:
    return _wide_layout() if _aspect(image) >= 1.18 else _immersive_layout(cover=True)


def _image_led_cover_layout(image: ImageAsset) -> LayoutSpec:
    return _cover_layout(image)


def _detail_layout(image: ImageAsset, page: int, copy: CopyCard) -> LayoutSpec:
    if _aspect(image) >= 1.18:
        return _wide_layout()
    return _immersive_layout()


def _ending_layout(image: ImageAsset, page: int, copy: CopyCard) -> LayoutSpec:
    if _aspect(image) >= 1.18:
        return _wide_layout()
    return _immersive_layout()


def _editorial_graphic_layout(page: int) -> LayoutSpec:
    """Legacy entry point retained only to fail closed; anime cards require source art."""
    raise ValueError(f"Card {page} cannot use a decorative placeholder instead of anime art.")


def _reserve_html_copy_space(
    layout: LayoutSpec,
    image: ImageAsset | None,
    page: int,
    *,
    cover: bool = False,
    copy_length: int = 0,
    preferred_background_style: str | None = None,
) -> LayoutSpec:
    """Build a full-width vertical composition with a source-matched image bleed."""
    copy_height = 0.34 if copy_length > 160 else 0.28
    bottom_copy = NormalizedBox(
        x=0.06,
        y=0.94 - copy_height,
        width=0.88,
        height=copy_height,
    )
    if image is None:
        safe_box = bottom_copy
        return layout.model_copy(
            update={
                "image_box": NormalizedBox(x=0.02, y=0.02, width=0.96, height=0.96),
                "copy_box": safe_box,
                "text_overlay_box": safe_box,
                "template": "full_bleed_bottom_copy",
                "background_style": "gradient",
                "blur_background": False,
            }
        )

    aspect = _aspect(image)
    use_landscape_stack = aspect >= 1.18
    if use_landscape_stack:
        # Wide artwork occupies a broad upper band; HTML copy stays below it.
        image_box = NormalizedBox(x=0.025, y=0.025, width=0.95, height=0.57)
        copy_box = bottom_copy
        template = "cover_wide_top" if cover else "wide_image_top"
    else:
        # Portrait and square artwork fills almost the whole card. The renderer
        # feathers its edges into a blurred copy of the same source image.
        image_box = NormalizedBox(x=0.015, y=0.015, width=0.97, height=0.97)
        copy_box = bottom_copy
        template = "cover_immersive" if cover else "immersive_full_bleed"

    background_style = (
        preferred_background_style
        if preferred_background_style in {"blur", "palette", "gradient"}
        else layout.background_style
    )
    return layout.model_copy(
        update={
            "image_box": image_box,
            "copy_box": copy_box,
            "text_overlay_box": copy_box,
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
            preferred_background_style=direction.get("background_style"),
        )
        cards.append(CardSpec(**copy.model_dump(), layout=layout))
    return cards


def reflow_for_composition(
    cards: list[CardSpec], images: list[ImageAsset], pages: set[int]
) -> list[CardSpec]:
    """Rebuild composition using the same image-aware layout rules as initial placement."""
    revised: list[CardSpec] = []
    image_by_id = {image.id: image for image in images}
    for card in cards:
        if card.page not in pages:
            revised.append(card)
            continue
        copy = CopyCard.model_validate(card.model_dump(exclude={"layout"}))
        rebuilt = build_cards(
            [copy],
            [image_by_id[card.image_id]] if card.image_id in image_by_id else [],
        )
        revised_layout = rebuilt[0].layout
        if card.page in pages:
            revised_layout = revised_layout.model_copy(update={"fit_mode": "contain"})
        revised.append(card.model_copy(update={"layout": revised_layout}))
    return revised
