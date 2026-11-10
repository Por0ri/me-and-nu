"""Independent, source-attributed evidence contracts for evergreen collection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
import json
import unicodedata
from urllib.parse import urlsplit


class RagContentType(StrEnum):
    CHARACTER = "character"
    GUIDE = "guide"
    PEOPLE = "people"
    MUSIC = "music"
    HUMOR = "humor"
    CURATION = "curation"
    REVIEW = "review"


class EvidenceType(StrEnum):
    OFFICIAL_FACT = "official_fact"
    REFERENCE_FACT = "reference_fact"
    EDITORIAL_CLAIM = "editorial_claim"
    EDITORIAL_REVIEW = "editorial_review"
    CURATION_RESULT = "curation_result"
    COMMUNITY_SIGNAL = "community_signal"
    PRODUCTION_BEHIND_SCENES = "production_behind_scenes"


class WorkLinkStatus(StrEnum):
    MATCHED = "matched"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    NOT_APPLICABLE = "not_applicable"


class WorkEntryFormat(StrEnum):
    TV = "tv"
    MOVIE = "movie"
    OVA = "ova"
    ONA = "ona"
    SPECIAL = "special"
    MANGA = "manga"
    NOVEL = "novel"
    OTHER = "other"
    UNKNOWN = "unknown"


class WorkEntryRelation(StrEnum):
    MAIN = "main"
    SEASON = "season"
    SEQUEL = "sequel"
    PREQUEL = "prequel"
    SPIN_OFF = "spin_off"
    COMPILATION = "compilation"
    ADAPTATION = "adaptation"
    RELATED = "related"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WorkRegistration:
    """A source-supported new work candidate; storage assigns its ID atomically."""

    name: str
    aliases: tuple[str, ...] = ()
    external_ids: dict[str, str] = field(default_factory=dict)
    evidence_quote: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.evidence_quote.strip():
            raise ValueError("New work registration needs a name and source quote")
        if any(not key.strip() or not value.strip() for key, value in self.external_ids.items()):
            raise ValueError("New-work external IDs cannot be blank")


class CollectionMethod(StrEnum):
    MCP_SEARCH = "mcp_search"
    MCP_READ = "mcp_read"
    MANUAL_IMPORT = "manual_import"


class ParseStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    PARTIAL = "partial"
    FAILED = "failed"


class AuthorAttribution(StrEnum):
    ATTRIBUTED = "attributed"
    UNATTRIBUTED = "unattributed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvidenceExcerpt:
    """A source passage with an optional locator and an optional claim link."""

    text: str
    locator: str | None = None
    supports_claim_index: int | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Evidence excerpt text cannot be empty")
        if self.supports_claim_index is not None and self.supports_claim_index < 0:
            raise ValueError("supports_claim_index must be zero or greater")


@dataclass(frozen=True)
class CurationContext:
    """Survey context; unknown fields remain explicit as None."""

    window_start: date | None = None
    window_end: date | None = None
    sample_size: int | None = None
    aggregation_method: str | None = None
    source_context: str | None = None

    def __post_init__(self) -> None:
        if self.window_start and self.window_end and self.window_end < self.window_start:
            raise ValueError("Curation window_end cannot precede window_start")
        if self.sample_size is not None and self.sample_size < 0:
            raise ValueError("Curation sample_size cannot be negative")


@dataclass(frozen=True)
class EvidenceDocument:
    """Preserved source material; opinion and community evidence stay typed as such."""

    document_id: str
    content_type: RagContentType
    title: str
    source_url: str
    source_id: str
    retrieved_at: datetime
    evidence_type: EvidenceType
    collection_method: CollectionMethod
    language: str
    source_native_id: str | None = None
    publisher: str | None = None
    body: str | None = None
    raw_payload: dict[str, object] = field(default_factory=dict)
    excerpts: tuple[EvidenceExcerpt, ...] = ()
    published_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    work_link_status: WorkLinkStatus = WorkLinkStatus.UNRESOLVED
    menu_work_ids: tuple[int, ...] = ()
    candidate_menu_work_ids: tuple[int, ...] = ()
    new_work_registration: WorkRegistration | None = None
    author_name: str | None = None
    author_attribution: AuthorAttribution = AuthorAttribution.UNKNOWN
    claims: tuple[str, ...] = ()
    curation_context: CurationContext | None = None
    temporal_context: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    event_detail_id: int | None = None
    parse_status: ParseStatus = ParseStatus.PARSED
    parse_error: str | None = None
    purge_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.document_id.strip() or not self.source_id.strip():
            raise ValueError("document_id and source_id are required")
        if not self.title.strip() or not self.language.strip():
            raise ValueError("title and original language are required")
        parsed_url = urlsplit(self.source_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.username or parsed_url.password:
            raise ValueError("source_url must be a credential-free absolute HTTP(S) URL")
        if not (self.body and self.body.strip()) and not self.excerpts:
            raise ValueError("Provide source body text or at least one evidence excerpt")

        for name in ("retrieved_at", "published_at", "valid_from", "valid_until", "purge_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until cannot precede valid_from")
        if self.event_detail_id is not None and self.event_detail_id <= 0:
            raise ValueError("event_detail_id must be positive when supplied")
        if self.parse_status is ParseStatus.FAILED and not self.parse_error:
            raise ValueError("Failed parsing must preserve parse_error")
        try:
            json.dumps(self.raw_payload, ensure_ascii=False)
        except (TypeError, ValueError) as error:
            raise ValueError("raw_payload must be JSON-compatible") from error

        if self.work_link_status is WorkLinkStatus.MATCHED:
            if not self.menu_work_ids or self.candidate_menu_work_ids:
                raise ValueError("Matched work links need menu_work_ids and no candidate IDs")
        elif self.work_link_status is WorkLinkStatus.AMBIGUOUS:
            if not self.candidate_menu_work_ids or self.menu_work_ids:
                raise ValueError("Ambiguous work links need candidate IDs, not resolved IDs")
        elif self.menu_work_ids or self.candidate_menu_work_ids:
            raise ValueError("Unresolved/not-applicable work links cannot carry work IDs")
        if self.new_work_registration is not None:
            if self.work_link_status is not WorkLinkStatus.UNRESOLVED or self.menu_work_ids or self.candidate_menu_work_ids:
                raise ValueError("A new-work candidate cannot already have a resolved work link")
            source_material = "\n".join((self.title, self.source_native_id or "", self.body or "", *(item.text for item in self.excerpts)))
            if self.new_work_registration.evidence_quote.casefold() not in source_material.casefold():
                raise ValueError("New-work identity quote must appear in the preserved source material")
            normalized_source = unicodedata.normalize("NFKC", source_material).casefold()
            normalized_name = unicodedata.normalize("NFKC", self.new_work_registration.name).casefold()
            if normalized_name not in normalized_source:
                raise ValueError("New-work canonical name must appear in the preserved source material")
            if any(value.casefold() not in source_material.casefold()
                   for value in self.new_work_registration.external_ids.values()):
                raise ValueError("New-work external IDs must appear in the preserved source material")
        if any(work_id <= 0 for work_id in (*self.menu_work_ids, *self.candidate_menu_work_ids)):
            raise ValueError("Work IDs must be positive integers")

        if self.evidence_type is EvidenceType.EDITORIAL_REVIEW:
            if self.content_type is not RagContentType.REVIEW:
                raise ValueError("Editorial review evidence must use content_type='review'")
            if not self.claims:
                raise ValueError("Review material must preserve attributed claims")
            if self.author_attribution is AuthorAttribution.ATTRIBUTED and not self.author_name:
                raise ValueError("Attributed review material needs author_name")
        if self.evidence_type is EvidenceType.CURATION_RESULT:
            if self.content_type is not RagContentType.CURATION or self.curation_context is None:
                raise ValueError("Curation results need curation content type and context")
        if self.evidence_type is EvidenceType.COMMUNITY_SIGNAL:
            if self.content_type is not RagContentType.HUMOR:
                raise ValueError("Community signals must use content_type='humor'")
        if self.evidence_type is EvidenceType.PRODUCTION_BEHIND_SCENES:
            if self.content_type is not RagContentType.HUMOR:
                raise ValueError("Production behind-scenes evidence must use content_type='humor'")
        for excerpt in self.excerpts:
            index = excerpt.supports_claim_index
            if index is not None and index >= len(self.claims):
                raise ValueError("Excerpt claim index is outside the claims list")
