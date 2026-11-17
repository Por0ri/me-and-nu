"""Shared contract for durable, independently claimed pipeline jobs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PipelineLane(StrEnum):
    TIME_SENSITIVE = "time_sensitive"
    EVERGREEN = "evergreen"


class PipelineStage(StrEnum):
    CRAWL = "crawl"
    GENERATE = "generate"
    REVIEW = "review"


class PipelineJobStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RETRY = "retry"
    COMPLETED = "completed"
    RETURNED = "returned"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class PipelineJob:
    """A claimed queue item; payload is stage-specific and provenance stays in DB."""

    job_id: str
    lane: PipelineLane
    stage: PipelineStage
    idempotency_key: str
    request_id: str | None
    status: PipelineJobStatus
    payload: dict[str, Any]
    attempts: int
    available_at: str
    lease_owner: str | None
    lease_expires_at: str | None

    @classmethod
    def from_row(cls, row: Any) -> "PipelineJob":
        return cls(
            job_id=row["job_id"],
            lane=PipelineLane(row["lane"]),
            stage=PipelineStage(row["stage"]),
            idempotency_key=row["idempotency_key"],
            request_id=row["request_id"],
            status=PipelineJobStatus(row["status"]),
            payload=json.loads(row["payload_json"]),
            attempts=int(row["attempts"]),
            available_at=row["available_at"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
        )
