import re
from typing import Annotated, Literal

from pydantic import ConfigDict, Discriminator, Field, Tag, field_validator

from app.schemas.common import CamelModel
from app.schemas.policy import ConsentInput

ConsumptionType = Literal["world", "relationships", "ost", "goods", "events", "news"]
CatalogId = Annotated[int, Field(strict=True, gt=0, le=9223372036854775807)]

# Onboarding nickname (2026-11 design): 2~20 Korean (jamo too), English or digit characters, no spaces or symbols.
# Not being used by another member who finished onboarding is checked by the service.
NICKNAME_MIN, NICKNAME_MAX = 2, 20
NICKNAME_RULE = "닉네임은 2~20자의 한글·영문·숫자만 쓸 수 있습니다."
NICKNAME_TAKEN = "중복된 닉네임입니다."
_NICKNAME = re.compile(r"[0-9A-Za-z가-힣ㄱ-ㅣ]+")


def nickname_problem(nickname: str) -> str | None:
    """None when the already trimmed nickname follows the rule, otherwise why not."""
    if not NICKNAME_MIN <= len(nickname) <= NICKNAME_MAX or not _NICKNAME.fullmatch(nickname):
        return NICKNAME_RULE
    return None


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


class OnboardingAnswersV2(CamelModel):
    """Explicit work/tag preferences; IDs have set rather than ranking semantics."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    favorite_entity_ids: list[CatalogId] = Field(min_length=1, max_length=5)
    preference_tag_ids: list[CatalogId] = Field(
        min_length=2, description="같은 Topic의 활성 장르와 소재를 각각 1~7개 선택합니다."
    )
    consumption_type: ConsumptionType

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("version은 정수 2여야 합니다.")
        return value

    @field_validator("favorite_entity_ids", "preference_tag_ids")
    @classmethod
    def unique_ids(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("같은 ID를 중복 선택할 수 없습니다.")
        return value


def _answer_version(value: object) -> str:
    if isinstance(value, OnboardingAnswersV2):
        return "v2"
    if isinstance(value, dict) and "version" in value:
        return "v2"
    return "v1"


VersionedAnswers = Annotated[
    Annotated[OnboardingAnswers, Tag("v1")] | Annotated[OnboardingAnswersV2, Tag("v2")],
    Discriminator(_answer_version),
]


class OnboardingRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str = Field(min_length=NICKNAME_MIN, max_length=NICKNAME_MAX,
                          description="앞뒤 공백을 지운 2~20자 한글·영문·숫자 (띄어쓰기·특수문자 제외)")
    account_type: Literal["user"]
    consents: list[ConsentInput] = Field(min_length=1)
    topic_id: CatalogId
    answers: VersionedAnswers

    @field_validator("answers", mode="before")
    @classmethod
    def validate_answer_version(cls, value: object) -> object:
        # Validate the selected branch before union parsing, so public error
        # locations remain answers.favoriteEntityIds instead of answers.v2.*.
        if isinstance(value, (OnboardingAnswers, OnboardingAnswersV2)):
            return value
        model = OnboardingAnswersV2 if _answer_version(value) == "v2" else OnboardingAnswers
        return model.model_validate(value)

    @field_validator("nickname", mode="before")
    @classmethod
    def trim_nickname(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("nickname")
    @classmethod
    def check_nickname(cls, value: str) -> str:
        if problem := nickname_problem(value):
            raise ValueError(problem)
        return value


class NicknameCheckResponse(CamelModel):
    nickname: str = Field(description="앞뒤 공백을 지운 닉네임")
    available: bool
    reason: Literal["invalid", "taken"] | None = Field(
        default=None, description="invalid = 2~20자 한글·영문·숫자 규칙 위반, taken = 온보딩을 마친 다른 회원이 사용 중")


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
    answers: VersionedAnswers


class OnboardingWork(CamelModel):
    entity_id: int
    name: str
    aliases: list[str]
    image_url: str | None
    release_year: int | None
    media_format: str | None
    selectable: bool
    unavailable_reason: Literal["PREFERENCE_TAGS_NOT_READY"] | None = None


class OnboardingWorksResponse(CamelModel):
    items: list[OnboardingWork]
    next_cursor: str | None = None


class PreferenceOptionsRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: CatalogId
    entity_ids: list[CatalogId] = Field(min_length=1, max_length=5)

    @field_validator("entity_ids")
    @classmethod
    def unique_ids(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("같은 ID를 중복 선택할 수 없습니다.")
        return value


class PreferenceOption(CamelModel):
    tag_id: int
    kind: Literal["genre", "theme"]
    code: str
    label: str
    source_entity_ids: list[int]


class PreferenceOptionsResponse(CamelModel):
    entity_ids: list[int]
    genres: list[PreferenceOption]
    themes: list[PreferenceOption]
    min_selections: Literal[2] = 2
    min_genre_selections: Literal[1] = 1
    min_theme_selections: Literal[1] = 1
    max_selections: None = None
    # EXP-33 cap per kind; completion rejects more (onboarding_catalog_service.MAX_TAGS_PER_KIND).
    max_selections_per_kind: Literal[7] = 7
