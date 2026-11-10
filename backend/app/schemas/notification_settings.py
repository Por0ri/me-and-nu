"""API-068/069: stored in-app preferences, separate from inbox/read state."""

from typing import Self

from pydantic import ConfigDict, Field, StrictBool, model_validator

from app.schemas.common import CamelModel


class NotificationSettingsResponse(CamelModel):
    in_app_enabled: bool
    night_in_app_enabled: bool
    version: int


class NotificationSettingsPatch(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)

    version: int = Field(strict=True, ge=1, le=2147483647)
    in_app_enabled: StrictBool | None = None
    night_in_app_enabled: StrictBool | None = None

    @model_validator(mode="after")
    def require_changes(self) -> Self:
        fields = self.model_fields_set - {"version"}
        if not fields or any(getattr(self, name) is None for name in fields):
            raise ValueError("하나 이상의 알림 설정을 boolean으로 지정해 주세요.")
        return self
