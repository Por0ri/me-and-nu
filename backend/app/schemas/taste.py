"""My Page taste edit (temporary): re-pick the onboarding Q1~Q3 answers after signup."""

from pydantic import ConfigDict

from app.schemas.common import CamelModel
from app.schemas.onboarding import CatalogId, ConsumptionType, OnboardingAnswersV2, OnboardingWork


class TasteResponse(CamelModel):
    topic_id: int
    works: list[OnboardingWork]
    preference_tag_ids: list[int]
    consumption_type: ConsumptionType | None


class TasteUpdateRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: CatalogId
    answers: OnboardingAnswersV2


class TasteHintWork(CamelModel):
    entity_id: int
    name: str
    percent: int


class TasteHintTag(CamelModel):
    tag_id: int
    name: str


class TasteHintType(CamelModel):
    answer: ConsumptionType
    percent: int


class TasteHintsResponse(CamelModel):
    """Hints above the edit screens from the posts the member reacted to (taste_hint_service)."""
    model_config = ConfigDict(json_schema_extra={"example": {
        "topicId": 1, "days": 30, "contents": 14, "enough": True,
        "works": [{"entityId": 1, "name": "진격의 거인", "percent": 62}],
        "genres": [{"tagId": 3, "name": "코미디"}], "themes": [{"tagId": 21, "name": "성장"}],
        "types": [{"answer": "ost", "percent": 42}, {"answer": "news", "percent": 20}],
    }})

    topic_id: int
    days: int
    contents: int
    enough: bool
    works: list[TasteHintWork]
    genres: list[TasteHintTag]
    themes: list[TasteHintTag]
    types: list[TasteHintType]
