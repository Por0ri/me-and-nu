"""Proposed admin read contract. No HTTP route exposes this contract yet."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator

from app.schemas.common import CamelModel
from app.schemas.feed import ContentSourceResponse

JudgmentStatus = Literal["pending", "confirmed", "needs_review", "failed"]
ContentStatus = Literal["active", "hidden", "pending_review", "deleted"]


class AdminContentQuery(CamelModel):
    mode: Literal["all", "review"] = "all"
    q: str | None = Field(default=None, max_length=300)
    judgment_status: JudgmentStatus | None = None
    status: ContentStatus | None = None
    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=20, ge=1, le=100)

    @field_validator("q", mode="before")
    @classmethod
    def trim_query(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class AdminContentItem(CamelModel):
    content_id: int = Field(gt=0)
    title: str
    judgment_status: JudgmentStatus
    status: ContentStatus
    created_at: datetime
    published_at: datetime | None


class AdminContentListResponse(CamelModel):
    items: list[AdminContentItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class AdminContentDetail(AdminContentItem):
    # The DB datetime is the version. Never infer a timezone or substitute a clock.
    updated_at: AwareDatetime = Field(strict=True)
    summary: str | None
    body: str | None
    sources: list[ContentSourceResponse]
