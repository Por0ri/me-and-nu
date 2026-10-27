"""Permission-gated, metadata-minimal intake for community humor trend signals."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl

from ani_menu_agent.models import ContentKind, ContentPackage, Fact, SourceEvidence
from ani_menu_agent.sources.registry import load_source_registry


class CommunityTrendInput(BaseModel):
    """Human-reviewed paraphrase; deliberately has no author, post-body, or image fields."""

    source_id: str
    source_url: HttpUrl
    topic: str = Field(min_length=4, max_length=120)
    summary: str = Field(
        min_length=15,
        max_length=300,
        description="게시물 문구를 옮기지 않은 편집자 고유의 트렌드 요약",
    )
    observed_at: datetime | None = None
    menu_work_ids: list[int] = Field(default_factory=list, max_length=10)
    audience_region: str = "KR"


def to_humor_package(signal: CommunityTrendInput) -> ContentPackage:
    """Convert a hand-curated signal into a humor-only package without scraping its URL."""
    registry = load_source_registry()
    source = next(
        (item for item in registry.community_sources if item.id == signal.source_id),
        None,
    )
    if source is None:
        raise ValueError(f"등록된 커뮤니티 출처가 아닙니다: {signal.source_id}")
    if (
        source.role != "trend"
        or source.status != "trend_only"
        or source.collection == "manual"
        or "humor" not in {item.value for item in source.content_types}
    ):
        raise ValueError("자동 수집 승인된 유머 출처만 생성 근거로 사용할 수 있습니다.")

    expected_host = (urlparse(str(source.url)).hostname or "").lower().rstrip(".")
    actual_host = (urlparse(str(signal.source_url)).hostname or "").lower().rstrip(".")
    if actual_host != expected_host and not actual_host.endswith(f".{expected_host}"):
        raise ValueError("참고 링크의 도메인이 등록된 커뮤니티 출처와 다릅니다.")

    source_id = source.id
    observed_at = signal.observed_at or datetime.now(UTC)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    request_id = "trend-" + sha256(
        f"{source.id}\n{signal.source_url}\n{signal.topic}".encode()
    ).hexdigest()[:20]

    return ContentPackage(
        request_id=request_id,
        topic=signal.topic,
        content_kind=ContentKind.HUMOR,
        content_type="humor",
        menu_work_ids=signal.menu_work_ids,
        audience_region=signal.audience_region,
        sources=[
            SourceEvidence(
                id=source_id,
                title="커뮤니티 트렌드 참고 링크",
                url=signal.source_url,
                publisher=source.name,
                is_official=False,
                checked_at=observed_at.isoformat(),
                role="trend",
                region=source.region,
            )
        ],
        facts=[
            Fact(
                id="community-trend-summary",
                statement=signal.summary,
                source_ids=[source_id],
                verified=False,
                fact_type="community_trend",
                details={
                    "topic": signal.topic,
                    "observed_at": observed_at.isoformat(),
                    "source_url": str(signal.source_url),
                },
            )
        ],
        status="draft",
    )
