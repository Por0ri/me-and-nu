"""Distinct, production-usable source image selection for carousel cards."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from ani_rag.content.models import ContentPackage, CopyCard, ImageAsset, SourceEvidence
from ani_rag.content.policy import POLICY

# Modest sources may still be usable in the existing low-res spotlight layout.
# Keep a separate cover floor so the broader detail-page pool cannot weaken covers.
MIN_SOURCE_EDGE = 160
MIN_SOURCE_PIXELS = 40_000
MIN_COVER_SOURCE_EDGE = 320
MIN_ALPHA_COVERAGE = 0.10
MIN_EDITORIAL_ASPECT_RATIO = 0.42
MAX_EDITORIAL_ASPECT_RATIO = 3.2
_CONTEXT_SPECIFIC_IMAGE_DESCRIPTION_RE = re.compile(
    r"\b(?:goods?|merch(?:andise)?|product|figure|plush|popup|pop-up|event|"
    r"campaign|collab(?:oration)?|bonus|benefit|exhibition|expo|festival|festa|fair|"
    r"anniversary|ticket|screening|convention|premiere|stage event|live event)\b|"
    r"\u7279\u5178|\u30b0\u30c3\u30ba|\u5546\u54c1|\u8cfc\u5165|"
    r"\u8ca9\u58f2|\u30dd\u30c3\u30d7\u30a2\u30c3\u30d7|"
    r"\u30b3\u30e9\u30dc|\u30a4\u30d9\u30f3\u30c8|"
    r"\u5c55\u793a|\u5c55\u89a7\u4f1a|\u535a\u89a7\u4f1a|\u7279\u5225\u5c55|"
    r"\u4e0a\u6620\u4f1a|\u4e0a\u6620|\u821e\u53f0\u6328\u62f6|"
    r"\u958b\u50ac|\u50ac\u4e8b|\u50ac\u5834|\u51fa\u5c55|\u30d5\u30a7\u30a2|\u30d5\u30a7\u30b9\u30bf|\u30e9\u30a4\u30d6|"
    r"\u4e88\u7d04|\u914d\u5e03|\u30e1\u30cb\u30e5\u30fc|"
    r"\u30b0\u30c3\u30ba|\u30ad\u30fc\u30db\u30eb\u30c0\u30fc|"
    r"\u30b3\u30fc\u30b9\u30bf\u30fc|\u30ad\u30c3\u30c1\u30f3\u30ab\u30fc|"
    r"\u30ef\u30fc\u30c9\u30e9\u30ea\u30fc|\u30b9\u30bf\u30f3\u30d7\u30e9\u30ea\u30fc|"
    r"\u76e3\u7763\u30b3\u30e1\u30f3\u30c8|\u5468\u5e74|\u8a18\u5ff5|\u30d5\u30a7\u30b9|\u30c1\u30fc\u30ba|"
    r"\u30dd\u30c6\u30c8|\u30e9\u30fc\u30e1\u30f3|\u9ebb\u5a46\u8c46\u8150|"
    r"\u30b7\u30c1\u30e5\u30fc|\u30c1\u30e5\u30ed\u30b9|\u3064\u304f\u308d\u3046",
    re.IGNORECASE,
)


def _normalized_source_url(url: str) -> str:
    """Collapse known renditions without erasing API parameters that identify files."""
    parts = urlsplit(url)
    path = re.sub(r"-\d{2,4}x\d{2,4}(?=\.[^.]+$)", "", parts.path)
    path = re.sub(r"-(?:l|large|full)(?=\.[^.]+$)", "", path, flags=re.IGNORECASE)
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
    package: ContentPackage | None = None,
    *,
    card_role: str | None = None,
) -> bool:
    """Accept source-page images or provenance-checked official art for the same work."""
    image_page = _source_page_key(str(image.source_url))
    same_source_page = any(_source_page_key(str(source.url)) == image_page for source in sources)
    if not same_source_page and package is not None:
        # 사전 글은 출처를 사이트별 한 줄로 묶어 보여 주므로 (11/11 서원), 사실마다 남은 근거 페이지 주소로도 확인한다
        same_source_page = any(
            _source_page_key(str(fact.details.get("source_url"))) == image_page
            for fact in package.facts
            if fact.details.get("source_url")
        )
    if (
        package is not None
        and image.menu_work_ids
        and not set(image.menu_work_ids).intersection(package.menu_work_ids)
    ):
        # Page co-location is not evidence that an explicitly different-work
        # asset belongs to this package (e.g. a multi-franchise article page).
        return False
    provenance_title = image.provenance_source.title if image.provenance_source else ""
    source_path = unquote(urlsplit(str(image.source_url)).path)
    image_context = " ".join((image.source_description, provenance_title, source_path))
    if not same_source_page and _CONTEXT_SPECIFIC_IMAGE_DESCRIPTION_RE.search(image_context):
        # A product/campaign visual from another official story is not generic
        # anime artwork, even if legacy metadata mislabeled it as a key visual.
        return False
    content_type = package.content_type if package is not None else None
    if image.asset_kind == "goods_product" and content_type != "goods":
        return False
    if image.asset_kind == "event_visual" and content_type != "event":
        return False
    if image.asset_kind == "fan_meme" and (
        package is None or package.content_kind.value != "humor"
    ):
        return False
    if same_source_page:
        return True
    # A source-page event/product image wins when available. If the article has
    # no usable visual, exact-work catalog art is still a valid fallback for
    # the cover and supporting cards. Context-specific assets from another
    # article were rejected above, so a different product/event cannot leak in.
    provenance = image.provenance_source
    if (
        package is not None
        and provenance is not None
        and provenance.role == "reference"
        # editorial_photo: 가수 · 인물 사진 (AniList 인물 페이지, 11/11 서원: 가수 모음 표지에 가수 사진)
        and image.asset_kind
        in {"catalog_poster", "catalog_cover", "character_art", "scene_still", "editorial_photo"}
        and set(image.menu_work_ids).intersection(package.menu_work_ids)
        and image.public_editorial_source_matches([provenance])
    ):
        return True
    if (
        package is None
        or provenance is None
        or not provenance.is_official
        or provenance.role != "primary"
        or not set(image.menu_work_ids).intersection(package.menu_work_ids)
        or image.asset_kind
        not in {
            "official_key_visual",
            "character_art",
            "scene_still",
            "manga_panel",
        }
    ):
        return False
    return image.public_official_source_matches([provenance])


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


def _fits_card_resolution(
    image: ImageAsset,
    card: CopyCard,
    *,
    maximum_upscale: float | None = None,
) -> bool:
    """Reject small assets for large placements; blur cannot make an upscaled hero sharp."""
    if image.width < 1 or image.height < 1:
        return False
    from ani_rag.content.layout_engine import build_cards

    layout_card = card.model_copy(update={"image_id": image.id, "visual_mode": "source_image"})
    layout = build_cards([layout_card], [image])[0].layout
    target_width = (
        POLICY.logical_card_width * layout.image_box.width * POLICY.image_quality_target_dpr
    )
    target_height = (
        POLICY.logical_card_height * layout.image_box.height * POLICY.image_quality_target_dpr
    )
    upscale = min(target_width / image.width, target_height / image.height)
    allowed_upscale = maximum_upscale or (
        POLICY.max_low_res_spotlight_upscale
        if layout.template == "low_res_spotlight"
        else 2.5
        if card.role == "cover"
        else POLICY.max_image_upscale
    )
    return upscale <= allowed_upscale


def _image_context_mismatch(image: ImageAsset, package: ContentPackage) -> bool:
    """Reject explicitly season/movie-specific artwork that contradicts the story topic."""
    topic = package.topic.casefold()
    path = unquote(urlsplit(str(image.url)).path).casefold()
    asset_season = re.search(r"(?:season|visual_season)[_-]?(\d+)", path)
    target_season = re.search(r"(?:season|시즌)\s*(\d+)|第\s*(\d+)\s*期", topic)
    target_number = (
        next((group for group in target_season.groups() if group), None)
        if target_season
        else None
    )
    has_movie_asset = bool(re.search(r"movie|film|劇場版", path))
    has_movie_topic = bool(re.search(r"movie|film|극장판|劇場版", topic))
    if target_number:
        if asset_season and asset_season.group(1) != target_number:
            return True
        return has_movie_asset and not has_movie_topic
    if has_movie_asset and not has_movie_topic:
        return True
    if has_movie_topic and asset_season:
        return True
    return False


def distinct_editorial_images(
    images: list[ImageAsset],
    sources: list[SourceEvidence],
    package: ContentPackage | None = None,
    *,
    card_role: str | None = None,
) -> list[ImageAsset]:
    """Return unique eligible public images that can support a readable card."""
    result: list[ImageAsset] = []
    seen: set[str] = set()
    # Crawl retries may leave both thumbnail and full-size renditions in storage.
    # Consider the largest rendition first so URL-level deduplication keeps the
    # usable source rather than discarding it after seeing a low-res duplicate.
    for image in sorted(images, key=lambda item: item.width * item.height, reverse=True):
        if (
            image.used_in_previous_content
            or image.vision_review_rejected
            or image.download_status not in {"downloaded", "available"}
            or image.width < 1
            or image.height < 1
            or max(image.width, image.height) < MIN_SOURCE_EDGE
            or image.width * image.height < MIN_SOURCE_PIXELS
            or not is_editorially_composable(image)
            or not image_belongs_to_package_sources(
                image, sources, package, card_role=card_role
            )
            or (package is not None and _image_context_mismatch(image, package))
            or (
                image.has_alpha
                and (image.alpha_coverage is None or image.alpha_coverage < MIN_ALPHA_COVERAGE)
            )
            or not image.eligible_for_editorial_use(
                [*sources, *([image.provenance_source] if image.provenance_source else [])],
                allow_risk_accepted=(
                    POLICY.allow_public_official_image_risk
                    or POLICY.allow_public_editorial_image_risk
                ),
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
    package: ContentPackage | None = None,
) -> list[ImageAsset]:
    """Keep relevant cover art available so layout/upscaling can try to use it."""
    return [
        image
        for image in distinct_editorial_images(
            images, sources, package, card_role="cover"
        )
        if max(image.width, image.height) >= MIN_COVER_SOURCE_EDGE
    ]


def _text_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).casefold()


def _subject_score(card: CopyCard, image: ImageAsset, package: ContentPackage) -> int:
    """이 그림이 카드 내용의 인물 · 작품과 맞는 정도. 인물 2점, 작품 1점 (카드 글 + 카드가 쓴 사실에서 찾는다)"""
    facts = {fact.id: fact.statement for fact in package.facts}
    text = _text_key(
        " ".join([*card.title_lines, *card.body_sentences, *(facts.get(i, "") for i in card.fact_ids)])
    )
    score = 0
    for entity in image.depicted_entities:
        if any(len(_text_key(part)) >= 2 and _text_key(part) in text for part in entity.split()):
            score += 2
    title = image.provenance_source.title.split(" — ")[0] if image.provenance_source else ""
    if len(_text_key(title)) >= 2 and _text_key(title) in text:
        score += 1
    return score


def assign_distinct_images(
    cards: list[CopyCard],
    package: ContentPackage,
) -> list[CopyCard]:
    """Preflight every card, then assign distinct relevant images by constrained match.

    Cards with fewer eligible assets get first claim. An augmenting-path pass can
    move a flexible card to another asset when that preserves a unique assignment
    for a more constrained card. Visual fingerprints and normalized URLs share a
    single capacity, so alternate CDN renditions cannot be reused across cards.
    """
    if not cards:
        return []

    target_source_pages = {_source_page_key(str(source.url)) for source in package.sources}

    def card_score(card: CopyCard, image: ImageAsset) -> tuple[int, ...]:
        fact_ids = set(card.fact_ids)
        target_text = " ".join(
            [
                package.topic,
                *card.title_lines,
                *card.body_sentences,
                *(
                    alias
                    for term in package.term_glossary
                    if term.category == "character"
                    for alias in (term.korean, term.original)
                    if alias
                ),
            ]
        ).casefold()
        image_context = " ".join(
            (
                image.source_description,
                unquote(urlsplit(str(image.url or "")).path),
                image.provenance_source.title if image.provenance_source else "",
                *image.depicted_entities,
            )
        ).casefold()
        entity_matches = sum(
            1 for entity in image.depicted_entities if entity.casefold() in target_text
        )
        for term in package.term_glossary:
            if term.category != "character" or not term.korean.strip():
                continue
            if term.korean.casefold() not in target_text:
                continue
            if any(
                alias and alias.casefold() in image_context
                for alias in (term.korean, term.original)
            ):
                entity_matches += 1
        source_page_match = int(
            _source_page_key(str(image.source_url)) in target_source_pages
        )
        return (
            _subject_score(card, image, package),
            int(bool(set(card.menu_work_ids).intersection(image.menu_work_ids))),
            len(fact_ids.intersection(image.related_fact_ids)),
            entity_matches,
            source_page_match,
            int(image.visual_match == "exact"),
            int(image.visual_match in {"exact", "strong"}),
            int(image.id == card.image_id),
        )

    # Merge assets that share any source URL, byte fingerprint, or storage key.
    assets = list(package.images)
    parents = list(range(len(assets)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    token_owner: dict[str, int] = {}
    asset_index = {id(image): index for index, image in enumerate(assets)}
    for index, image in enumerate(assets):
        for token in image_visual_keys(image):
            previous = token_owner.setdefault(token, index)
            union(index, previous)

    # For each card, retain its best rendition for each unique visual and order
    # choices by semantic relevance. Nothing here relaxes eligibility or reuse.
    choices: dict[int, list[tuple[int, ImageAsset]]] = {}
    for card_index, card in enumerate(cards):
        best_by_visual: dict[int, ImageAsset] = {}
        for image in distinct_editorial_images(
            package.images, package.sources, package, card_role=card.role
        ):
            if not _fits_card_resolution(image, card):
                continue
            index = asset_index.get(id(image))
            if index is None:
                # Defensive fallback for model copies returned by a future filter.
                index = next(
                    (i for i, asset in enumerate(assets) if asset.id == image.id),
                    None,
                )
            if index is None:
                continue
            visual = find(index)
            current = best_by_visual.get(visual)
            if current is None or card_score(card, image) > card_score(card, current):
                best_by_visual[visual] = image
        choices[card_index] = [
            (visual, image)
            for visual, image in sorted(
                best_by_visual.items(),
                key=lambda pair: (card_score(card, pair[1]), pair[1].width * pair[1].height),
                reverse=True,
            )
        ]

    # Kuhn-style augmenting paths maximize the number of cards with a distinct
    # eligible image. Resolve the most constrained cards first for deterministic
    # tie-breaking; cover gets priority only when candidate counts are equal.
    owner_by_visual: dict[int, int] = {}
    assignment: dict[int, tuple[int, ImageAsset]] = {}

    pinned: set[int] = set()
    # 0) 장별 계획(card_plan)이 정한 그림을 가장 먼저 고정한다
    #    (11/11: 계획한 〈종말의 세라프〉 포스터 대신 귀멸 그림이 붙어 검수에서 막히던 문제)
    planned = {int(p["page"]): p.get("image_id") for p in package.card_plan if p.get("page") and p.get("image_id")}
    for card_index, card in enumerate(cards):
        wanted = planned.get(card.page)
        wanted_index = next((i for i, asset in enumerate(assets) if asset.id == wanted), None) if wanted else None
        if wanted_index is None:
            continue
        wanted_visual = find(wanted_index)
        for visual, image in choices[card_index]:
            if visual == wanted_visual and visual not in owner_by_visual:
                owner_by_visual[visual] = card_index
                assignment[card_index] = (visual, image)
                pinned.add(card_index)
                break

    # 먼저 카드 글에 나온 인물 · 작품의 그림을 그 카드에 고정한다. 표지는 여러 이름이 나오므로 맨 뒤에.
    for card_index in sorted(range(len(cards)), key=lambda i: (cards[i].role == "cover", cards[i].page)):
        if card_index in pinned:
            continue
        for visual, image in choices[card_index]:
            if visual not in owner_by_visual and _subject_score(cards[card_index], image, package) > 0:
                owner_by_visual[visual] = card_index
                assignment[card_index] = (visual, image)
                pinned.add(card_index)
                break

    def place(card_index: int, visited_visuals: set[int]) -> bool:
        for visual, image in choices[card_index]:
            if visual in visited_visuals:
                continue
            visited_visuals.add(visual)
            previous_card = owner_by_visual.get(visual)
            if previous_card in pinned:
                continue
            if previous_card is None or place(previous_card, visited_visuals):
                owner_by_visual[visual] = card_index
                assignment[card_index] = (visual, image)
                return True
        return False

    card_order = sorted(
        range(len(cards)),
        key=lambda index: (
            len(choices[index]),
            cards[index].role != "cover",
            cards[index].page,
        ),
    )
    for card_index in card_order:
        if card_index not in pinned:
            place(card_index, set())

    assigned: list[CopyCard] = []
    for card_index, card in enumerate(cards):
        match = assignment.get(card_index)
        if match is None:
            assigned.append(
                card.model_copy(
                    update={
                        "image_id": f"missing-source-image-{card.page}",
                        "visual_mode": "source_image",
                        "image_reuse_mode": "missing_source_image",
                        "image_explanation": "카드에 맞는 그림을 찾지 못함",
                    }
                )
            )
            continue
        _visual, selected = match
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
