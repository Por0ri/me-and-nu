"""One-item database workers plus an explicit, opt-in polling loop utility."""

from __future__ import annotations

import uuid
import asyncio
import logging
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Protocol, Sequence

from ani_rag.content import ContentDraft, GenerationService, ImageAsset
from ani_rag.collector import CollectionRequest, collect_from_source
from ani_rag.entity_linking import EntityCatalogRecord
from ani_rag.adapter import MCPReadSearchAdapter
from ani_rag.interfaces import EvidenceRepository
from ani_rag.image_ingest import ImageAssetRepository
from ani_rag.models import EvidenceDocument, EvidenceType, RagContentType
from ani_rag.queues import QueueItem, QueueName, QueueRepository, QueueStatus
from ani_rag.agents.content_generation.renderer import RenderedCard, render_text_free_cards
from ani_rag.agents.content_generation.layout_director import ImageCopyMismatchError
from ani_rag.review import IndependentReviewer, ReviewDecision, ReviewInput, ReviewResult, ReviewRoute, route_review
from ani_rag.retrieval import (
    EvidenceChunk,
    EvidenceRetriever,
    GenerationBacklogRepository,
    GenerationWorkStatus,
    GenerationWorkRequest,
    RetrievalRequest,
    RetrievedEvidence,
)
from ani_rag.settings import EvergreenSourceRegistry
from ani_rag.interfaces import WorkCatalogResolver
from ani_rag.structured_mcp import StructuredCollectionRequest, StructuredMCPAdapter, collect_structured_items
from ani_rag.profiles import get_content_profile

logger = logging.getLogger(__name__)


async def run_worker_forever(
    step: Callable[[], Awaitable[bool]],
    *,
    stop_event: asyncio.Event,
    poll_interval_seconds: float = 2.0,
    worker_name: str,
) -> None:
    """Poll one independent worker until stopped; construction never starts it."""
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be greater than zero")
    while not stop_event.is_set():
        try:
            did_work = await step()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s worker loop iteration failed", worker_name)
            did_work = False
        if did_work or stop_event.is_set():
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)
        except TimeoutError:
            pass


@dataclass(frozen=True)
class DraftReviewContext:
    draft: ContentDraft
    evidence: dict[str, EvidenceDocument]
    images: dict[str, ImageAsset]
    retrieved_chunks: dict[str, EvidenceChunk]
    retrieval_id: str
    rendered_cards: dict[str, bytes]


class DraftRepository(Protocol):
    async def save(
        self, draft: ContentDraft, retrieval: RetrievedEvidence,
        rendered_cards: Sequence[RenderedCard],
    ) -> None: ...
    async def load_for_review(self, draft_id: str) -> DraftReviewContext: ...
    async def set_status(self, draft_id: str, status: str) -> None: ...


class ReviewHistoryRepository(Protocol):
    async def save_result(self, result: ReviewResult, revision_count: int, reviewed_at: datetime) -> None: ...


class ImageReader(Protocol):
    async def get_many(self, asset_ids: Sequence[str]) -> dict[str, ImageAsset]: ...
    async def candidates_for(self, evidence: Sequence[EvidenceDocument]) -> Sequence[ImageAsset]: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_job(queue: QueueName, payload: dict[str, object], dedupe_key: str, handler_kind: str | None = None) -> QueueItem:
    now = _utcnow()
    return QueueItem(
        job_id=f"job_{uuid.uuid4().hex}", queue=queue, status=QueueStatus.PENDING,
        created_at=now, updated_at=now, dedupe_key=dedupe_key, payload=payload,
        handler_kind=handler_kind,
    )


async def _record_failure(queues: QueueRepository, job: QueueItem, stage: str, error: Exception) -> QueueStatus:
    status = QueueStatus.RETRY_WAIT if job.retry_count < job.max_retries else QueueStatus.FAILED
    await queues.save(job.transition(status, now=_utcnow(), error=f"{stage}_error:{type(error).__name__}"))
    return status


