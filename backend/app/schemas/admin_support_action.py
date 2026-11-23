"""Strict write contracts; scope headers bind a retry but never authorize it."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator

from app.schemas.admin_content_input import AdminInputModel, clean_text
from app.schemas.admin_content_review import _VERSION
from app.schemas.common import CamelModel

SupportAction = Literal["start_review", "resolve", "reject", "answer"]


class AdminSupportScope(CamelModel):
    actor_user_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    admin_account_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")

    @field_validator("actor_user_id", "admin_account_id")
    @classmethod
    def bigint(cls, value):
        if int(value) > 9223372036854775807:
            raise ValueError("ID exceeds PostgreSQL bigint")
        return value


class AdminSupportActions(CamelModel):
    allowed_actions: list[SupportAction] = Field(default_factory=list)
    disabled_reason: Literal["permission_denied", "terminal_state"] | None = "permission_denied"
    scope: AdminSupportScope | None = None


class SupportWriteRequest(AdminInputModel):
    request_id: UUID
    expected_updated_at: AwareDatetime

    @field_validator("request_id", mode="before")
    @classmethod
    def uuid(cls, value):
        return UUID(value) if isinstance(value, str) else value

    @field_validator("expected_updated_at", mode="before")
    @classmethod
    def timestamp(cls, value):
        if isinstance(value, str):
            if not _VERSION.fullmatch(value) or value.endswith("-00:00"):
                raise ValueError("Use an ISO timestamp with offset and at most six fractional digits")
            if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
                raise ValueError("Invalid timezone offset")
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class AdminReportActionRequest(SupportWriteRequest):
    action: Literal["start_review", "resolve", "reject"]
    rationale: str = Field(min_length=1, max_length=2000)
    expected_status: Literal["received", "reviewing"]

    @field_validator("rationale", mode="before")
    @classmethod
    def trim(cls, value):
        return clean_text(value)


class AdminInquiryAnswerRequest(SupportWriteRequest):
    answer: str = Field(min_length=1, max_length=5000)
    expected_status: Literal["received"]

    @field_validator("answer", mode="before")
    @classmethod
    def trim(cls, value):
        return clean_text(value)


class AdminSupportActionResponse(CamelModel):
    record_id: str
    record_type: Literal["report", "inquiry"]
    action: SupportAction
    raw_status: Literal["reviewing", "resolved", "rejected", "answered"]
    updated_at: AwareDatetime
    audit_log_id: str
    replayed: bool = False
