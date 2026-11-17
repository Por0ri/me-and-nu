"""Separate queue contracts and state transitions; no worker loop or DB connection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Protocol


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
    async def enqueue(self, item: QueueItem) -> QueueItem:
        """Insert idempotently by dedupe_key where provided."""

    async def claim(self, queue: QueueName, *, now: datetime, handler_kind: str | None = None) -> QueueItem | None:
        """Claim one pending/retryable item without coupling other queue stages."""

    async def save(self, item: QueueItem) -> None:
        """Persist a validated state transition and history event."""

    async def get(self, job_id: str) -> QueueItem | None:
        """Load one queue item by ID."""