class GenerationWorker:
    """Retrieves related stored evidence, then queues deterministic-clean drafts for review."""

    def __init__(self, queues: QueueRepository, backlog: GenerationBacklogRepository,
                 retriever: EvidenceRetriever, images: ImageReader, drafts: DraftRepository,
                 generation: GenerationService):
        self.queues, self.backlog, self.retriever = queues, backlog, retriever
        self.images, self.drafts, self.generation = images, drafts, generation

    async def run_once(self) -> bool:
        job = await self.queues.claim(QueueName.GENERATION, now=_utcnow())
        if job is None:
            return False
        payload = dict(job.payload or {})
        instructions = tuple(str(value) for value in payload.get("revision_instructions", ()))
        try:
            retrieval_request = RetrievalRequest(
                query=str(payload["query"]),
                content_type=RagContentType(str(payload["content_type"])),
                menu_work_ids=tuple(int(value) for value in payload.get("menu_work_ids", ())),
                seed_evidence_ids=tuple(str(value) for value in payload.get("seed_evidence_ids", ())),
                top_k=int(payload.get("top_k", 8)),
                candidate_limit=int(payload.get("candidate_limit", 100)),
            )
            profile = get_content_profile(retrieval_request.content_type)
            if profile.work_identity_priority == "required_before_promotion" and not retrieval_request.menu_work_ids:
                error = "required_work_identity_unresolved"
                work_id = str(payload.get("backlog_work_id", ""))
                if work_id:
                    await self.backlog.mark_generation_result(
                        work_id, GenerationWorkStatus.BLOCKED, error=error,
                    )
                await self.queues.save(
                    job.transition(QueueStatus.BLOCKED, now=_utcnow(), error=error)
                )
                logger.warning(
                    "generation_blocked job_id=%s content_type=%s reason=%s",
                    job.job_id, retrieval_request.content_type.value, error,
                )
                return True
            retrieved = await self.retriever.retrieve(retrieval_request)
            if not retrieved.documents or not retrieved.chunks:
                work_id = str(payload.get("backlog_work_id", ""))
                if work_id:
                    await self.backlog.mark_generation_result(work_id, GenerationWorkStatus.WAITING_FOR_EVIDENCE)
                await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_utcnow(), error="waiting_for_retrieval_evidence"))
                return True
            supplied_evidence = retrieved.documents
            candidates = await self.images.candidates_for(supplied_evidence)
            if len(candidates) < 3:
                work_id = str(payload.get("backlog_work_id", ""))
                if work_id:
                    await self.backlog.mark_generation_result(
                        work_id, GenerationWorkStatus.WAITING_FOR_EVIDENCE,
                        error="waiting_for_three_work_linked_image_assets",
                    )
                await self.queues.save(job.transition(
                    QueueStatus.BLOCKED, now=_utcnow(),
                    error="waiting_for_three_work_linked_image_assets",
                ))
                logger.info(
                    "generation_blocked job_id=%s reason=waiting_for_three_work_linked_image_assets candidate_count=%d",
                    job.job_id, len(candidates),
                )
                return True
            draft = await self.generation.generate(
                retrieved,
                image_candidates=candidates, revision_instructions=instructions,
            )
            image_ids = tuple(dict.fromkeys(card.image_asset_id for card in draft.cards))
            image_assets = await self.images.get_many(image_ids)
            rendered_cards = render_text_free_cards(draft, image_assets)
            await self.drafts.save(draft, retrieved, rendered_cards)
            revision_count = int(payload.get("revision_count", 0))
            work_id = str(payload.get("backlog_work_id", ""))
            await self.queues.enqueue(_new_job(QueueName.REVIEW, {
                "draft_id": draft.draft_id,
                "revision_count": revision_count,
                "query": retrieval_request.query,
                "content_type": retrieval_request.content_type.value,
                "menu_work_ids": retrieval_request.menu_work_ids,
                "seed_evidence_ids": retrieval_request.seed_evidence_ids,
                "top_k": retrieval_request.top_k,
                "candidate_limit": retrieval_request.candidate_limit,
                "backlog_work_id": work_id,
            }, f"review:{draft.draft_id}:{revision_count}"))
            if work_id:
                await self.backlog.mark_generation_result(work_id, GenerationWorkStatus.DRAFTED)
            await self.queues.save(job.transition(QueueStatus.SUCCEEDED, now=_utcnow()))
            logger.info("generation_complete job_id=%s draft_id=%s content_type=%s",
                        job.job_id, draft.draft_id, draft.content_type.value)
        except ImageCopyMismatchError as error:
            reason = f"image_subject_match_required:{str(error)[:240]}"
            work_id = str(payload.get("backlog_work_id", ""))
            if work_id:
                await self.backlog.mark_generation_result(
                    work_id, GenerationWorkStatus.BLOCKED, error=reason,
                )
            await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_utcnow(), error=reason))
            logger.warning("generation_blocked job_id=%s reason=%s", job.job_id, reason)
        except Exception as error:
            if isinstance(error, sqlite3.IntegrityError):
                logger.error(
                    "generation_persistence_integrity_error job_id=%s detail=%s",
                    job.job_id, str(error)[:300],
                )
            final_status = await _record_failure(self.queues, job, "generation", error)
            work_id = str(payload.get("backlog_work_id", ""))
            if work_id and final_status is QueueStatus.FAILED:
                await self.backlog.mark_generation_result(work_id, GenerationWorkStatus.FAILED, error=f"generation_error:{type(error).__name__}")
        return True


