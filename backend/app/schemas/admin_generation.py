"""Sanitized host snapshot contract; no identifiers, URLs, paths or free text."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AliasGenerator, AwareDatetime, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.schemas.common import CamelModel

MAX_SNAPSHOT_BYTES = 128 * 1024
STALE_AFTER_SECONDS = 180
Count = Annotated[int, Field(ge=0, le=1_000_000_000)]
Money = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,12})?$")]
SourceReason = Literal["source_partial", "source_unavailable", "policy_day_mismatch"]
Reason = Literal[
    "not_configured", "snapshot_missing", "snapshot_unreadable", "snapshot_invalid",
    "snapshot_future", "snapshot_stale", "policy_day_mismatch", "source_partial", "source_unavailable",
]


class StrictSnapshotModel(CamelModel):
    # The file accepts snake_case only; the HTTP response serializes camelCase.
    model_config = ConfigDict(
        strict=True, extra="forbid", populate_by_name=True,
        alias_generator=AliasGenerator(validation_alias=lambda name: name, serialization_alias=to_camel),
    )


class GenerationDaily(StrictSnapshotModel):
    attempts: Annotated[int, Field(ge=0, le=40)] | None
    max_attempts: Literal[40]
    passes: Annotated[int, Field(ge=0, le=20)] | None
    success_target: Literal[20]
    remaining_attempts: Annotated[int, Field(ge=0, le=40)] | None
    remaining_passes: Annotated[int, Field(ge=0, le=20)] | None
    pending_records: Count | None
    duplicate_records: Count | None
    known_estimated_cost_usd: Money | None
    total_estimated_cost_usd: Money | None
    reserved_cost_usd: Money | None
    unknown_cost_jobs: Count | None
    inflight_cost_jobs: Count | None
    article_attempts: Count | None
    rag_attempts: Count | None

    @field_validator("max_attempts", "success_target", mode="before")
    @classmethod
    def integer_policy_limits(cls, value):
        if type(value) is not int:
            raise ValueError("policy limits must be integers")
        return value

    @model_validator(mode="after")
    def consistent_limits_and_cost(self):
        for count, remaining, maximum in (
            (self.attempts, self.remaining_attempts, self.max_attempts),
            (self.passes, self.remaining_passes, self.success_target),
        ):
            if remaining != (None if count is None else maximum - count):
                raise ValueError("inconsistent remaining count")
        if self.attempts is not None and self.passes is not None and self.passes > self.attempts:
            raise ValueError("passes exceed attempts")
        if all(x is not None for x in (self.attempts, self.article_attempts, self.rag_attempts)):
            if self.article_attempts + self.rag_attempts != self.attempts:
                raise ValueError("producer attempts do not match")
        _validate_total(self.known_estimated_cost_usd, self.total_estimated_cost_usd,
                        self.unknown_cost_jobs, self.inflight_cost_jobs)
        return self


def _validate_total(known, total, unknown, inflight=0):
    if total is not None:
        if known is None or unknown != 0 or inflight != 0 or Decimal(total) != Decimal(known):
            raise ValueError("incomplete cost cannot be a total")


class GenerationHistory(StrictSnapshotModel):
    known_estimated_cost_usd: Money | None
    baseline_estimated_cost_usd: Money | None
    total_estimated_cost_usd: Money | None
    unknown_cost_jobs: Count | None

    @model_validator(mode="after")
    def consistent_cost(self):
        _validate_total(self.known_estimated_cost_usd, self.total_estimated_cost_usd, self.unknown_cost_jobs)
        return self


class ProducerJobs(StrictSnapshotModel):
    queued: Count | None
    running: Count | None
    succeeded: Count | None
    failed: Count | None
    needs_attention: Count | None
    cancelled: Count | None
    held: Count | None
    skipped: Count | None
    rejected: Count | None
    ready_for_staging: Count | None


class GenerationJobs(StrictSnapshotModel):
    article: ProducerJobs
    rag: ProducerJobs


class GenerationBroker(StrictSnapshotModel):
    article_queued: Count | None
    rag_queued: Count | None
    unacked: Count | None
    unacked_index: Count | None


class GenerationRuntime(StrictSnapshotModel):
    controller: Literal["active", "activating", "inactive", "failed", "unknown"]
    timer: Literal["active", "inactive", "failed", "unknown"]
    article_worker: Literal["running", "stopped", "missing", "unknown"]
    rag_worker: Literal["running", "stopped", "missing", "unknown"]


class GenerationSnapshot(StrictSnapshotModel):
    schema_version: Literal[1]
    purpose: Literal["generation_observability"]
    generated_at: AwareDatetime
    timezone: Literal["Asia/Seoul"]
    policy_date: date | None
    availability: Literal["available", "partial", "unavailable"]
    reason: SourceReason | None
    daily: GenerationDaily | None
    history: GenerationHistory | None
    jobs: GenerationJobs | None
    broker: GenerationBroker | None
    runtime: GenerationRuntime | None

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_schema_version(cls, value):
        if type(value) is not int:
            raise ValueError("schema version must be an integer")
        return value

    @model_validator(mode="after")
    def consistent_snapshot(self):
        if self.generated_at.utcoffset().total_seconds() != 0:
            raise ValueError("snapshot timestamp must be UTC")
        if self.policy_date is None and (self.daily is not None or self.jobs is not None):
            raise ValueError("daily data requires a policy date")
        sections = (self.daily, self.history, self.jobs, self.broker, self.runtime)
        if self.availability == "unavailable" and any(x is not None for x in sections):
            raise ValueError("unavailable source must not claim current data")
        if self.availability == "available" and (self.reason is not None or any(x is None for x in sections)):
            raise ValueError("available source must supply all sections")
        return self


class AdminGenerationResponse(StrictSnapshotModel):
    availability: Literal["available", "partial", "stale", "unavailable"] = "unavailable"
    reason: Reason | None = None
    observed_at: AwareDatetime
    generated_at: AwareDatetime | None = None
    age_seconds: Count | None = None
    stale_after_seconds: Literal[180] = STALE_AFTER_SECONDS
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    policy_date: date | None = None
    daily: GenerationDaily | None = None
    history: GenerationHistory | None = None
    jobs: GenerationJobs | None = None
    broker: GenerationBroker | None = None
    runtime: GenerationRuntime | None = None
