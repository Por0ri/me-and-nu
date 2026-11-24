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
from ani_rag.web_discovery import (
    WebCandidateAssessmentWorker,
    WebDiscoveryRequest,
    WebDiscoveryPlanRepository,
    WebDiscoveryWorker,
    WebSearchGate,
    enqueue_due_web_discovery_plans,
)


@dataclass(frozen=True)
class RecurringCollectionPlan:
    """One bounded URL, structured, or broad-web request on a repeating schedule."""

    plan_id: str
    request: CollectionRequest | StructuredCollectionRequest | WebDiscoveryRequest
    interval_seconds: int
    first_run_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise ValueError("Recurring collection plan needs a stable plan_id")
        if not 60 <= self.interval_seconds <= 31_536_000:
            raise ValueError("Recurring collection interval must be between one minute and one year")
        if self.first_run_at is not None and (
            self.first_run_at.tzinfo is None or self.first_run_at.utcoffset() is None
        ):
            raise ValueError("Recurring collection first_run_at must include a timezone")


class MCPCrawlerAgent:
    """Dispatch one bounded MCP collection item through the matching worker."""

    def __init__(
        self, *, url_worker: CollectionWorker,
        structured_worker: StructuredCollectionWorker | None = None,
        web_discovery_worker: WebDiscoveryWorker | None = None,
        web_candidate_worker: WebCandidateAssessmentWorker | None = None,
        web_discovery_plan_repository: WebDiscoveryPlanRepository | None = None,
        recurring_plans: Sequence[RecurringCollectionPlan] = (),
    ):
        self.url_worker = url_worker
        self.structured_worker = structured_worker
        self.web_discovery_worker = web_discovery_worker
        self.web_candidate_worker = web_candidate_worker
        self.web_discovery_plan_repository = web_discovery_plan_repository
        self.recurring_plans = tuple(recurring_plans)
        self._search_gate = getattr(web_discovery_worker, "search_gate", None) or WebSearchGate(url_worker.queues)
        self._last_scheduled_slots: dict[str, int] = {}
        plan_ids = [plan.plan_id for plan in self.recurring_plans]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("Recurring collection plan IDs must be unique")
        self._validate_plans()

    async def run_url_once(self) -> bool:
        if await self._search_gate.search_is_paused():
            return await self.url_worker.run_once(allow_scheduled_discovery=False)
        return await self.url_worker.run_once()

    async def run_structured_once(self) -> bool:
        return await self.structured_worker.run_once() if self.structured_worker else False

    async def run_web_discovery_once(self) -> bool:
        return await self.web_discovery_worker.run_once() if self.web_discovery_worker else False

    async def run_web_candidate_once(self) -> bool:
        return await self.web_candidate_worker.run_once() if self.web_candidate_worker else False

    async def run_once(self) -> bool:
        """Schedule one due plan, then overlap only independent MCP servers."""
        allow_web_discovery = not await self._search_gate.search_is_paused()
        scheduled = await self._enqueue_due_plans(allow_web_discovery=allow_web_discovery)
        web_plans_scheduled = False
        if (allow_web_discovery and self.web_discovery_worker is not None
                and self.web_discovery_plan_repository is not None):
            web_plans_scheduled = bool(await enqueue_due_web_discovery_plans(
                self.web_discovery_plan_repository, self.url_worker.queues,
            ))
        # Firecrawl backs the URL, web-search, and candidate-read lanes. Keep
        # those calls serial because the shared MCP session is not concurrency-safe.
        # Wikidata uses a separate server/session and can overlap one Firecrawl task.
        structured_task = (
            asyncio.create_task(self.run_structured_once())
            if self.structured_worker is not None else None
        )
        url_worked = await self.run_url_once()
        web_search_worked = await self.run_web_discovery_once() if allow_web_discovery else False
        candidate_worked = await self.run_web_candidate_once()
        structured_worked = await structured_task if structured_task is not None else False
        return scheduled or web_plans_scheduled or url_worked or structured_worked or web_search_worked or candidate_worked

    def _validate_plans(self) -> None:
        registry = self.url_worker.registry
        for plan in self.recurring_plans:
            request = plan.request
            if isinstance(request, WebDiscoveryRequest):
                if self.web_discovery_worker is None:
                    raise ValueError(f"Recurring web plan {plan.plan_id!r} needs a web-discovery worker")
                continue
            source = next((item for item in registry.sources if item.source_id == request.source_id), None)
            if source is None or source.status is not SourceStatus.APPROVED:
                raise ValueError(f"Recurring plan {plan.plan_id!r} must use a source in the approved allowlist")
            if not source.is_collecting:
                raise ValueError(f"Recurring plan {plan.plan_id!r} uses a source that is not activated for collection")
            if isinstance(request, StructuredCollectionRequest) and self.structured_worker is None:
                raise ValueError(f"Recurring plan {plan.plan_id!r} needs a structured MCP adapter")
            if request.content_type not in source.content_types:
                raise ValueError(f"Recurring plan {plan.plan_id!r} uses a content type not approved for its source")
            if not {"search", "read"} <= source.allowed_tools:
                raise ValueError(f"Recurring plan {plan.plan_id!r} requires approved search and read tools")

    async def _enqueue_due_plans(self, *, allow_web_discovery: bool = True) -> bool:
        now_timestamp = time.time()
        now = datetime.now(timezone.utc)
        for plan in self.recurring_plans:
            first_run = plan.first_run_at or datetime.fromtimestamp(0, timezone.utc)
            if now < first_run:
                continue
            slot = int((now_timestamp - first_run.timestamp()) // plan.interval_seconds)
            if self._last_scheduled_slots.get(plan.plan_id) == slot:
                continue
            request = plan.request
            if isinstance(request, WebDiscoveryRequest):
                if not allow_web_discovery:
                    continue
                stable_id = hashlib.sha256(f"{plan.plan_id}:{slot}".encode("utf-8")).hexdigest()[:32]
                payload: dict[str, object] = {
                    "query": request.query.strip(),
                    "query_variants": [item.strip() for item in request.query_variants],
                    "search_run_id": stable_id,
                    "search_query_index": 0,
                    "search_cursor": None,
                    "content_type": request.content_type.value,
                    "menu_work_id": request.menu_work_id,
                    "expected_work_titles": list(request.expected_work_titles),
                    "result_limit": request.result_limit,
                    "top_k": request.top_k,
                    "candidate_limit": request.candidate_limit,
                    "min_retrieval_score": request.min_retrieval_score,
                }
                await self.url_worker.queues.enqueue(QueueItem(
                    job_id=f"job_web_discovery_{stable_id}",
                    queue=QueueName.COLLECTION,
                    status=QueueStatus.PENDING,
                    created_at=now,
                    updated_at=now,
                    dedupe_key=f"recurring_web_discovery:{plan.plan_id}:{slot}",
                    handler_kind="web_discovery",
                    payload=payload,
                ))
                self._last_scheduled_slots[plan.plan_id] = slot
                return True
            is_structured = isinstance(request, StructuredCollectionRequest)
            if not is_structured and not allow_web_discovery:
                continue
            payload: dict[str, object] = {
                "source_id": request.source_id,
                "content_type": request.content_type.value,
                "evidence_type": request.evidence_type.value,
                "query": request.query,
                "language": request.language,
                "result_limit": request.result_limit,
                "refresh_after_seconds": getattr(request, "refresh_after_seconds", 2_592_000),
            }
            if request.seed_menu_work_id is not None:
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
            return True
        return False

    async def run_forever(
        self, *, stop_event: asyncio.Event, poll_interval_seconds: float = 2.0,
    ) -> None:
        await run_worker_forever(
            self.run_once, stop_event=stop_event,
            poll_interval_seconds=poll_interval_seconds, worker_name="mcp_crawler",
        )
