"""Notice reads and strict, replayable authoring requests."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.schemas.admin_content_input import AdminInputModel, clean_text
from app.schemas.admin_content_review import _VERSION
from app.schemas.common import CamelModel

NoticeStatus = Literal["draft", "scheduled", "published"]
NoticeAction = Literal["save_draft", "publish", "schedule", "cancel_schedule"]


def _timestamp(value):
    if isinstance(value, str):
        if not _VERSION.fullmatch(value) or value.endswith("-00:00"):
            raise ValueError("Use an ISO timestamp with offset and at most six fractional digits")
        if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError("Invalid timezone offset")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        try:
            parsed.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise ValueError("Timestamp must fit the supported UTC range") from None
        return parsed
    return value


class AdminNoticeScope(CamelModel):
    actor_user_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    admin_account_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")

    @field_validator("actor_user_id", "admin_account_id")
    @classmethod
    def bigint(cls, value):
        if int(value) > 9223372036854775807:
            raise ValueError("ID exceeds PostgreSQL bigint")
        return value


class AdminNoticeCapabilities(CamelModel):
    allowed_actions: list[Literal["create"]] = Field(default_factory=list)
    disabled_reason: Literal["permission_denied"] | None = "permission_denied"
    scope: AdminNoticeScope | None = None


class AdminNoticeActions(CamelModel):
    allowed_actions: list[NoticeAction] = Field(default_factory=list)
    disabled_reason: Literal["permission_denied", "terminal_state"] | None = "permission_denied"
    scope: AdminNoticeScope | None = None


class AdminNoticeQuery(CamelModel):
    status: Literal["all", "draft", "scheduled", "published"] = "all"
    q: str = Field(default="", max_length=300, exclude=True)
    page: int = Field(default=1, ge=1, le=100_000)
    page_size: int = Field(default=7, ge=1, le=100)


class AdminNoticeItem(CamelModel):
    id: str
    title: str
    status: NoticeStatus
    scheduled_at: AwareDatetime | None
    published_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    read_count: int = Field(ge=0)


class AdminNoticeStatistics(CamelModel):
    all: int = Field(ge=0)
    draft: int = Field(ge=0)
    scheduled: int = Field(ge=0)
    published: int = Field(ge=0)


class AdminNoticePage(CamelModel):
    items: list[AdminNoticeItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    statistics: AdminNoticeStatistics
    capabilities: AdminNoticeCapabilities = Field(default_factory=AdminNoticeCapabilities)


class AdminNoticeDetail(AdminNoticeItem):
    body: str
    actions: AdminNoticeActions = Field(default_factory=AdminNoticeActions)


class _NoticeRequest(AdminInputModel):
    request_id: UUID

    @field_validator("request_id", mode="before")
    @classmethod
    def uuid(cls, value):
        return UUID(value) if isinstance(value, str) else value


class AdminNoticeCreateRequest(_NoticeRequest):
    action: Literal["save_draft", "publish", "schedule"]
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=5000)
    scheduled_at: AwareDatetime | None = None

    @field_validator("title", "body", mode="before")
    @classmethod
    def trim(cls, value):
        return clean_text(value)

    @field_validator("scheduled_at", mode="before")
    @classmethod
    def timestamp(cls, value):
        return _timestamp(value)

    @model_validator(mode="after")
    def schedule_fields(self):
        if (self.action == "schedule") != (self.scheduled_at is not None):
            raise ValueError("Only scheduling requires scheduledAt")
        return self


class AdminNoticeActionRequest(_NoticeRequest):
    action: NoticeAction
    title: str | None = Field(default=None, min_length=1, max_length=100)
    body: str | None = Field(default=None, min_length=1, max_length=5000)
    scheduled_at: AwareDatetime | None = None
    expected_status: Literal["draft", "scheduled"]
    expected_updated_at: AwareDatetime

    @field_validator("title", "body", mode="before")
    @classmethod
    def trim(cls, value):
        return clean_text(value)

    @field_validator("scheduled_at", "expected_updated_at", mode="before")
    @classmethod
    def timestamp(cls, value):
        return _timestamp(value)

    @model_validator(mode="after")
    def action_fields(self):
        if self.action == "cancel_schedule":
            if self.title is not None or self.body is not None or self.scheduled_at is not None:
                raise ValueError("Cancellation cannot edit notice contents")
        elif self.title is None or self.body is None:
            raise ValueError("Provide the complete title and body")
        if (self.action == "schedule") != (self.scheduled_at is not None):
            raise ValueError("Only scheduling requires scheduledAt")
        return self


class AdminNoticeWriteResponse(CamelModel):
    notice_id: str
    status: NoticeStatus
    scheduled_at: AwareDatetime | None
    published_at: AwareDatetime | None
    updated_at: AwareDatetime
    audit_log_id: str
    replayed: bool = False
