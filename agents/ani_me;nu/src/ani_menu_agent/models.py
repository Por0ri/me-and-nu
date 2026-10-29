"""에이전트 노드 사이에서 주고받는 구조화 데이터 모델."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl, model_validator


def _host_matches_any(host: str, allowed_hosts: set[str]) -> bool:
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)


class ContentKind(StrEnum):
    INFORMATION = "information"
    HUMOR = "humor"


class ContentType(StrEnum):
    NEWS = "news"
    EVENT = "event"
    GUIDE = "guide"
    PEOPLE = "people"
    GOODS = "goods"
    BEHIND = "behind"  # Legacy stored value; new content uses the humor type.
    REVIEW = "review"
    HUMOR = "humor"
    CHARACTER = "character"
    CURATION = "curation"
    MUSIC = "music"


CONTENT_TYPE_LABELS = {
    ContentType.NEWS: "소식",
    ContentType.EVENT: "행사",
    ContentType.GUIDE: "작품 가이드",
    ContentType.PEOPLE: "인물",
    ContentType.GOODS: "굿즈",
    ContentType.REVIEW: "리뷰 분석",
    ContentType.HUMOR: "유머",
    ContentType.CHARACTER: "캐릭터",
    ContentType.CURATION: "큐레이션",
    ContentType.MUSIC: "음악",
}

CONTENT_TYPE_GUIDANCE = {
    "news": (
        "애니화·후속작 발표, 방영·OTT 일정, PV·비주얼 공개, 캐스팅·제작진 발표, "
        "원작 동향, 흥행·수상·업계 소식"
    ),
    "event": (
        "전시·원화전, 팝업스토어, 콜라보 카페, 상영회·재개봉, 무대인사, "
        "공연·애니송 이벤트, 대형 행사"
    ),
    "guide": "작품 소개, 입문 가이드, 정주행 순서, 세계관·용어 정리, 인물 관계도",
    "people": "성우 출연작, 감독·작가·스튜디오 소개, OP·ED 아티스트 등 사람 중심 정보",
    "curation": "테마별 작품 모음, 취향 연결, 분기 결산·랭킹, 숨은 명작 추천",
    "goods": "피규어·굿즈 발매·예약, 블루레이·OST, 한정판·특전, 작품 콜라보 상품",
    "review": "작품 리뷰, 분기 신작 첫인상, 원작과 애니메이션 비교·분석",
    "character": "캐릭터 생일, 프로필, 작품에서 확인되는 TMI와 인물 중심 이야기",
    "music": "애니메이션 OST·BGM·주제가·삽입곡과 음악가 정보",
    "humor": (
        "팬덤 유머·토론·밈과, 출처가 확인된 제작 비하인드·메이킹·성지순례·오마주 이야기. "
        "커뮤니티 의견은 의견으로 다루고, 제작진 인터뷰와 공식 비하인드의 사실은 "
        "근거에 맞춰 정확히 쓴다."
    ),
}


class RightsStatus(StrEnum):
    ALLOWED = "allowed"
    # Legacy value retained for old serialized records; it is not a publish approval.
    HUMAN_APPROVED = "human_approved"
    # Business risk acceptance for publicly posted official images; this is not a license.
    OFFICIAL_PUBLIC_RISK_ACCEPTED = "official_public_risk_accepted"
    # The project owner explicitly accepts limited editorial use of a public
    # image embedded in a registered publisher's same-work article. This is
    # still a risk decision, not a license or permission claim.
    PUBLIC_EDITORIAL_RISK_ACCEPTED = "public_editorial_risk_accepted"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"


class RightsBasis(StrEnum):
    RIGHTS_HOLDER_PERMISSION = "rights_holder_permission"
    OFFICIAL_MEDIA_KIT = "official_media_kit"
    LICENSE = "license"
    PUBLIC_DOMAIN = "public_domain"


class SourceEvidence(BaseModel):
    id: str
    title: str
    url: HttpUrl
    publisher: str
    is_official: bool = False
    published_at: str | None = None
    checked_at: str | None = None
    role: str = "primary"
    region: str = "global"
    allowed_asset_hosts: list[str] = Field(default_factory=list)
    # The source whose feed/listing surfaced the item is provenance, not fact
    # evidence. url above remains the page actually used to support facts.
    discovered_via_source_id: str | None = None
    discovery_url: HttpUrl | None = None
    discovery_title: str | None = None


class WorkMatchEvidence(BaseModel):
    """Auditable evidence used to attach a source/content item to a catalog work."""

    menu_work_id: int = Field(gt=0)
    basis: Literal[
        "article_title",
        "headline_character",
        "kitsu_catalog_title",
        "source_registry",
        "structured_about",
        "structured_mention",
        "registered_official_link",
    ]
    matched_value: str = Field(min_length=1, max_length=500)
    evidence_url: HttpUrl | None = None
    confidence: float = Field(ge=0, le=1)


class StructuredWorkReference(BaseModel):
    """A work name explicitly marked as about/mentioned by page metadata."""

    relation: Literal["about", "mention"]
    name: str = Field(min_length=1, max_length=500)
    url: HttpUrl | None = None


class Fact(BaseModel):
    id: str
    statement: str = Field(min_length=2)
    source_ids: list[str] = Field(min_length=1)
    verified: bool = False
    time_sensitive: bool = False
    fact_type: str = "general"
    # Source facts often contain structured nested data (e.g. cast members with
    # both a name and role). Keep the complete JSON object instead of making a
    # valid extraction fail when a nested object appears in a list.
    details: dict[str, Any] = Field(default_factory=dict)


class TermTranslation(BaseModel):
    original: str = Field(min_length=1)
    korean: str = Field(min_length=1)
    category: Literal["work_title", "character", "product", "brand", "event", "other"] = "other"

    @model_validator(mode="after")
    def normalize_known_korean_terms(self) -> TermTranslation:
        # Japanese クール is the anime-season term “cour”, conventionally Korean “쿨”.
        if "クール" in self.original and "쿠르" in self.korean:
            self.korean = self.korean.replace("쿠르", "쿨")
        return self


class ImageAsset(BaseModel):
    id: str
    url: HttpUrl | None = None
    local_path: str | None = None
    storage_key: str | None = None
    media_type: str | None = None
    file_size_bytes: int | None = Field(default=None, ge=0)
    content_bytes: bytes | None = Field(default=None, exclude=True, repr=False)
    source_url: HttpUrl
    # A separate official page may supply same-work artwork for a text-only source.
    # Keep that asset provenance distinct from fact sources shown in the article.
    provenance_source: SourceEvidence | None = None
    menu_work_ids: list[int] = Field(default_factory=list)
    official: bool = False
    rights_status: RightsStatus = RightsStatus.NEEDS_REVIEW
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    has_alpha: bool = False
    alpha_coverage: float | None = Field(default=None, ge=0, le=1)
    subject_hint: str = "center"
    subject_box: NormalizedBox | None = None
    fingerprint: str | None = None
    derived_from_image_id: str | None = None
    enhancement_model: str | None = None
    used_in_previous_content: bool = False
    related_fact_ids: list[str] = Field(default_factory=list)
    source_description: str = ""
    asset_kind: Literal[
        "official_key_visual",
        "catalog_poster",
        "catalog_cover",
        "character_art",
        "scene_still",
        "manga_panel",
        "goods_product",
        "event_visual",
        "fan_meme",
        "editorial_photo",
        "unknown",
    ] = "unknown"
    depicted_entities: list[str] = Field(default_factory=list)
    asset_metadata_confidence: float = Field(default=0.0, ge=0, le=1)
    visual_match: str = "context"
    detected_languages: list[str] = Field(default_factory=list)
    visible_text: list[str] = Field(default_factory=list)
    vision_review_rejected: bool = False
    korean_explanation: str = ""
    text_confidence: float = Field(default=0.0, ge=0, le=1)
    rights_evidence_url: HttpUrl | None = None
    rights_basis: RightsBasis | None = None
    rights_scope: list[Literal["instagram", "app", "commercial", "adapt"]] = Field(
        default_factory=list
    )
    rights_checked_at: str | None = None
    attribution_text: str = ""
    download_status: str = "pending"
    download_error: str | None = None

    @model_validator(mode="after")
    def require_location(self) -> ImageAsset:
        if not self.url and not self.local_path and not self.storage_key:
            raise ValueError("이미지는 url, local_path 또는 PostgreSQL storage_key가 필요합니다.")
        return self

    @property
    def cleared_for_mennu(self) -> bool:
        required_scope = {"instagram", "app", "commercial", "adapt"}
        return (
            self.rights_status == RightsStatus.ALLOWED
            and self.rights_basis is not None
            and self.rights_evidence_url is not None
            and self.rights_checked_at is not None
            and bool(self.attribution_text.strip())
            and required_scope.issubset(self.rights_scope)
        )

    def public_official_source_matches(self, sources: list[SourceEvidence]) -> bool:
        """Require both the page and asset host to map to an official primary source."""
        page_host = (urlparse(str(self.source_url)).hostname or "").lower().rstrip(".")
        asset_host = (
            (urlparse(str(self.url)).hostname or "").lower().rstrip(".") if self.url else ""
        )
        for source in sources:
            if not source.is_official or source.role != "primary":
                continue
            primary_host = (urlparse(str(source.url)).hostname or "").lower().rstrip(".")
            allowed_hosts = {
                primary_host,
                *(host.lower().rstrip(".") for host in source.allowed_asset_hosts),
            }
            if _host_matches_any(page_host, allowed_hosts) and (
                not asset_host or _host_matches_any(asset_host, allowed_hosts)
            ):
                return True
        return False

    def public_editorial_source_matches(self, sources: list[SourceEvidence]) -> bool:
        """Require an image embedded in the exact registered editorial page.

        This narrowly supports the owner's project-use risk decision for
        syndicated/public article imagery. It does not accept unrelated CDN
        URLs, community uploads, or images from another article.
        """
        page_url = str(self.source_url)
        page_host = (urlparse(page_url).hostname or "").lower().rstrip(".")
        asset_host = (
            (urlparse(str(self.url)).hostname or "").lower().rstrip(".") if self.url else ""
        )
        page_path = urlparse(page_url)._replace(query="", fragment="").geturl().rstrip("/")
        for source in sources:
            if source.is_official or source.role not in {"editorial", "reference"}:
                continue
            source_page = str(source.url)
            source_path = urlparse(source_page)._replace(query="", fragment="").geturl().rstrip("/")
            if page_path != source_path:
                continue
            allowed_hosts = {
                (urlparse(source_page).hostname or "").lower().rstrip("."),
                *(host.lower().rstrip(".") for host in source.allowed_asset_hosts),
            }
            if not any(
                page_host == allowed or page_host.endswith(f".{allowed}")
                for allowed in allowed_hosts
            ):
                continue
            if any(
                asset_host == allowed or asset_host.endswith(f".{allowed}")
                for allowed in allowed_hosts
            ):
                return True
        return False

    def eligible_for_editorial_use(
        self,
        sources: list[SourceEvidence],
        *,
        allow_risk_accepted: bool,
    ) -> bool:
        """Separate actual permission clearance from the user's accepted editorial risk."""
        eligible_sources = list(sources)
        if self.provenance_source and all(
            str(source.url) != str(self.provenance_source.url) for source in eligible_sources
        ):
            eligible_sources.append(self.provenance_source)
        if self.cleared_for_mennu:
            return True
        return (
            allow_risk_accepted
            and self.rights_status == RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED
            and self.official
            and self.public_official_source_matches(eligible_sources)
            and self.rights_evidence_url is not None
            and self.rights_checked_at is not None
            and bool(self.attribution_text.strip())
        ) or (
            allow_risk_accepted
            and self.rights_status == RightsStatus.PUBLIC_EDITORIAL_RISK_ACCEPTED
            and not self.official
            and self.public_editorial_source_matches(eligible_sources)
            and self.rights_evidence_url is not None
            and self.rights_checked_at is not None
            and bool(self.attribution_text.strip())
        )


class NormalizedBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @property
    def bottom(self) -> float:
        return self.y + self.height


class LayoutSpec(BaseModel):
    template: str = "split"
    fit_mode: str = "contain_blur"
    background_style: Literal["blur", "palette", "gradient"] = "blur"
    image_box: NormalizedBox
    copy_box: NormalizedBox
    # Frontend AI places HTML inside this per-page normalized region.
    text_overlay_box: NormalizedBox = Field(
        default_factory=lambda: NormalizedBox(x=0.06, y=0.67, width=0.88, height=0.29)
    )
    image_scale: float = Field(default=1.0, ge=0.5, le=1.5)
    image_offset_y: float = Field(default=0.0, ge=-0.3, le=0.3)
    contrast_mode: str = "scrim_required"
    blur_background: bool = False


class CardSpec(BaseModel):
    page: int = Field(ge=1)
    role: str
    eyebrow: str = ""
    title_lines: list[str] = Field(min_length=1, max_length=4)
    body_sentences: list[str] = Field(default_factory=list, max_length=4)
    protected_phrases: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    image_id: str
    visual_mode: Literal["source_image", "editorial_graphic"] = "source_image"
    image_explanation: str = ""
    image_reuse_mode: str = "unique"
    layout: LayoutSpec
    ending_joke: bool = False
    rendered_image_key: str | None = None
    rendered_width: int | None = Field(default=None, ge=1)
    rendered_height: int | None = Field(default=None, ge=1)

    @property
    def plain_title(self) -> str:
        return " ".join(self.title_lines).strip()


