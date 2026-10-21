from datetime import date
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from app.schemas.common import CamelModel
from app.schemas.policy import ConsentInput


class OnboardingAnswers(CamelModel):
    """Required single choices for Q1/Q2 at onboarding completion.

    Choice IDs match the current UI and are not topic/subtopic database IDs.
    """

    model_config = ConfigDict(extra="forbid")

    q1: Literal["world", "relationships", "ost", "goods", "events", "news"] = Field(
        description="Q1에서 정확히 1개 선택한 값. 배열·미선택은 허용하지 않습니다."
    )
    q2: Literal["releases", "characters", "restock", "events"] = Field(
        description="Q2에서 정확히 1개 선택한 값. 배열·미선택은 허용하지 않습니다."
    )


class OnboardingRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str = Field(min_length=1, max_length=30)
    birth_date: date
    account_type: Literal["user"]
    consents: list[ConsentInput] = Field(min_length=1)
    topic_id: int = Field(gt=0)
    answers: OnboardingAnswers

    @field_validator("nickname", mode="before")
    @classmethod
    def trim_nickname(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class OnboardingUser(CamelModel):
    id: int
    account_type: Literal["user"]


class ActiveTopic(CamelModel):
    id: int
    code: str
    name: str
    subtopic_ids: list[int]


class OnboardingResponse(CamelModel):
    user: OnboardingUser
    onboarding_completed: bool
    active_topic: ActiveTopic | None
    answers: OnboardingAnswers
