"""Separate queue contracts and state transitions; no worker loop or DB connection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Protocol

from ani_rag.models import RagContentType


class QueueName(StrEnum):
    COLLECTION = "collection"
    GENERATION = "generation"
    REVIEW = "review"


class QueueStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class WebPipelineBacklog:
    total_active: int
    active_revisions: int


@dataclass(frozen=True)
class QueueItem:
    job_id: str
    queue: QueueName
    status: QueueStatus = QueueStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    dedupe_key: str | None = None
    payload: Mapping[str, object] | None = None
    handler_kind: str | None = None

    def transition(self, status: QueueStatus, *, now: datetime, error: str | None = None) -> "QueueItem":
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Queue timestamps must be timezone-aware")
        allowed = {
            QueueStatus.PENDING: {QueueStatus.RUNNING, QueueStatus.CANCELLED, QueueStatus.BLOCKED},
            QueueStatus.RETRY_WAIT: {QueueStatus.RUNNING, QueueStatus.CANCELLED, QueueStatus.BLOCKED},
            QueueStatus.RUNNING: {QueueStatus.SUCCEEDED, QueueStatus.RETRY_WAIT, QueueStatus.FAILED, QueueStatus.BLOCKED},
            QueueStatus.BLOCKED: {QueueStatus.PENDING, QueueStatus.CANCELLED},
            QueueStatus.SUCCEEDED: set(),
            QueueStatus.FAILED: set(),
            QueueStatus.CANCELLED: set(),
        }
        if status not in allowed[self.status]:
            raise ValueError(f"Invalid queue transition: {self.status.value} -> {status.value}")
        retries = self.retry_count + int(status is QueueStatus.RETRY_WAIT)
        if status is QueueStatus.RETRY_WAIT and retries > self.max_retries:
            raise ValueError("Retry limit reached; mark failed or blocked")
        return replace(self, status=status, retry_count=retries, last_error=error, updated_at=now)


class QueueRepository(Protocol):
    async def web_evidence_revision(
        self, menu_work_id: int, content_type: RagContentType, source_ids: tuple[str, ...],
    ) -> str | None:
        """Stable fingerprint of eligible, matched evidence for the requested work and lane."""
        ...

    async def enqueue(self, item: QueueItem) -> QueueItem:
        """Insert idempotently by dedupe_key where provided."""

    async def count_active(self, queue: QueueName, *, handler_kind: str | None = None) -> int:
        """Count pending, running, and retry-wait items for queue backpressure."""

    async def web_pipeline_backlog(self) -> WebPipelineBacklog:
        """Count active candidate, generation and review jobs, including their revision subset."""

    async def claim(
        self, queue: QueueName, *, now: datetime, handler_kind: str | None = None,
        allow_scheduled_discovery: bool = True,
    ) -> QueueItem | None:
        """Claim one pending/retryable item without coupling other queue stages."""

    async def save(self, item: QueueItem) -> None:
        """Persist a validated state transition and history event."""

    async def get(self, job_id: str) -> QueueItem | None:
        """Load one queue item by ID."""