class CopyCard(BaseModel):
    """LLM이 작성하고 코드 레이아웃 엔진이 소비하는 최소 카피."""

    page: int = Field(ge=1)
    role: str
    eyebrow: str = ""
    title_lines: list[str] = Field(min_length=1, max_length=4)
    body_sentences: list[str] = Field(default_factory=list, max_length=4)
    protected_phrases: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    image_id: str
    visual_mode: Literal["source_image", "editorial_graphic"] = "source_image"
    image_explanation: str = ""
    image_reuse_mode: str = "unique"
    ending_joke: bool = False


class CopyDeck(BaseModel):
    content_type: str = Field(min_length=1, max_length=64)
    editorial_angle: str = Field(min_length=8, max_length=160)
    cover_mode: Literal[
        "wordplay",
        "contrast_irony",
        "question",
        "in_world_scene",
        "deadpan",
        "fan_empathy",
        "surprising_fact",
        "teaser",
        "declarative",
    ]
    ending_mode: Literal[
        "wordplay",
        "contrast_irony",
        "question",
        "in_world_scene",
        "deadpan",
        "fan_empathy",
        "surprising_fact",
        "teaser",
        "declarative",
    ]
    cards: list[CopyCard] = Field(min_length=2, max_length=10)


class CardLanguageReview(BaseModel):
    page: int = Field(ge=1)
    korean_naturalness: int = Field(ge=0, le=5)
    translation_naturalness: int = Field(ge=0, le=5)
    reader_clarity: int = Field(ge=0, le=5)
    fan_engagement: int = Field(ge=0, le=5)
    card_flow: int = Field(ge=0, le=5)
    safety_respect: int = Field(ge=0, le=5)
    safety_blocker: bool
    safety_category: (
        Literal[
            "hate_or_discrimination",
            "sexual_content",
            "minor_sexualization",
            "harassment_or_threat",
            "violence_glorification",
            "other",
        ]
        | None
    )
    feedback: str = ""
    image_relevance: int = Field(default=5, ge=0, le=5)
    image_composition: int = Field(default=5, ge=0, le=5)
    visual_feedback: str = ""

    @model_validator(mode="after")
    def require_safety_category_for_blocker(self) -> CardLanguageReview:
        if self.safety_blocker and self.safety_category is None:
            raise ValueError("안전 차단 시 safety_category가 필요합니다.")
        return self


