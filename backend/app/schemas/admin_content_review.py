"""Authenticated administrator review request and committed-result contract."""

from datetime import datetime
import re
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, field_validator

from app.schemas.admin_content_input import AdminInputModel, clean_text
from app.schemas.common import CamelModel

ReviewAction = Literal["approve", "reject"]
_VERSION = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")


class AdminContentReviewScope(CamelModel):
    """Expected identity of the original action, never an authorization grant."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    actor_user_id: int = Field(strict=True, gt=0, le=9223372036854775807)
    admin_account_id: int = Field(strict=True, gt=0, le=9223372036854775807)


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
        # Do not silently truncate fractional seconds or accept an unknown offset.
        if isinstance(value, str):
            if not _VERSION.fullmatch(value) or value.endswith("-00:00"):
                raise ValueError("Use an ISO timestamp with an offset and at most six fractional digits")
            if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
                raise ValueError("Invalid timezone offset")
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class AdminContentReviewResponse(CamelModel):
    content_id: int
    draft_id: int
    action: ReviewAction
    status: Literal["active", "hidden"]
    judgment_status: Literal["confirmed", "failed"]
    draft_status: Literal["published", "discarded"]
    published_at: AwareDatetime | None
    updated_at: AwareDatetime
    audit_log_id: int = Field(gt=0)
    replayed: bool = False
