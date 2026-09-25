from datetime import datetime

from pydantic import ConfigDict, Field

from app.schemas.common import CamelModel


class ConsentItem(CamelModel):
    type: str
    policy_version: str
    required: bool
    text: str


class ChatRetention(CamelModel):
    days: int | None = None
    status: str = "undecided"


class PoliciesResponse(CamelModel):
    consent_items: list[ConsentItem]
    ai_notice: str
    chat_retention: ChatRetention


class ConsentInput(CamelModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=50)
    policy_version: str = Field(min_length=1, max_length=50)
    agreed: bool


class ConsentHistoryItem(CamelModel):
    type: str
    policy_version: str
    agreed: bool
    agreed_at: datetime | None
    withdrawn_at: datetime | None


class ConsentHistoryResponse(CamelModel):
    items: list[ConsentHistoryItem]


class ConsentPatchRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ConsentInput] = Field(min_length=1)


class ConsentPatchItem(CamelModel):
    type: str
    agreed: bool
    updated_at: datetime


class ConsentPatchResponse(CamelModel):
    items: list[ConsentPatchItem]
