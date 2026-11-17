from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.schemas.common import CamelModel
from app.schemas.onboarding import NICKNAME_MAX, NICKNAME_MIN, nickname_problem


class ProfileResponse(CamelModel):
    user_id: int
    nickname: str
    email: str | None
    profile_image_id: int | None = None
    profile_image_url: str | None = None
    account_type: Literal["user"]


class ProfilePatchRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str | None = Field(default=None, min_length=NICKNAME_MIN, max_length=NICKNAME_MAX,
                                 description="온보딩과 같은 규칙: 앞뒤 공백을 지운 2~20자 한글·영문·숫자 (띄어쓰기·특수문자 제외)")
    profile_image_id: int | None = Field(default=None, gt=0)

    @field_validator("nickname", mode="before")
    @classmethod
    def trim_nickname(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("nickname")
    @classmethod
    def check_nickname(cls, value: str | None) -> str | None:
        if value is not None and (problem := nickname_problem(value)):
            raise ValueError(problem)
        return value

    @model_validator(mode="after")
    def validate_changes(self) -> "ProfilePatchRequest":
        if not self.model_fields_set:
            raise ValueError("수정할 값을 하나 이상 보내 주세요.")
        if "nickname" in self.model_fields_set:
            if self.nickname is None or not self.nickname.strip():
                raise ValueError("nickname은 빈 값이거나 null일 수 없습니다.")
            self.nickname = self.nickname.strip()
        return self
