"""Separate offline series and prospective content evaluation contracts."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import CamelModel

# Shared by the offline publisher and API reader without loading runtime settings.
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024

Audience = Literal["all", "existing", "new"]
Cohort = Literal["existing", "new"]
Period = Literal["7d", "30d", "3m", "6m"]
EvaluationDataset = Literal["offline", "service"]
Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:-]+$")]
Description = Annotated[str, Field(min_length=1, max_length=2000)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Rank = Annotated[int, Field(strict=True, ge=1, le=10)]


class StrictArtifactModel(CamelModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationConditions(StrictArtifactModel):
    evaluation_data: Description
    dataset_sha256: Digest
    input_conditions: Description
    aggregation_unit: Literal["series", "content"] = "series"
    common_rules: Description
    target_period: Description
    ground_truth: Description
    candidate_policy: Description
    record_source: Description
    cohort_interpretation: Literal["input_scenarios", "disjoint_users"]


class EvaluationModel(StrictArtifactModel):
    model_id: Identifier
    label: Annotated[str, Field(min_length=1, max_length=100)]
    version: Identifier


class ModelCaseRanks(StrictArtifactModel):
    model_id: Identifier
    # A null is a miss, including a target outside the candidate set.
    # The same array position refers to the same held-out case for every model.
    target_ranks: list[Rank | None] = Field(min_length=1, max_length=100_000)


class EvaluationCohort(StrictArtifactModel):
    audience: Cohort
    definition: Description
    case_set_sha256: Digest
    sample_size: Annotated[int, Field(strict=True, ge=1, le=100_000)]
    models: list[ModelCaseRanks] = Field(min_length=2, max_length=16)

    @model_validator(mode="after")
    def aligned_cases(self):
        ids = [model.model_id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate model in cohort")
        if any(len(model.target_ranks) != self.sample_size for model in self.models):
            raise ValueError("all models must evaluate the same case count")
        return self


class RecommendationEvaluationArtifact(StrictArtifactModel):
    schema_version: Literal[1, 2]
    evaluation_kind: EvaluationDataset = "offline"
    data_source: Literal["production", "local_demo"] | None = None
    evaluation_id: Identifier
    evaluated_at: AwareDatetime
    k: Literal[10]
    ground_truth_per_case: Literal[1]
    split: Literal["held_out", "prospective"]
    baseline_model_id: Identifier
    current_model_id: Identifier
    conditions: EvaluationConditions
    models: list[EvaluationModel] = Field(min_length=2, max_length=16)
    cohorts: list[EvaluationCohort] = Field(min_length=1, max_length=2)

    @field_validator("schema_version", "k", "ground_truth_per_case", mode="before")
    @classmethod
    def strict_protocol_integers(cls, value):
        if type(value) is not int:
            raise ValueError("protocol constants must be JSON integers")
        return value

    @model_validator(mode="after")
    def complete_comparison(self):
        if self.evaluation_kind == "service":
            if (self.data_source is None or self.schema_version != 2 or self.split != "prospective"
                    or self.conditions.aggregation_unit != "content"
                    or self.conditions.cohort_interpretation != "disjoint_users"):
                raise ValueError("service evaluation requires prospective content cases and disjoint users")
        elif self.data_source is not None or self.schema_version != 1 or self.split != "held_out" or self.conditions.aggregation_unit != "series":
            raise ValueError("offline evaluation requires the version 1 held-out series contract")
        ids = [model.model_id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate model definition")
        if self.baseline_model_id not in ids or self.current_model_id not in ids:
            raise ValueError("baseline and current model must be included")
        audiences = [cohort.audience for cohort in self.cohorts]
        if len(audiences) != len(set(audiences)):
            raise ValueError("duplicate audience")
        for cohort in self.cohorts:
            if {model.model_id for model in cohort.models} != set(ids):
                raise ValueError("every cohort must include every comparison model")
        return self


class RecommendationCohortMetadata(CamelModel):
    audience: Cohort
    definition: str
    case_set_sha256: str
    sample_size: int


class RecommendationScore(CamelModel):
    audience: Cohort
    sample_size: int = Field(ge=1)
    ndcg_at10: float = Field(ge=0, le=1)
    hit_rate_at10: float = Field(ge=0, le=1)
    # Absolute score differences, not relative percentages.
    ndcg_delta_vs_baseline: float = Field(ge=-1, le=1)
    hit_rate_delta_vs_baseline: float = Field(ge=-1, le=1)


class RecommendationModelScores(EvaluationModel):
    scores: list[RecommendationScore]


class EvaluationPipelineStatus(StrictArtifactModel):
    """Only aggregate job state; no paths, account identifiers or raw errors."""
    schema_version: Literal[1]
    dataset: Literal["service"]
    state: Literal["running", "published", "waiting", "unchanged", "failed"]
    reason: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")] | None = None
    data_source: Literal["production", "local_demo", "automated_test", "imported"] | None = None
    observation_days: int = Field(strict=True, ge=1, le=30)
    min_samples: int = Field(strict=True, ge=1, le=100_000)
    collected_cases: int = Field(strict=True, ge=0, le=1_000_000_000)
    mature_cases: int = Field(strict=True, ge=0, le=1_000_000_000)
    eligible_cases: int = Field(strict=True, ge=0, le=1_000_000_000)
    cohort_sample_sizes: dict[Cohort, Annotated[int, Field(strict=True, ge=0, le=100_000)]]
    last_evaluation_id: Identifier | None = None
    last_attempt_at: AwareDatetime
    last_success_at: AwareDatetime | None = None
    last_published_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def complete_cohort_counts(self):
        if set(self.cohort_sample_sizes) != {"existing", "new"}:
            raise ValueError("pipeline status must include both cohort counts")
        return self


class AdminRecommendationPerformanceResponse(CamelModel):
    availability: Literal["available", "unavailable"]
    reason: Literal[
        "not_configured", "artifact_missing", "artifact_invalid", "artifact_unreadable",
        "outside_period", "audience_not_evaluated",
    ] | None = None
    audience: Audience
    period: Period
    period_start: AwareDatetime
    period_end: AwareDatetime
    time_basis: Literal["evaluation_completed_at"] = "evaluation_completed_at"
    source: Literal["offline_evaluation", "service_feedback"] = "offline_evaluation"
    data_source: Literal["production", "local_demo"] | None = None
    metric_unit: Literal["ratio"] = "ratio"
    k: Literal[10] = 10
    ground_truth_per_case: Literal[1] = 1
    evaluation_id: str | None = None
    evaluated_at: AwareDatetime | None = None
    baseline_model_id: str | None = None
    current_model_id: str | None = Field(
        default=None,
        description="Reference model in the selected evaluation dataset; conditions define the measured ranking and ground truth.",
    )
    conditions: EvaluationConditions | None = None
    cohorts: list[RecommendationCohortMetadata] = Field(default_factory=list)
    models: list[RecommendationModelScores] = Field(default_factory=list)
    pipeline: EvaluationPipelineStatus | None = None
