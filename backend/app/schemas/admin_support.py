"""Read contracts for support records; no reporter identity or write actions."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from app.schemas.common import CamelModel

SupportKind = Literal["all", "content", "service", "bug"]
SupportStatus = Literal["all", "waiting", "reviewing", "completed"]
SupportPeriod = Literal["7d", "30d", "3m", "6m"]
RawSupportStatus = Literal["received", "reviewing", "resolved", "rejected", "answered", "closed"]
DecimalId = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,18}$")]


class AdminSupportQuery(CamelModel):
    kind: SupportKind = "all"
    status: SupportStatus = "all"
    period: SupportPeriod = "7d"
    q: str = Field(default="", max_length=300, exclude=True)
    page: int = Field(default=1, ge=1, le=100_000)
    page_size: int = Field(default=5, ge=1, le=100)


class AdminSupportItem(CamelModel):
    id: str = Field(pattern=r"^[RI]-[1-9][0-9]{0,18}$")
    record_id: DecimalId
    record_type: Literal["report", "inquiry"]
    kind: Literal["content", "service", "bug"]
    title: str | None
    reason_code: Literal["wrong_info", "spoiler", "rights", "inappropriate", "other"] | None
    reason: str | None
    status: Literal["waiting", "reviewing", "completed"]
    raw_status: RawSupportStatus
    received_at: AwareDatetime
    updated_at: AwareDatetime


class AdminSupportStatistics(CamelModel):
    all: int = Field(ge=0)
    waiting: int = Field(ge=0)
    reviewing: int = Field(ge=0)
    completed: int = Field(ge=0)


class AdminSupportPage(CamelModel):
    items: list[AdminSupportItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    period_start: AwareDatetime
    period_end: AwareDatetime
    # Same period/kind/search as the list, before its status tab filter.
    statistics: AdminSupportStatistics


class AdminSupportSource(CamelModel):
    title: str
    url: str


class AdminSupportContent(CamelModel):
    content_id: DecimalId
    title: str
    subtitle: str | None
    category: str | None
    cover_url: str | None
    body: str | None
    source: AdminSupportSource | None
    production_type: Literal["human", "ai", "hybrid"]
    ai_generated: bool


class AdminSupportDetail(AdminSupportItem):
    message: str | None
    target: str | None
    content: AdminSupportContent | None
    # No inquiry-answer persistence exists. An answered status alone is not an answer body.
    answer: None = None
    answer_availability: Literal["unavailable"] = "unavailable"