class DetailBodyReview(BaseModel):
    naturalness: int = Field(ge=0, le=5)
    factual_support: int = Field(ge=0, le=5)
    coherence: int = Field(ge=0, le=5)
    completeness: int = Field(ge=0, le=5)
    feedback: str = ""


class LanguageReview(BaseModel):
    """독립 검수자가 카드 카피의 언어 품질을 평가한 결과."""

    content_type_fit: int = Field(ge=0, le=5)
    content_type_feedback: str = ""
    cards: list[CardLanguageReview] = Field(min_length=2, max_length=10)
    summary: str = ""
    detail_body_review: DetailBodyReview | None = None


class UsageRecord(BaseModel):
    model: str
    stage: str = "llm"
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    elapsed_seconds: float = 0.0


class GenerationResult(BaseModel):
    deck: CopyDeck
    usage: UsageRecord


class DetailArticleDraft(BaseModel):
    """Long-form detail copy, grounded in existing package facts."""

    body: str = Field(min_length=180, max_length=3000)
    fact_ids: list[str] = Field(min_length=1, max_length=40)


class ContentPackage(BaseModel):
    request_id: str
    topic: str
    content_kind: ContentKind
    content_type: str | None = None
    # Ordered work-dictionary IDs; index 0 is the representative work.
    menu_work_ids: list[int] = Field(default_factory=list)
    work_match_evidence: list[WorkMatchEvidence] = Field(default_factory=list)
    audience_region: str = "KR"
    region_policy: str = "kr_first"
    event_start: str | None = None
    event_end: str | None = None
    event_location: str | None = None
    event_country: str | None = None
    event_region: str = "unknown"
    feed_valid_until: str | None = None
    purge_at: str | None = None
    retention_expires_at: str | None = None
    lifecycle: str = "active"
    content_plan_reason: str = ""
    editorial_angle: str = ""
    cover_mode: str = ""
    ending_mode: str = ""
    term_glossary: list[TermTranslation] = Field(default_factory=list)
    sources: list[SourceEvidence] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    images: list[ImageAsset] = Field(default_factory=list)
    cards: list[CardSpec] = Field(default_factory=list)
    detail_body: str = ""
    detail_fact_ids: list[str] = Field(default_factory=list)
    detail_source_ids: list[str] = Field(default_factory=list)
    prior_image_fingerprints: list[str] = Field(default_factory=list)
    status: str = "draft"

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_content_metadata(cls, value):
        if not isinstance(value, dict):
            return value
        if value.get("content_type") == "review_analysis":
            value["content_type"] = "review"
        # A country is the physical location of an event/pop-up, not the country
        # an anime announcement happens to mention or originate from.
        if value.get("content_type") and value.get("content_type") != "event":
            value["event_country"] = None
        country = value.get("event_country")
        if isinstance(country, str) and country.strip():
            aliases = {
                "대한민국": "KR",
                "한국": "KR",
                "korea": "KR",
                "south korea": "KR",
                "republic of korea": "KR",
                "일본": "JP",
                "japan": "JP",
                "日本": "JP",
                "미국": "US",
                "united states": "US",
                "united states of america": "US",
                "usa": "US",
                "u.s.": "US",
                "us": "US",
                "kr": "KR",
                "jp": "JP",
                "canada": "CA",
                "캐나다": "CA",
                "france": "FR",
                "프랑스": "FR",
                "germany": "DE",
                "독일": "DE",
                "taiwan": "TW",
                "대만": "TW",
                "hong kong": "HK",
                "홍콩": "HK",
                "china": "CN",
                "중국": "CN",
                "singapore": "SG",
                "싱가포르": "SG",
                "australia": "AU",
                "호주": "AU",
                "united kingdom": "GB",
                "영국": "GB",
            }
            codes = []
            for part in re.split(r"\s*[/,]\s*", country):
                if not part.strip():
                    continue
                country_part = part.strip()
                code = aliases.get(country_part.casefold())
                if code is None and re.fullmatch(r"[A-Za-z]{2}", country_part):
                    code = country_part.upper()
                if code:
                    codes.append(code)
            value["event_country"] = ",".join(dict.fromkeys(codes)) or None
        return value


class QualityIssue(BaseModel):
    code: str
    message: str
    severity: str = "error"
    page: int | None = None
    owner: str = "editor_in_chief"
