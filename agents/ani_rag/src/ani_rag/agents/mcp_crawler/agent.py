"""Role boundary for bounded URL and structured MCP collection workers."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from ani_rag.collector import CollectionRequest
from ani_rag.models import RagContentType
from ani_rag.queues import QueueItem, QueueName, QueueStatus
from ani_rag.settings import SourceStatus
from ani_rag.structured_mcp import StructuredCollectionRequest
from ani_rag.workers import CollectionWorker, StructuredCollectionWorker
from ani_rag.workers import run_worker_forever
from ani_rag.web_discovery import WebCandidateAssessmentWorker, WebDiscoveryWorker


@dataclass(frozen=True)
class RecurringCollectionPlan:
    """An operator-defined bounded query to enqueue on a repeating interval."""

    plan_id: str
    request: CollectionRequest | StructuredCollectionRequest
    interval_seconds: int

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise ValueError("Recurring collection plan needs a stable plan_id")
        if self.interval_seconds < 1:
            raise ValueError("Recurring collection interval must be at least one second")


class MCPCrawlerAgent:
    """Dispatch one bounded MCP collection item through the matching worker."""

    def __init__(
        self, *, url_worker: CollectionWorker,
        structured_worker: StructuredCollectionWorker,
        web_discovery_worker: WebDiscoveryWorker | None = None,
        web_candidate_worker: WebCandidateAssessmentWorker | None = None,
        recurring_plans: Sequence[RecurringCollectionPlan] = (),
    ):
        self.url_worker = url_worker
        self.structured_worker = structured_worker
        self.web_discovery_worker = web_discovery_worker
        self.web_candidate_worker = web_candidate_worker
        self.recurring_plans = tuple(recurring_plans)
        self._last_scheduled_slots: dict[str, int] = {}
        plan_ids = [plan.plan_id for plan in self.recurring_plans]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("Recurring collection plan IDs must be unique")
        self._validate_plans()

    async def run_url_once(self) -> bool:
        return await self.url_worker.run_once()

    async def run_structured_once(self) -> bool:
        return await self.structured_worker.run_once()

    async def run_web_discovery_once(self) -> bool:
        return await self.web_discovery_worker.run_once() if self.web_discovery_worker else False

    async def run_web_candidate_once(self) -> bool:
        return await self.web_candidate_worker.run_once() if self.web_candidate_worker else False

    async def run_once(self) -> bool:
        """Schedule due bounded queries, then consume at most one request per adapter."""
        scheduled = await self._enqueue_due_plans()
        url_worked = await self.run_url_once()
        structured_worked = await self.run_structured_once()
        web_search_worked = await self.run_web_discovery_once()
        candidate_worked = await self.run_web_candidate_once()
        return scheduled or url_worked or structured_worked or web_search_worked or candidate_worked

    def _validate_plans(self) -> None:
        registry = self.url_worker.registry
        for plan in self.recurring_plans:
            request = plan.request
            source = next((item for item in registry.sources if item.source_id == request.source_id), None)
            if source is None or source.status is not SourceStatus.APPROVED:
                raise ValueError(f"Recurring plan {plan.plan_id!r} must use a source in the approved allowlist")
            if request.content_type not in source.content_types:
                raise ValueError(f"Recurring plan {plan.plan_id!r} uses a content type not approved for its source")
            if not {"search", "read"} <= source.allowed_tools:
                raise ValueError(f"Recurring plan {plan.plan_id!r} requires approved search and read tools")

    async def _enqueue_due_plans(self) -> bool:
        now_timestamp = time.time()
        now = datetime.now(timezone.utc)
        enqueued = False
        for plan in self.recurring_plans:
            slot = int(now_timestamp // plan.interval_seconds)
            if self._last_scheduled_slots.get(plan.plan_id) == slot:
                continue
            request = plan.request
            is_structured = isinstance(request, StructuredCollectionRequest)
            payload: dict[str, object] = {
                "source_id": request.source_id,
                "content_type": request.content_type.value,
                "evidence_type": request.evidence_type.value,
                "query": request.query,
                "language": request.language,
                "result_limit": request.result_limit,
            }
            if isinstance(request, CollectionRequest) and request.seed_menu_work_id is not None:
                payload["seed_menu_work_id"] = request.seed_menu_work_id
            if isinstance(request, CollectionRequest) and request.generation_query:
                payload["generation_query"] = request.generation_query
            if is_structured:
                payload["property_ids"] = request.property_ids
            stable_id = hashlib.sha256(f"{plan.plan_id}:{slot}".encode("utf-8")).hexdigest()[:24]
            await self.url_worker.queues.enqueue(QueueItem(
                job_id=f"job_collection_{stable_id}",
                queue=QueueName.COLLECTION,
                status=QueueStatus.PENDING,
                created_at=now,
                updated_at=now,
                dedupe_key=f"recurring_collection:{plan.plan_id}:{slot}",
                handler_kind="structured" if is_structured else "url",
                payload=payload,
            ))
            self._last_scheduled_slots[plan.plan_id] = slot
            enqueued = True
        return enqueued

    async def run_forever(
        self, *, stop_event: asyncio.Event, poll_interval_seconds: float = 2.0,
    ) -> None:
        await run_worker_forever(
            self.run_once, stop_event=stop_event,
            poll_interval_seconds=poll_interval_seconds, worker_name="mcp_crawler",
        )