class CollectionWorker:
    """Consumes only configured source IDs; runtime adapters must be explicitly injected."""

    def __init__(self, queues: QueueRepository, registry: EvergreenSourceRegistry,
                 adapter: MCPReadSearchAdapter, evidence: EvidenceRepository,
                 work_catalog: WorkCatalogResolver | None = None,
                 image_repository: ImageAssetRepository | None = None,
                 image_asset_dir: Path | None = None,
                 image_entity_catalog: tuple[EntityCatalogRecord, ...] = ()):
        self.queues, self.registry, self.adapter = queues, registry, adapter
        self.evidence, self.work_catalog = evidence, work_catalog
        self.image_repository, self.image_asset_dir = image_repository, image_asset_dir
        self.image_entity_catalog = image_entity_catalog

    async def run_once(self) -> bool:
        job = await self.queues.claim(QueueName.COLLECTION, now=_utcnow(), handler_kind="url")
        if job is None:
            return False
        payload = dict(job.payload or {})
        try:
            request = CollectionRequest(
                source_id=str(payload["source_id"]),
                content_type=RagContentType(str(payload["content_type"])),
                evidence_type=EvidenceType(str(payload["evidence_type"])),
                query=str(payload["query"]),
                language=str(payload["language"]),
                result_limit=int(payload.get("result_limit", 10)),
                seed_menu_work_id=(
                    int(payload["seed_menu_work_id"])
                    if payload.get("seed_menu_work_id") is not None else None
                ),
                generation_query=(
                    str(payload["generation_query"])
                    if payload.get("generation_query") is not None else None
                ),
            )
            result = await collect_from_source(
                request, registry=self.registry, adapter=self.adapter,
                repository=self.evidence, work_catalog=self.work_catalog,
                image_repository=self.image_repository, image_asset_dir=self.image_asset_dir,
                image_entity_catalog=self.image_entity_catalog,
            )
            logger.info(
                "collection_complete source_id=%s retrieved=%d stored=%d duplicates=%d "
                "image_candidates=%d images_downloaded=%d images_reused=%d image_failures=%d stage_timings_ms=%s",
                result.source_id, result.retrieved, len(result.stored), result.duplicate_documents,
                result.image_candidates, result.images_downloaded, result.images_reused,
                result.image_failures, dict(result.stage_timings_ms),
            )
            await self.queues.save(job.transition(QueueStatus.SUCCEEDED, now=_utcnow()))
        except Exception as error:
            retries = job.retry_count
            rate_limited = bool(getattr(error, "rate_limited", False))
            http_status = _mcp_http_status(error)
            rate_limited = rate_limited or http_status == 429
            operator_action_required = bool(getattr(error, "operator_action_required", False)) or http_status in {401, 403}
            status = (
                QueueStatus.BLOCKED if rate_limited or operator_action_required else
                QueueStatus.RETRY_WAIT if retries < job.max_retries else
                QueueStatus.FAILED
            )
            detail = getattr(error, "safe_summary", None)
            if not isinstance(detail, str):
                if http_status is not None:
                    category = (
                        "rate_limited" if http_status == 429 else
                        "operator_action_required" if http_status in {401, 403} else
                        "http_error"
                    )
                    detail = f"mcp_endpoint_{category} status={http_status}"
                else:
                    detail = type(error).__name__
            failure = f"collection_error:{detail}"[:420]
            await self.queues.save(job.transition(status, now=_utcnow(), error=failure))
            logger.error("collection_failed job_id=%s status=%s error=%s", job.job_id, status.value, failure)
        return True


