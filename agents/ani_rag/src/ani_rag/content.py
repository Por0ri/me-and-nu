"""Content draft contracts. Generation is an injected service, never self-approval."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from math import isfinite
import re
from typing import Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from ani_rag.models import EvidenceDocument, RagContentType
from ani_rag.retrieval import RetrievedEvidence

TEXT_ONLY_ASSET_ID = "text_only_gradient"
DESIGNED_COVERS = frozenset({"beginner_guide", "cast_intro_grid", "fan_recommendation"})
AI_DISCLOSURE = "\uc774 \ucf58\ud150\uce20\ub294 \uc6d0\ubb38\uacfc \ud655\uc778 \uc790\ub8cc\ub97c \ubc14\ud0d5\uc73c\ub85c AI\ub97c \uc0ac\uc6a9\ud574 \uc0dd\uc131\u00b7\uc815\ub9ac\ud588\uc2b5\ub2c8\ub2e4."


class DuplicateContentError(ValueError):
    """Raised when a draft repeats a recent work-specific content angle."""


def web_claims_can_generate(claims: Sequence[Mapping[str, object]]) -> bool:
    """A supported story matters more than how many pieces its text was cut into."""
    if not claims:
        return False
    if all("kind" not in item for item in claims):
        return len(claims) >= 3  # Previously approved jobs used an untyped three-passage contract.
    anchors = [item for item in claims if item.get("kind") in {"rag_core", "rag_context"}]
    core = [item for item in anchors if item["kind"] == "rag_core"]
    if any(item.get("standalone") is True for item in core) or (core and len(anchors) >= 2):
        return True
    return bool(anchors and any(item.get("kind") == "attributed_quote" for item in claims))


def web_claims_for_generation(
    source: Mapping[str, object], citation_ref_by_chunk: Mapping[str, str],
) -> list[dict[str, object]]:
    """Only carry a verified passage if every corroborating chunk is still available."""
    claims = source.get("verified_claims")
    if not isinstance(claims, (tuple, list)):
        return []
    result = []
    for item in claims:
        if not isinstance(item, Mapping) or not isinstance(item.get("claim"), str):
            continue
        ids = item.get("chunk_ids")
        if not isinstance(ids, (tuple, list)) or not ids or any(
            not isinstance(key, str) or key not in citation_ref_by_chunk for key in ids
        ):
            continue
        claim = {"claim": item["claim"], "evidence_refs": [citation_ref_by_chunk[key] for key in ids]}
        claim.update({key: item[key] for key in ("kind", "standalone", "speaker", "attribution_excerpt") if key in item})
        result.append(claim)
    return result


@dataclass(frozen=True)
class SupportedClaim:
    text: str
    evidence_ids: tuple[str, ...]
    evidence_chunk_ids: tuple[str, ...]
    attribution: str | None = None

    def __post_init__(self) -> None:
        if not self.text.strip() or not self.evidence_ids or not self.evidence_chunk_ids:
            raise ValueError("Every content claim needs text, evidence IDs, and retrieved chunk IDs")


@dataclass(frozen=True)
class NormalizedBox:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if min(self.x, self.y, self.width, self.height) < 0:
            raise ValueError("Normalized layout coordinates cannot be negative")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Normalized layout boxes must fit inside the page")


@dataclass(frozen=True)
class CardLayout:
    """Per-page image and frontend HTML overlay coordinates."""

    image_box: NormalizedBox = field(
        default_factory=lambda: NormalizedBox(0.04, 0.04, 0.92, 0.68)
    )
    text_overlay_box: NormalizedBox = field(
        default_factory=lambda: NormalizedBox(0.06, 0.68, 0.88, 0.28)
    )
    background_style: str = "blur"
    template: str = "image_top_copy_bottom"
    image_emphasis: str = "dominant"

    def __post_init__(self) -> None:
        if self.text_overlay_box.y < 0.60:
            raise ValueError("HTML card copy must stay in the lower page area")
        if self.background_style not in {"blur", "palette", "gradient"}:
            raise ValueError("Unsupported source-derived background style")
        if self.image_emphasis not in {"dominant", "balanced"}:
            raise ValueError("image_emphasis must be dominant or balanced")


@dataclass(frozen=True)
class CardDraft:
    card_id: str
    role: str
    headline: str
    body: str
    claims: tuple[SupportedClaim, ...]
    image_asset_id: str
    card_image_url: str | None = None
    source_image_url: str | None = None
    source_page_url: str | None = None
    image_rights_status: str | None = None
    image_credit: str | None = None
    layout: CardLayout = field(default_factory=CardLayout)
    component_image_asset_ids: tuple[str, ...] = ()

    @property
    def source_asset_ids(self) -> tuple[str, ...]:
        """Registered source images in paint order, with the primary image first."""
        return (self.image_asset_id, *self.component_image_asset_ids)

    def __post_init__(self) -> None:
        if not self.card_id.strip() or not self.role.strip() or not self.image_asset_id.strip():
            raise ValueError("Card ID, role, and image mode are required")
        if not self.headline.strip() or not self.body.strip():
            raise ValueError("Each card needs a headline and body")
        if self.layout.template in DESIGNED_COVERS and self.role != "cover":
            raise ValueError("Cover designs can only be used on the cover card")
        if (self.layout.template == "cast_intro_grid" and len(self.component_image_asset_ids) not in (2, 3)
                or self.layout.template == "fan_recommendation" and len(self.component_image_asset_ids) != 4):
            raise ValueError("Cover design requires its full set of source images")
        if self.component_image_asset_ids:
            if self.layout.template not in {"cast_intro_grid", "fan_recommendation"}:
                raise ValueError("Additional images require a composite cover design")
            if (not all(asset_id.strip() for asset_id in self.component_image_asset_ids)
                    or len(set(self.source_asset_ids)) != len(self.source_asset_ids)):
                raise ValueError("Cover source images must be present and distinct")


@dataclass(frozen=True)
class ImageEntityLink:
    entity_id: int
    relation_type: str
    source_document_id: str
    source_chunk_id: str
    status: str = "matched"
    entity_name: str | None = None

    def __post_init__(self) -> None:
        if self.entity_id <= 0 or not all(value.strip() for value in (
            self.relation_type, self.source_document_id, self.source_chunk_id,
        )):
            raise ValueError("Image/entity links need a catalog ID, relation, and evidence provenance")
        if self.status not in {"matched", "unresolved", "ambiguous"}:
            raise ValueError("Unsupported image/entity link status")


def kitsu_image_slot(source_url: str) -> tuple[str, str] | None:
    """Read the Kitsu resource from either CDN path used by imported and current art."""
    url = urlsplit(source_url)
    if url.hostname not in {"media.kitsu.app", "media.kitsu.io"}:
        return None
    for role, routes in (
        ("poster", (r"/anime/poster_images/(\d+)/", r"/anime/(\d+)/poster_image/")),
        ("character", (r"/characters/images/(\d+)/", r"/character/(\d+)/image/")),
        ("cover", (r"/anime/cover_images/(\d+)/", r"/anime/(\d+)/cover_image/")),
    ):
        for route in routes:
            if match := re.match(route, url.path):
                return role, match.group(1)
    return None


def image_source_identity_keys(
    asset_id: str, source_url: str, content_sha256: str | None, source_id: str,
    source_native_id: str | None, image_role: str,
) -> frozenset[tuple[str, str]]:
    """Any shared key identifies the same original, including a Kitsu size variant."""
    keys = {("asset_id", asset_id)}
    if source_url:
        keys.add(("source_url", source_url))
    if content_sha256:
        keys.add(("sha256", content_sha256.lower()))
    role = image_role.partition(":")[0]
    if source_id == "kitsu_anime_catalog":
        slot = kitsu_image_slot(source_url)
        if slot:
            keys.add(("kitsu_image", ":".join(slot)))
        elif source_native_id and role in {"cover", "poster", "character"}:
            keys.add(("kitsu_image", f"{role}:{source_native_id}"))
    return frozenset(keys)


@dataclass(frozen=True)
class ImageAsset:
    asset_id: str
    source_url: str
    source_id: str
    work_link_status: str
    menu_work_ids: tuple[int, ...]
    rights_basis: str | None
    rights_evidence_url: str | None
    local_path: str
    source_page_url: str | None = None
    source_native_id: str | None = None
    image_role: str = "artwork"
    alt_text: str | None = None
    content_sha256: str | None = None
    media_type: str | None = None
    byte_size: int | None = None
    width: int | None = None
    height: int | None = None
    entity_links: tuple[ImageEntityLink, ...] = ()
    recent_use_count: int = 0
    last_used_at: str | None = None
    used_as_cover_before: bool = False

    @property
    def source_identity_keys(self) -> frozenset[tuple[str, str]]:
        return image_source_identity_keys(self.asset_id, self.source_url, self.content_sha256,
                                         self.source_id, self.source_native_id, self.image_role)


@dataclass(frozen=True)
class ContentEntityLink:
    entity_id: int
    link_method: str
    sort_order: int

    def __post_init__(self) -> None:
        if self.entity_id <= 0 or self.sort_order <= 0 or not self.link_method.strip():
            raise ValueError("Entity links need positive IDs/order and a link method")


@dataclass(frozen=True)
class ContentTag:
    subtopic_id: int
    tagged_by: str

    def __post_init__(self) -> None:
        if self.subtopic_id <= 0 or not self.tagged_by.strip():
            raise ValueError("Content tags need a positive subtopic ID and tagged_by")


@dataclass(frozen=True)
class ContentDraft:
    draft_id: str
    content_type: RagContentType
    title: str
    cards: tuple[CardDraft, ...]
    detail_body: str
    detail_claims: tuple[SupportedClaim, ...]
    evidence_ids: tuple[str, ...]
    menu_work_ids: tuple[int, ...]
    retrieval_id: str
    created_at: datetime
    source_footer: str
    ai_disclosure: str = AI_DISCLOSURE
    retrieved_chunk_ids: tuple[str, ...] = ()
    topic_id: int | None = None
    topic_label: str | None = None
    summary: str | None = None
    keywords: tuple[str, ...] = ()
    thumbnail_url: str | None = None
    is_sponsored: bool = False
    is_changed_notice: bool = False
    ai_judgment_basis: Mapping[str, object] = field(default_factory=dict)
    event_detail_id: int | None = None
    published_at: datetime | None = None
    valid_until: datetime | None = None
    embedding: tuple[float, ...] | None = None
    entity_links: tuple[ContentEntityLink, ...] = ()
    tags: tuple[ContentTag, ...] = ()

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        for name in ("published_at", "valid_until"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if self.topic_id is not None and self.topic_id <= 0:
            raise ValueError("topic_id must be a positive ERD topic identifier")
        if self.topic_label is not None and not self.topic_label.strip():
            raise ValueError("topic_label cannot be blank when supplied")
        if self.event_detail_id is not None and self.event_detail_id <= 0:
            raise ValueError("event_detail_id must be positive when supplied")
        if self.valid_until and self.published_at and self.valid_until < self.published_at:
            raise ValueError("valid_until cannot precede published_at")
        if self.embedding is not None and (
            len(self.embedding) != 1024 or any(not isfinite(value) for value in self.embedding)
        ):
            raise ValueError("ERD content embedding must contain exactly 1024 finite values")
        if not self.draft_id.strip() or not self.title.strip() or not self.retrieval_id.strip():
            raise ValueError("Draft ID, title, and retrieval ID are required")
        if not self.detail_body.strip() or not self.evidence_ids:
            raise ValueError("Draft needs a detail article and stored evidence IDs")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Draft evidence IDs must be unique")
        if not self.retrieved_chunk_ids:
            raise ValueError("Draft needs the IDs of retrieved evidence chunks used as context")
        if self.ai_disclosure != AI_DISCLOSURE:
            raise ValueError("AI disclosure must match the approved Korean phrase exactly")
        if any(work_id <= 0 for work_id in self.menu_work_ids):
            raise ValueError("Draft work IDs must be positive catalog IDs")
        if len({link.entity_id for link in self.entity_links}) != len(self.entity_links):
            raise ValueError("Draft entity links must be unique")
        if len({tag.subtopic_id for tag in self.tags}) != len(self.tags):
            raise ValueError("Draft subtopic tags must be unique")
        attached_chunks = set(self.retrieved_chunk_ids)
        for claim in (*self.detail_claims, *(claim for card in self.cards for claim in card.claims)):
            if not set(claim.evidence_ids) <= set(self.evidence_ids):
                raise ValueError("Claim evidence must be attached to the draft")
            if not set(claim.evidence_chunk_ids) <= attached_chunks:
                raise ValueError("Claim chunk IDs must be among retrieved draft context")


def assemble_detail_article(draft: ContentDraft) -> str:
    """Build the reader-facing detail section: article, source credits, then AI notice."""
    return "\n\n".join((
        draft.detail_body.rstrip(),
        "## \ucd9c\ucc98\n\n" + draft.source_footer.strip(),
        "## AI \uc0ac\uc6a9 \uc548\ub0b4\n\n" + draft.ai_disclosure,
    ))

class GenerationService(Protocol):
    async def generate(
        self, retrieval: RetrievedEvidence, *, image_candidates: Sequence[ImageAsset] = (),
        revision_instructions: Sequence[str] = (),
        previous_copy: Mapping[str, object] | None = None,
        avoid_recent_topics: Sequence[Mapping[str, str]] = (),
        primary_web_source: Mapping[str, object] | None = None,
    ) -> ContentDraft:
        """Use a web source as the story subject and RAG chunks as factual support."""


class GenerationOutcome(StrEnum):
    DRAFT_FOR_REVIEW = "draft_for_review"
    WAITING_FOR_EVIDENCE = "waiting_for_evidence"


def require_generation_evidence(evidence_ids: Sequence[str]) -> tuple[str, ...]:
    cleaned = tuple(dict.fromkeys(item.strip() for item in evidence_ids if item.strip()))
    if not cleaned:
        raise ValueError("Generation must wait until evidence has been stored")
    return cleaned
