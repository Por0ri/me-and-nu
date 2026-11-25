"""토론방 API 요청·응답 (설계 docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 7장)."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.debate import DESCRIPTION_MAX, MESSAGE_MAX, SIDE_LABEL_INPUT_MAX, TITLE_MAX, WORKS_MAX
from app.schemas.common import CamelModel

CatalogId = Annotated[int, Field(strict=True, gt=0, le=9223372036854775807)]
DebateStatus = Literal["voting", "open", "closed", "expired"]
DebateSideKey = Literal["a", "b"]
DebateResult = Literal["a", "b", "draw", "none"]


class DebateCreateRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: CatalogId
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    side_a: str = Field(min_length=1, max_length=SIDE_LABEL_INPUT_MAX, description="A 선택지 이름 (예: 나루토)")
    side_b: str = Field(min_length=1, max_length=SIDE_LABEL_INPUT_MAX, description="B 선택지 이름 (예: 사스케)")
    work_ids: list[CatalogId] = Field(min_length=1, max_length=WORKS_MAX, description="관련 작품 1~2개 (필수)")

    @field_validator("title", "side_a", "side_b", mode="before")
    @classmethod
    def trim(cls, value: object) -> object:
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def trim_description(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("work_ids")
    @classmethod
    def unique_works(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("같은 작품을 두 번 고를 수 없습니다.")
        return value

    @model_validator(mode="after")
    def different_sides(self) -> "DebateCreateRequest":
        if self.side_a.casefold() == self.side_b.casefold():
            raise ValueError("양쪽 선택지 이름이 달라야 합니다.")
        return self


class DebateSideView(CamelModel):
    label: str
    count: int
    percent: int | None = Field(description="진영 투표 비율(%). 아무도 고르지 않았으면 null")


class DebateWorkView(CamelModel):
    entity_id: int
    name: str


class DebateSummary(CamelModel):
    debate_id: int
    topic_id: int
    title: str
    description: str | None
    side_a: DebateSideView
    side_b: DebateSideView
    works: list[DebateWorkView]
    status: DebateStatus = Field(description="저장된 상태와 지금 시각으로 계산한 상태")
    vote_count: int
    vote_threshold: int
    voting_ends_at: datetime
    opened_at: datetime | None
    closes_at: datetime | None
    participant_count: int = Field(description="진영을 고른 사람 수")
    member_count: int = Field(description="토론방에 참여 중인 사람 수 (방을 나가면 빠진다)")
    joined: bool = Field(description="내가 이 토론방에 참여 중인지")
    joined_at: datetime | None = Field(description="내가 이 토론방에 참여한 시각. 참여 중이 아니면 null ('참여 중인 방'을 최근에 참여한 순서로 보여 줄 때 쓴다)")
    message_count: int
    result: DebateResult | None = Field(description="종료된 방만: a·b 이긴 쪽, draw 동률, none 참여자 없음")
    my_vote: bool
    my_side: DebateSideKey | None
    is_mine: bool
    author_nickname: str = Field(description="주제를 올린 사람. 탈퇴했거나 지워진 회원이면 '알 수 없음'")
    created_at: datetime


class DebateListResponse(CamelModel):
    items: list[DebateSummary]
    next_cursor: str | None = None


class VoteTicketsResponse(CamelModel):
    balance: int
    max: int
    next_refill_at: datetime | None = Field(description="다음 1개가 차는 시각. 가득 차 있으면 null")


class DebateVoteResponse(CamelModel):
    debate: DebateSummary
    tickets: VoteTicketsResponse
    opened: bool = Field(False, description="이번 표로 기준 표 수를 채워 토론방이 열렸는지")


class DebateSideRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    side: DebateSideKey


class DebateMessageRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=MESSAGE_MAX, description="앞뒤 공백을 지운 1~300자")

    @field_validator("body", mode="before")
    @classmethod
    def trim(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class DebateMessageView(CamelModel):
    message_id: int
    side: DebateSideKey | None = Field(description="보낸 순간의 진영. 진영을 고르지 않고 보냈으면 null")
    nickname: str
    body: str
    created_at: datetime
    is_mine: bool


class DebateMessagesResponse(CamelModel):
    items: list[DebateMessageView] = Field(description="오래된 순")
    next_cursor: str | None = Field(None, description="더 이전 메시지")


class DebateSearchWork(CamelModel):
    entity_id: int
    name: str
    aliases: list[str]


class DebateSearchResponse(CamelModel):
    works: list[DebateSearchWork]
    debates: list[DebateSummary]
