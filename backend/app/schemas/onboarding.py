from datetime import date
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.schemas.common import CamelModel
from app.schemas.policy import ConsentInput


class OnboardingRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str = Field(min_length=1, max_length=30)
    birth_date: date
    account_type: Literal["consumer", "creator"]
    consents: list[ConsentInput] = Field(min_length=1)
    profile_image_id: int | None = Field(default=None, gt=0)
    topic_id: int | None = Field(default=None, gt=0)
    subtopic_ids: list[int] | None = None

    @model_validator(mode="after")
    def validate_topic_choice(self) -> "OnboardingRequest":
        self.nickname = self.nickname.strip()
        if not self.nickname:
            raise ValueError("nickname을 입력해 주세요.")
        if self.account_type == "consumer":
            if self.topic_id is None or not self.subtopic_ids:
                raise ValueError("소비자는 Topic과 Subtopic을 하나 이상 선택해야 합니다.")
            if any(value <= 0 for value in self.subtopic_ids):
                raise ValueError("subtopicIds에는 양의 정수만 사용할 수 있습니다.")
            if len(set(self.subtopic_ids)) != len(self.subtopic_ids):
                raise ValueError("subtopicIds에 중복이 있습니다.")
        elif self.topic_id is not None or self.subtopic_ids is not None:
            raise ValueError("크리에이터 온보딩에는 Topic 선택값을 보내지 않습니다.")
        return self


class OnboardingUser(CamelModel):
    id: int
    account_type: str


class ActiveTopic(CamelModel):
    id: int
    code: str
    name: str
    subtopic_ids: list[int]


class OnboardingResponse(CamelModel):
    user: OnboardingUser
    onboarding_completed: bool
    active_topic: ActiveTopic | None
