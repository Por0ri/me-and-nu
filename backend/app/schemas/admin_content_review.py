"""Proposed review contract; no runtime route is registered."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator

from app.schemas.admin_content_input import AdminInputModel, clean_text
from app.schemas.common import CamelModel

ReviewAction = Literal["approve", "reject"]


class AdminContentReviewRequest(AdminInputModel):
    request_id: UUID
    action: ReviewAction
    rationale: str = Field(min_length=1, max_length=2000)
    expected_status: Literal["active", "pending_review"]
    expected_judgment_status: Literal["needs_review"]
    expected_updated_at: AwareDatetime

    @field_validator("rationale", mode="before")
    @classmethod
    def trim_rationale(cls, value):
        return clean_text(value)

    @field_validator("request_id", mode="before")
    @classmethod
    def parse_id(cls, value):
        return UUID(value) if isinstance(value, str) else value

    @field_validator("expected_updated_at", mode="before")
    @classmethod
    def parse_timestamp(cls, value):
        # Accept ISO JSON strings, not numeric timestamps or coercions.
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


class AdminContentReviewResponse(CamelModel):
    content_id: int
    draft_id: int
    action: ReviewAction
    status: Literal["active", "hidden"]
    judgment_status: Literal["confirmed", "failed"]
    draft_status: Literal["published", "discarded"]
    published_at: datetime | None
    updated_at: datetime
    audit_log_id: int = Field(gt=0)
    replayed: bool = False
