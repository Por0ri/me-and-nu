"""Distinct, production-usable source image selection for carousel cards."""

from __future__ import annotations

from ani_menu_agent.models import ContentPackage, CopyCard, ImageAsset, SourceEvidence
from ani_menu_agent.policy import POLICY

MIN_SOURCE_EDGE = 320
MIN_SOURCE_PIXELS = 80_000


def image_visual_key(image: ImageAsset) -> str:
    """Treat duplicate URLs/files as one visual, even when the scraper gave new IDs."""
    return image.fingerprint or image.storage_key or image.id


def distinct_editorial_images(
    images: list[ImageAsset],
    sources: list[SourceEvidence],
) -> list[ImageAsset]:
    """Return unique official images that can actually support a readable card."""
    result: list[ImageAsset] = []
    seen: set[str] = set()
    for image in images:
        if (
            not image.official
            or image.used_in_previous_content
            or image.download_status not in {"downloaded", "available"}
            or image.width < 1
            or image.height < 1
            or max(image.width, image.height) < MIN_SOURCE_EDGE
            or image.width * image.height < MIN_SOURCE_PIXELS
            or not image.eligible_for_editorial_use(
                sources,
                allow_risk_accepted=POLICY.allow_public_official_image_risk,
            )
        ):
            continue
        key = image_visual_key(image)
        if key in seen:
            continue
        seen.add(key)
        result.append(image)
    return result


def assign_distinct_images(
    cards: list[CopyCard],
    package: ContentPackage,
) -> tuple[list[CopyCard], bool]:
    """Keep model-chosen images where possible, fill repeats with distinct relevant assets."""
    candidates = distinct_editorial_images(package.images, package.sources)
    if len(candidates) < len(cards):
        return cards, False

    by_id = {image.id: image for image in candidates}
    used: set[str] = set()
    assigned: list[CopyCard] = []
    for card in sorted(cards, key=lambda item: item.page):
        preferred = by_id.get(card.image_id)
        if preferred and image_visual_key(preferred) not in used:
            selected = preferred
        else:
            fact_ids = set(card.fact_ids)

            def score(
                image: ImageAsset,
                fact_ids: set[str] = fact_ids,
                preferred_id: str = card.image_id,
            ) -> tuple[int, int, int, int]:
                return (
                    len(fact_ids.intersection(image.related_fact_ids)),
                    int(image.visual_match == "exact"),
                    int(image.visual_match in {"exact", "strong"}),
                    int(image.id == preferred_id),
                )

            available = [image for image in candidates if image_visual_key(image) not in used]
            selected = max(available, key=score)
        used.add(image_visual_key(selected))
        explanation = card.image_explanation.strip()
        if selected.id != card.image_id and selected.korean_explanation:
            explanation = selected.korean_explanation
        assigned.append(
            card.model_copy(
                update={
                    "image_id": selected.id,
                    "image_explanation": explanation,
                    "image_reuse_mode": "unique",
                }
            )
        )
    return assigned, True


def image_coverage_issue(
    package: ContentPackage,
    card_count: int | None = None,
) -> dict[str, str] | None:
    """Return a crawler handoff issue instead of making a deck from repeated art."""
    available = len(distinct_editorial_images(package.images, package.sources))
    required = (
        card_count if card_count is not None else POLICY.minimum_distinct_images_before_generation
    )
    if available >= required:
        return None
    return {
        "code": "IMAGE_COVERAGE_INSUFFICIENT",
        "message": (
            f"캐러셀에 쓸 서로 다른 공식 이미지가 {available}장뿐입니다. "
            f"최소 {required}장의 선명한 이미지를 더 수집한 뒤 생성해야 합니다."
        ),
        "owner": "image_curator",
    }
