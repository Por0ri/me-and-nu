"""Distinct, production-usable source image selection for carousel cards."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ani_menu_agent.models import ContentPackage, CopyCard, ImageAsset, SourceEvidence
from ani_menu_agent.policy import POLICY

MIN_SOURCE_EDGE = 320
MIN_SOURCE_PIXELS = 80_000
MIN_ALPHA_COVERAGE = 0.10
MIN_EDITORIAL_ASPECT_RATIO = 0.42
MAX_EDITORIAL_ASPECT_RATIO = 3.2


def _normalized_source_url(url: str) -> str:
    """Collapse known renditions without erasing API parameters that identify files."""
    parts = urlsplit(url)
    path = re.sub(r"-\d{2,4}x\d{2,4}(?=\.[^.]+$)", "", parts.path)
    path = re.sub(r"/w\d+/?$", "", path, flags=re.IGNORECASE)
    path = re.sub(r"_(?:on|off)(?=\.[^.]+$)", "", path, flags=re.IGNORECASE)
    rendition_params = {
        "auto",
        "fit",
        "format",
        "h",
        "height",
        "q",
        "quality",
        "w",
        "width",
    }
    tracking_params = {"fbclid", "gclid", "ref", "source"}
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in rendition_params | tracking_params
            and not key.lower().startswith("utm_")
        )
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def _source_page_key(url: str) -> tuple[str, str]:
    """Canonical identity of the page that actually hosted an image."""
    parts = urlsplit(url)
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    # Official sites sometimes serve the same article under a locale-prefixed
    # route (e.g. /news/123 and /de/news/123). Keep the article slug strict while
    # normalizing only this known localization layer.
    path = re.sub(r"^/(?:en|ja|de|fr|es|it|ko|zh)/(?=news/)", "/", path, flags=re.I)
    return parts.netloc.lower(), path


def image_belongs_to_package_sources(
    image: ImageAsset,
    sources: list[SourceEvidence],
) -> bool:
    """Do not borrow a same-franchise image from an unrelated page on the same site."""
    image_page = _source_page_key(str(image.source_url))
    return any(_source_page_key(str(source.url)) == image_page for source in sources)


def image_visual_key(image: ImageAsset) -> str:
    """Treat duplicate URLs/files as one visual, even when the scraper gave new IDs."""
    if image.url:
        return _normalized_source_url(str(image.url))
    return image.fingerprint or image.storage_key or image.id


def image_visual_keys(image: ImageAsset) -> set[str]:
    """Return URL and byte-identity keys so CDN aliases cannot bypass reuse checks."""
    keys = {image_visual_key(image)}
    if image.fingerprint:
        keys.add(f"sha256:{image.fingerprint.lower()}")
    if image.storage_key:
        keys.add(f"storage:{image.storage_key.lower()}")
    return keys


def is_editorially_composable(image: ImageAsset) -> bool:
    """Reject decorative labels and banner strips that collapse in a portrait card."""
    ratio = image.width / image.height if image.height else 0
    path = urlsplit(str(image.url or "")).path.lower()
    filename = path.rsplit("/", 1)[-1]
    return (
        MIN_EDITORIAL_ASPECT_RATIO <= ratio <= MAX_EDITORIAL_ASPECT_RATIO
        and "/deco/" not in path
        and not filename.startswith(("ttl_", "title_"))
        and not filename.startswith("shot-")
        and len(image.source_description.strip()) < 240
    )


def _fits_card_resolution(image: ImageAsset, card: CopyCard) -> bool:
    """Reject small assets for large placements; blur cannot make an upscaled hero sharp."""
    if image.width < 1 or image.height < 1:
        return False
    from ani_menu_agent.layout_engine import build_cards

    layout_card = card.model_copy(update={"image_id": image.id, "visual_mode": "source_image"})
    layout = build_cards([layout_card], [image])[0].layout
    target_width = (
        POLICY.logical_card_width * layout.image_box.width * POLICY.image_quality_target_dpr
    )
    target_height = (
        POLICY.logical_card_height * layout.image_box.height * POLICY.image_quality_target_dpr
    )
    upscale = min(target_width / image.width, target_height / image.height)
    return upscale <= POLICY.max_image_upscale


def distinct_editorial_images(
    images: list[ImageAsset],
    sources: list[SourceEvidence],
) -> list[ImageAsset]:
    """Return unique official images that can actually support a readable card."""
    result: list[ImageAsset] = []
    seen: set[str] = set()
    # Crawl retries may leave both thumbnail and full-size renditions in storage.
    # Consider the largest rendition first so URL-level deduplication keeps the
    # usable source rather than discarding it after seeing a low-res duplicate.
    for image in sorted(images, key=lambda item: item.width * item.height, reverse=True):
        if (
            not image.official
            or image.used_in_previous_content
            or image.download_status not in {"downloaded", "available"}
            or image.width < 1
            or image.height < 1
            or max(image.width, image.height) < MIN_SOURCE_EDGE
            or image.width * image.height < MIN_SOURCE_PIXELS
            or not is_editorially_composable(image)
            or not image_belongs_to_package_sources(image, sources)
            or (
                image.has_alpha
                and (image.alpha_coverage is None or image.alpha_coverage < MIN_ALPHA_COVERAGE)
            )
            or not image.eligible_for_editorial_use(
                sources,
                allow_risk_accepted=POLICY.allow_public_official_image_risk,
            )
        ):
            continue
        keys = image_visual_keys(image)
        if seen.intersection(keys):
            continue
        seen.update(keys)
        result.append(image)
    return result


def cover_usable_editorial_images(
    images: list[ImageAsset],
    sources: list[SourceEvidence],
) -> list[ImageAsset]:
    """Keep only source images that can support a full-resolution cover placement."""
    sample_card = CopyCard(
        page=1,
        role="cover",
        eyebrow="공식 소식",
        title_lines=["작품의 새 소식"],
        body_sentences=["팬이 알아야 할 핵심 내용을 전합니다."],
        image_id="cover-fit-probe",
    )
    return [
        image
        for image in distinct_editorial_images(images, sources)
        if _fits_card_resolution(image, sample_card)
    ]


def assign_distinct_images(
    cards: list[CopyCard],
    package: ContentPackage,
) -> list[CopyCard]:
    """Use distinct source images and mark unfilled cards for crawler handoff."""
    candidates = distinct_editorial_images(package.images, package.sources)
    by_id = {image.id: image for image in candidates}
    used: set[str] = set()
    assigned: list[CopyCard] = []
    for card in sorted(cards, key=lambda item: (item.role != "cover", item.page)):
        available = [
            image
            for image in candidates
            if not used.intersection(image_visual_keys(image))
            and _fits_card_resolution(image, card)
        ]
        if not available:
            is_final_card = card.role == "ending" and card.page == max(item.page for item in cards)
            assigned.append(
                card.model_copy(
                    update={
                        "image_id": (
                            f"editorial-graphic-{card.page}"
                            if is_final_card
                            else f"missing-source-image-{card.page}"
                        ),
                        "visual_mode": ("editorial_graphic" if is_final_card else "source_image"),
                        "image_reuse_mode": (
                            "editorial_graphic" if is_final_card else "missing_source_image"
                        ),
                        "image_explanation": (
                            "원본 이미지 반복 대신 자체 제작한 정보 그래픽으로 구성"
                        ),
                    }
                )
            )
            continue
        preferred = by_id.get(card.image_id)
        if preferred and any(item.id == preferred.id for item in available):
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

            selected = max(available, key=score)
        used.update(image_visual_keys(selected))
        explanation = card.image_explanation.strip()
        if selected.id != card.image_id and selected.korean_explanation:
            explanation = selected.korean_explanation
        assigned.append(
            card.model_copy(
                update={
                    "image_id": selected.id,
                    "visual_mode": "source_image",
                    "image_explanation": explanation,
                    "image_reuse_mode": "unique",
                }
            )
        )
    return assigned