def _mcp_http_status(error: Exception) -> int | None:
    """Extract only the HTTP status from MCP transport errors; never persist response bodies."""
    import re
    match = re.search(r"MCP endpoint returned HTTP\s+(\d{3})\b", str(error), re.IGNORECASE)
    return int(match.group(1)) if match else None


class StructuredCollectionWorker:
    """Separate collection worker for entity/property MCP APIs; never treats them as URL scrapers."""

    def __init__(self, queues: QueueRepository, registry: EvergreenSourceRegistry,
                 adapter: StructuredMCPAdapter, evidence: EvidenceRepository,
                 work_catalog: WorkCatalogResolver | None = None,
                 image_repository: ImageAssetRepository | None = None,
                 image_asset_dir: Path | None = None,
                 image_entity_catalog: tuple[EntityCatalogRecord, ...] = ()):
        self.queues, self.registry, self.adapter = queues, registry, adapter
        self.evidence, self.work_catalog = evidence, work_catalog
        self.image_repository, self.image_asset_dir = image_repository, image_asset_dir
        self.image_entity_catalog = image_entity_catalog

    async def run_once(self) -> bool:
        job = await self.queues.claim(QueueName.COLLECTION, now=_utcnow(), handler_kind="structured")
        if job is None:
            return False
        payload = dict(job.payload or {})
        try:
            request = StructuredCollectionRequest(
                source_id=str(payload["source_id"]),
                content_type=RagContentType(str(payload["content_type"])),
                evidence_type=EvidenceType(str(payload["evidence_type"])),
                query=str(payload["query"]),
                language=str(payload["language"]),
                property_ids=tuple(str(value) for value in payload["property_ids"]),
                result_limit=int(payload.get("result_limit", 10)),
            )
            stored = await collect_structured_items(
                request, registry=self.registry, adapter=self.adapter,
                repository=self.evidence, work_catalog=self.work_catalog,
                image_repository=self.image_repository, image_asset_dir=self.image_asset_dir,
                image_entity_catalog=self.image_entity_catalog,
            )
            await self.queues.save(job.transition(QueueStatus.SUCCEEDED, now=_utcnow()))
        except Exception as error:
            await _record_failure(self.queues, job, "structured_collection", error)
        return True


