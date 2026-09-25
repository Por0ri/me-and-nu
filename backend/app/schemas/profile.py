from datetime import date

from pydantic import ConfigDict, Field, model_validator

from app.schemas.common import CamelModel


class ProfileResponse(CamelModel):
    user_id: int
    nickname: str
    birth_date: date | None
    email: str | None
    profile_image_id: int | None = None
    profile_image_url: str | None = None
    account_type: str


class ProfilePatchRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str | None = Field(default=None, min_length=1, max_length=30)
    profile_image_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_changes(self) -> "ProfilePatchRequest":
        if not self.model_fields_set:
            raise ValueError("수정할 값을 하나 이상 보내 주세요.")
        if "nickname" in self.model_fields_set:
            if self.nickname is None or not self.nickname.strip():
                raise ValueError("nickname은 빈 값이거나 null일 수 없습니다.")
            self.nickname = self.nickname.strip()
        return self
