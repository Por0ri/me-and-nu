"""Masked, read-only member contracts; missing history is never fabricated."""

from typing import Literal

from pydantic import AwareDatetime, ConfigDict, Field, field_validator

from app.schemas.common import CamelModel

MemberStatus = Literal["active", "warned", "suspended", "banned", "withdrawn"]
MemberProvider = Literal["local", "kakao", "naver", "apple"]


class AdminMemberQuery(CamelModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=20, ge=1, le=100)
    status: MemberStatus | None = None
    provider: MemberProvider | None = None
    sort: Literal["recent", "oldest"] = "recent"


class AdminMemberSummary(CamelModel):
    id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    nickname: str
    masked_email: str | None
    provider: MemberProvider | None
    domain: str | None
    joined_at: AwareDatetime | None
    last_login_at: AwareDatetime | None
    status: MemberStatus

    @field_validator("id")
    @classmethod
    def bigint_identifier(cls, value: str) -> str:
        if int(value) > 9223372036854775807:
            raise ValueError("member identifier is outside bigint range")
        return value


class AdminMemberListResponse(CamelModel):
    items: list[AdminMemberSummary]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class AdminMemberOnboarding(CamelModel):
    works: list[str]
    genres: list[str]
    themes: list[str]
    content_types: list[str]


class AdminMemberDetail(AdminMemberSummary):
    # Neither nickname history nor administrator notes have a storage source.
    history: None = None
    notes: None = None
    onboarding: AdminMemberOnboarding | None = None
    saved_count: int | None = Field(default=None, ge=0)
    liked_count: int | None = Field(default=None, ge=0)
