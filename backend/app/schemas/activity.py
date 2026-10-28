"""API-033 activity event batch (POST /users/me/activity-events)."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field

from app.models.recommendation import USER_EVENT_TYPES
from app.schemas.common import CamelModel


RecordId = Annotated[int, Field(strict=True, gt=0, le=9223372036854775807)]


class ActivityEventValue(CamelModel):
    """Raw measurements only; the recommendation batch decides what they mean."""

    model_config = ConfigDict(extra="forbid")

    surface: Literal["home_feed", "related", "saved", "detail"] | None = Field(
        default=None, description="행동이 일어난 화면"
    )
    view_mode: Literal["card", "list"] | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=64, description="브라우저 탐색 세션 ID")
    active_ms: int | None = Field(default=None, strict=True, ge=0, le=86_400_000, description="open: 실제로 보인 시간")
    scroll_pct: float | None = Field(default=None, ge=0, le=100, description="open: 최대 읽은 비율(%)")
    body_chars: int | None = Field(default=None, strict=True, ge=0, le=1_000_000, description="open: 본문 글자 수")
    card_index: int | None = Field(default=None, strict=True, ge=1, le=10, description="card_swipe: 넘긴 뒤 카드 번호(1부터)")
    card_count: int | None = Field(default=None, strict=True, ge=1, le=10, description="card_swipe: 전체 카드 수")
    channel: str | None = Field(default=None, min_length=1, max_length=30, description="share: 공유 경로")
    entity_id: RecordId | None = Field(default=None, description="spoiler_reveal·watched: 작품 번호")


class ActivityEventIn(CamelModel):
    model_config = ConfigDict(extra="forbid")

    client_event_id: UUID = Field(description="프론트가 이벤트마다 만든 UUID. 재전송해도 같은 값")
    event_type: Literal[*USER_EVENT_TYPES]
    topic_id: RecordId
    content_id: RecordId
    recommendation_item_id: RecordId | None = Field(
        default=None, description="추천 피드에서 본 카드면 피드의 recommendationId"
    )
    occurred_at: AwareDatetime = Field(description="행동 시각 (시간대 포함)")
    value: ActivityEventValue = Field(default_factory=ActivityEventValue)


class ActivityEventsRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    events: list[ActivityEventIn] = Field(min_length=1, max_length=100)


class ActivityEventsResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"saved": 3, "duplicated": 1, "rejected": 0}})

    saved: int = Field(description="새로 저장한 이벤트 수")
    duplicated: int = Field(description="이미 저장된 clientEventId라 건너뛴 수")
    rejected: int = Field(description="본인 Tap·공개 콘텐츠·본인 추천 결과가 아니라 저장하지 않은 수")