class ReviewWorker:
    """Consumes the review queue independently from generation and collection."""

    def __init__(self, queues: QueueRepository, drafts: DraftRepository, history: ReviewHistoryRepository, reviewer: IndependentReviewer):
        self.queues, self.drafts, self.history, self.reviewer = queues, drafts, history, reviewer

    async def run_once(self) -> bool:
        job = await self.queues.claim(QueueName.REVIEW, now=_utcnow())
        if job is None:
            return False
        payload = dict(job.payload or {})
        draft_id = str(payload.get("draft_id", ""))
        if not draft_id:
            await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_utcnow(), error="review_job_missing_draft_id"))
            return True
        try:
            context = await self.drafts.load_for_review(draft_id)
            from ani_rag.quality import validate_carousel_readability
            readability_issues = validate_carousel_readability(context.draft)
            result = await self.reviewer.review(ReviewInput(
                context.draft, context.evidence, context.images, context.retrieved_chunks,
                context.rendered_cards,
            ))
            if result.draft_id != draft_id:
                raise ValueError("Reviewer result draft_id does not match the requested draft")
            if readability_issues and result.decision not in {ReviewDecision.REJECT_ARCHIVE}:
                result = replace(
                    result,
                    decision=ReviewDecision.REVISE,
                    issues=(*result.issues, *readability_issues),
                    revision_instructions=tuple(dict.fromkeys((
                        *result.revision_instructions,
                        *(issue.message for issue in readability_issues),
                    ))),
                )
            revisions = int(payload.get("revision_count", 0))
            await self.history.save_result(result, revisions, _utcnow())
            route = route_review(result, revisions)
            if route is ReviewRoute.READY:
                await self.drafts.set_status(draft_id, "ready_for_downstream")
            elif route is ReviewRoute.GENERATION_QUEUE:
                await self.drafts.set_status(draft_id, "revision_requested")
                await self.queues.enqueue(_new_job(QueueName.GENERATION, {
                    "query": str(payload["query"]),
                    "content_type": str(payload["content_type"]),
                    "menu_work_ids": tuple(int(value) for value in payload.get("menu_work_ids", ())),
                    "seed_evidence_ids": tuple(str(value) for value in payload.get("seed_evidence_ids", ())),
                    "top_k": int(payload.get("top_k", 8)),
                    "candidate_limit": int(payload.get("candidate_limit", 100)),
                    "backlog_work_id": str(payload.get("backlog_work_id", "")),
                    "revision_instructions": result.revision_instructions,
                    "revision_count": revisions + 1,
                }, f"revision:{draft_id}:{revisions + 1}"))
            elif route is ReviewRoute.COLLECTION_QUEUE:
                await self.drafts.set_status(draft_id, "evidence_requested")
                now = _utcnow()
                await self.queues.enqueue(QueueItem(
                    job_id=f"job_{uuid.uuid4().hex}", queue=QueueName.COLLECTION,
                    status=QueueStatus.BLOCKED, created_at=now, updated_at=now,
                    dedupe_key=f"evidence:{draft_id}:{revisions}",
                    payload={"draft_id": draft_id, "evidence_requests": result.evidence_requests},
                ))
            elif route is ReviewRoute.HUMAN_REVIEW:
                await self.drafts.set_status(draft_id, "awaiting_manual_visual_review")
            else:
                await self.drafts.set_status(draft_id, "rejected_archived")
            await self.queues.save(job.transition(QueueStatus.SUCCEEDED, now=_utcnow()))
            logger.info("review_complete job_id=%s draft_id=%s decision=%s route=%s",
                        job.job_id, draft_id, result.decision.value, route.value)
        except Exception as error:
            await _record_failure(self.queues, job, "review", error)
        return True


class GenerationQueueReconciler:
    """Dispatch transactional generation outbox rows with stable idempotency keys."""

    def __init__(self, backlog: GenerationBacklogRepository, queues: QueueRepository):
        self.backlog, self.queues = backlog, queues

    async def run_once(self, *, limit: int = 50) -> int:
        if not 1 <= limit <= 500:
            raise ValueError("Reconciliation limit must be between 1 and 500")
        work_items = await self.backlog.pending_generation_work(limit=limit)
        dispatched = 0
        for item in work_items:
            queue_item = await self.queues.enqueue(_generation_job(item))
            await self.backlog.mark_generation_enqueued(item.work_id, queue_item.job_id)
            dispatched += 1
        return dispatched


def _generation_job(item: GenerationWorkRequest) -> QueueItem:
    return _new_job(
        QueueName.GENERATION,
        {
            "query": item.query,
            "content_type": item.content_type.value,
            "menu_work_ids": item.menu_work_ids,
            "seed_evidence_ids": item.seed_evidence_ids,
            "top_k": item.top_k,
            "candidate_limit": item.candidate_limit,
            "backlog_work_id": item.work_id,
        },
        f"generation:{item.work_id}",
    )
