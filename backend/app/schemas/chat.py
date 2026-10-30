"""AI 대화 (API-048 세션 생성, API-052 질문 전송, API-053 답변 조회)."""

import uuid
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from app.schemas.common import CamelModel

AI_NOTICE = "AI가 생성한 답변입니다. 부정확한 내용이 포함될 수 있으니 출처와 근거를 확인해 주세요."


class ChatSessionCreate(CamelModel):
    topic_id: int = Field(gt=0)
    anchor_content_id: int | None = Field(default=None, gt=0)


class ChatSessionResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"sessionId": 101, "topicId": 1, "anchorContentId": 301, "aiNotice": AI_NOTICE, "expiresAt": None}})
    session_id: int
    topic_id: int
    anchor_content_id: int | None = None
    ai_notice: str = AI_NOTICE
    expires_at: None = None


class ChatMessageCreate(CamelModel):
    client_message_id: uuid.UUID
    content: str = Field(max_length=500)
    selected_text: str | None = Field(default=None, max_length=2000)

    @field_validator("content", mode="before")
    @classmethod
    def not_blank(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            raise ValueError("질문을 입력해 주세요.")
        return value


class ChatMessageAccepted(CamelModel):
    user_message_id: int
    job_id: int
    status: Literal["processing"] = "processing"


class ChatSource(CamelModel):
    content_id: int
    title: str
    url: str


class ChatJobResult(CamelModel):
    type: Literal["answer", "topicSwitchSuggested"]
    message_id: int
    content: str
    sources: list[ChatSource] = Field(default_factory=list)
    notices: list[dict[str, str]] = Field(default_factory=list)
    context_coverage: Literal["full", "partial", "none"] = "full"
    target_topic_id: int | None = None


class ChatJobError(CamelModel):
    code: str
    message: str


class ChatJobResponse(CamelModel):
    job_id: int
    status: Literal["processing", "completed", "failed"]
    result: ChatJobResult | None = None
    error: ChatJobError | None = None
