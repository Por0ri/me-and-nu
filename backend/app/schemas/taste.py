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
