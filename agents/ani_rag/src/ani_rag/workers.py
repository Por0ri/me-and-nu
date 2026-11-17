"""One-item database workers plus an explicit, opt-in polling loop utility."""

from __future__ import annotations

import uuid
import asyncio
import logging
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Protocol, Sequence

from ani_rag.content import ContentDraft, DuplicateContentError, GenerationService, ImageAsset
from ani_rag.collector import CollectionRequest, collect_from_source
from ani_rag.entity_linking import EntityCatalogRecord
from ani_rag.adapter import MCPReadSearchAdapter
from ani_rag.interfaces import EvidenceRepository
from ani_rag.image_ingest import ImageAssetRepository
from ani_rag.image_quality import image_resolution_tier
from ani_rag.models import EvidenceDocument, EvidenceType, RagContentType
from ani_rag.queues import QueueItem, QueueName, QueueRepository, QueueStatus
from ani_rag.agents.content_generation.renderer import RenderedCard, render_text_free_cards
from ani_rag.agents.content_generation.layout_director import ImageCopyMismatchError
from ani_rag.review import IndependentReviewer, ReviewDecision, ReviewInput, ReviewResult, ReviewRoute, route_review
from ani_rag.retrieval import (
    DEFAULT_MIN_RETRIEVAL_SCORE,
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
    async def recent_topics(
        self, menu_work_ids: Sequence[int], *, limit: int = 30,
    ) -> Sequence[dict[str, str]]: ...
    async def find_duplicate_topic(self, draft: ContentDraft) -> str | None: ...
    async def load_for_review(self, draft_id: str) -> DraftReviewContext: ...
    async def set_status(self, draft_id: str, status: str) -> None: ...


class ReviewHistoryRepository(Protocol):
    async def save_result(self, result: ReviewResult, revision_count: int, reviewed_at: datetime) -> None: ...


class ImageReader(Protocol):
    async def get_many(self, asset_ids: Sequence[str]) -> dict[str, ImageAsset]: ...
    async def candidates_for(self, evidence: Sequence[EvidenceDocument]) -> Sequence[ImageAsset]: ...


def _query_person_terms(query: str) -> set[str]:
    query_terms = set(re.findall(r"[0-9a-z\u3040-\u30ff\u3400-\u9fff]+", query.casefold()))
    generic_terms = {
        "image", "type", "visual", "role", "description", "kitsu", "catalog", "character",
        "art", "episode", "still", "profile", "photo", "official", "portrait", "anime",
        "one", "piece", "straw", "hat", "crew", "monkey", "d", "and", "the",
    }
    return query_terms - generic_terms


def _image_names_queried_person(query: str, asset: ImageAsset) -> bool:
    """Match explicit character/person captions from Kitsu or web image results."""
    if not asset.alt_text:
        return False
    text = asset.alt_text.casefold()
    if "depicted:" in text:
        text = text.split("depicted:", 1)[1]
    elif "—" in text:
        # Kitsu captions put the canonical character name after this separator.
        text = text.rsplit("—", 1)[1]
    caption_terms = set(re.findall(r"[0-9a-z\u3040-\u30ff\u3400-\u9fff]+", text))
    return bool(_query_person_terms(query) & caption_terms)


def _named_character_image_candidates(
    query: str, candidates: Sequence[ImageAsset],
) -> tuple[ImageAsset, ...]:
    """Retain every confirmed work-linked image; named art is only a ranking hint."""

    return tuple(candidates)


def _focused_image_query(
    base_query: str, evidence_title: str, mismatch: ImageCopyMismatchError,
) -> str:
    """Turn a failed card into a concise exact-character image search."""
    focus = " ".join((str(mismatch), mismatch.headline or "", mismatch.body or "")).casefold()
    name_pattern = r"\b[A-Z][a-z]+(?:\s+(?:D\.)?\s*[A-Z][a-z]+){0,2}\b"
    names = tuple(dict.fromkeys(re.findall(name_pattern, base_query)))
    matched_name = next((
        name for name in names
        if name.casefold().split()[-1].rstrip(".") in focus
    ), "")
    work_title = next(iter(re.findall(r"\b[A-Z][A-Z0-9]+(?:\s+[A-Z][A-Z0-9]+)+\b", base_query)), "")
    subject = " ".join(part for part in (work_title, matched_name) if part)
    if not subject:
        subject = evidence_title or mismatch.headline or base_query
    return f"{subject} anime artwork memorable portrait same series; exact scene is not required"[:450]


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
                source_ids=tuple(str(value) for value in payload.get("source_ids", ())),
                seed_evidence_ids=tuple(str(value) for value in payload.get("seed_evidence_ids", ())),
                top_k=int(payload.get("top_k", 8)),
                candidate_limit=int(payload.get("candidate_limit", 100)),
                min_retrieval_score=float(
                    payload.get("min_retrieval_score", DEFAULT_MIN_RETRIEVAL_SCORE)
                ),
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
            best_score = max(chunk.score for chunk in retrieved.chunks)
            qualifying_chunks = tuple(
                chunk for chunk in retrieved.chunks
                if chunk.score >= retrieval_request.min_retrieval_score
            )
            if not qualifying_chunks:
                score_error = (
                    "retrieval_score_below_threshold:"
                    f"best={best_score:.4f};minimum={retrieval_request.min_retrieval_score:.4f}"
                )
                work_id = str(payload.get("backlog_work_id", ""))
                if work_id:
                    await self.backlog.mark_generation_result(
                        work_id, GenerationWorkStatus.WAITING_FOR_EVIDENCE,
                        error=score_error,
                    )
                await self.queues.save(
                    job.transition(QueueStatus.BLOCKED, now=_utcnow(), error=score_error)
                )
                logger.info(
                    "generation_blocked job_id=%s reason=retrieval_score_below_threshold "
                    "best_score=%.4f minimum_score=%.4f",
                    job.job_id, best_score, retrieval_request.min_retrieval_score,
                )
                return True

            qualifying_evidence_ids = {chunk.evidence_id for chunk in qualifying_chunks}
            retrieved = replace(
                retrieved,
                documents=tuple(
                    document for document in retrieved.documents
                    if document.document_id in qualifying_evidence_ids
                ),
                chunks=qualifying_chunks,
            )
            supplied_evidence = retrieved.documents
            candidates = await self.images.candidates_for(supplied_evidence)
            image_search = getattr(self.images, "search_for_generation", None)
            if callable(image_search):
                try:
                    web_candidates = await image_search(
                        supplied_evidence, query=retrieval_request.query,
                        content_type=retrieval_request.content_type,
                        menu_work_ids=retrieval_request.menu_work_ids,
                    )
                except Exception as error:
                    logger.warning(
                        "web_image_search_unavailable job_id=%s error_type=%s",
                        job.job_id, type(error).__name__,
                    )
                else:
                    candidates = (*candidates, *web_candidates)
            candidates = _named_character_image_candidates(retrieval_request.query, candidates)
            # Keep the model's image packet focused. Search hits stay in the packet so
            # the exact card-to-image vision check can select or reject them.
            candidates = tuple(sorted(
                {item.asset_id: item for item in candidates}.values(),
                key=lambda item: (
                    image_resolution_tier(item.width, item.height),
                    0 if _image_names_queried_person(retrieval_request.query, item) else 1,
                    0 if item.source_id == "firecrawl_web_images" else 1,
                    item.recent_use_count, item.last_used_at or "", item.asset_id,
                ),
            )[:20])
            if len(candidates) < 3:
                image_reason = "waiting_for_three_work_linked_image_assets"
                work_id = str(payload.get("backlog_work_id", ""))
                if work_id:
                    await self.backlog.mark_generation_result(
                        work_id, GenerationWorkStatus.WAITING_FOR_EVIDENCE,
                        error=image_reason,
                    )
                await self.queues.save(job.transition(
                    QueueStatus.BLOCKED, now=_utcnow(), error=image_reason,
                ))
                logger.info(
                    "generation_blocked job_id=%s reason=%s candidate_count=%d",
                    job.job_id, image_reason, len(candidates),
                )
                cleanup = getattr(self.images, "discard_staged", None)
                if callable(cleanup):
                    cleanup()
                return True
            recent_topics = await self.drafts.recent_topics(retrieval_request.menu_work_ids, limit=30)
            draft = None
            for image_attempt in range(3):
                try:
                    draft = await self.generation.generate(
                        retrieved,
                        image_candidates=candidates, revision_instructions=instructions,
                        avoid_recent_topics=recent_topics,
                    )
                    break
                except ImageCopyMismatchError as mismatch:
                    image_search = getattr(self.images, "search_for_generation", None)
                    if (image_attempt >= 2 or not callable(image_search)
                            or not mismatch.image_asset_id):
                        raise
                    # Search again for the failed page itself. The previous image is
                    # removed from the next model packet so it cannot be selected again.
                    candidates = tuple(
                        asset for asset in candidates
                        if asset.asset_id != mismatch.image_asset_id
                    )
                    work_title = next((
                        item.title.strip() for item in supplied_evidence
                        if item.title.strip()
                    ), "")
                    focused_query = _focused_image_query(
                        retrieval_request.query, work_title, mismatch,
                    )
                    try:
                        replacement_images = await image_search(
                            supplied_evidence, query=focused_query,
                            content_type=retrieval_request.content_type,
                            menu_work_ids=retrieval_request.menu_work_ids,
                        )
                    except Exception as search_error:
                        logger.warning(
                            "targeted_image_retry_unavailable job_id=%s card_id=%s error_type=%s",
                            job.job_id, mismatch.card_id, type(search_error).__name__,
                        )
                        raise mismatch
                    candidates = tuple({
                        asset.asset_id: asset
                        for asset in (*replacement_images, *candidates)
                    }.values())[:20]
                    if len(candidates) < 3:
                        raise mismatch
                    instructions = (*instructions,
                        "A previous image candidate was unreadable, visibly pixelated at card size, or could not "
                        "be confirmed as belonging to the selected work. For the affected page, choose a readable "
                        "sharper distinct image whose "
                        "work or related character identity is supported. It does not need to depict the exact "
                        "scene, action, age, season, episode, or sentence. An attractive representative image "
                        "from the same work is acceptable.")
                    logger.info(
                        "targeted_image_retry job_id=%s card_id=%s candidate_count=%d attempt=%d",
                        job.job_id, mismatch.card_id, len(replacement_images), image_attempt + 1,
                    )
            if draft is None:
                raise RuntimeError("Generation did not produce a draft after bounded image retries")
            web_candidate = payload.get("web_candidate")
            if isinstance(web_candidate, dict):
                judgment_basis = dict(draft.ai_judgment_basis)
                judgment_basis["web_discovery_candidate"] = {
                    key: web_candidate[key]
                    for key in (
                        "title", "url", "host", "published_at", "fetched_at", "rag_score",
                        "corroborating_document_ids", "corroborating_chunk_ids", "verified_claims",
                        "unsupported_claim_count",
                    )
                    if key in web_candidate
                }
                draft = replace(draft, ai_judgment_basis=judgment_basis)
            duplicate_title = await self.drafts.find_duplicate_topic(draft)
            if duplicate_title:
                reason = f"duplicate_content_guard:{duplicate_title[:120]}"
                work_id = str(payload.get("backlog_work_id", ""))
                if work_id:
                    await self.backlog.mark_generation_result(
                        work_id, GenerationWorkStatus.BLOCKED, error=reason,
                    )
                await self.queues.save(job.transition(
                    QueueStatus.BLOCKED, now=_utcnow(), error=reason,
                ))
                logger.info("generation_blocked job_id=%s reason=duplicate_content_guard", job.job_id)
                cleanup = getattr(self.images, "discard_staged", None)
                if callable(cleanup):
                    cleanup()
                return True
            image_ids = tuple(dict.fromkeys(card.image_asset_id for card in draft.cards))
            persist_selected = getattr(self.images, "persist_selected", None)
            if callable(persist_selected):
                await persist_selected(image_ids)
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
                "source_ids": retrieval_request.source_ids,
                "seed_evidence_ids": retrieval_request.seed_evidence_ids,
                "top_k": retrieval_request.top_k,
                "candidate_limit": retrieval_request.candidate_limit,
                "min_retrieval_score": retrieval_request.min_retrieval_score,
                "backlog_work_id": work_id,
            }, f"review:{draft.draft_id}:{revision_count}"))
            if work_id:
                await self.backlog.mark_generation_result(work_id, GenerationWorkStatus.DRAFTED)
            await self.queues.save(job.transition(QueueStatus.SUCCEEDED, now=_utcnow()))
            logger.info("generation_complete job_id=%s draft_id=%s content_type=%s",
                        job.job_id, draft.draft_id, draft.content_type.value)
            cleanup = getattr(self.images, "discard_staged", None)
            if callable(cleanup):
                cleanup()
        except ImageCopyMismatchError as error:
            reason = f"image_subject_match_required:{str(error)[:240]}"
            work_id = str(payload.get("backlog_work_id", ""))
            if work_id:
                await self.backlog.mark_generation_result(
                    work_id, GenerationWorkStatus.BLOCKED, error=reason,
                )
            await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_utcnow(), error=reason))
            logger.warning("generation_blocked job_id=%s reason=%s", job.job_id, reason)
            cleanup = getattr(self.images, "discard_staged", None)
            if callable(cleanup):
                cleanup()
        except DuplicateContentError as error:
            reason = f"duplicate_content_guard:{str(error)[:160]}"
            work_id = str(payload.get("backlog_work_id", ""))
            if work_id:
                await self.backlog.mark_generation_result(
                    work_id, GenerationWorkStatus.BLOCKED, error=reason,
                )
            await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_utcnow(), error=reason))
            logger.info("generation_blocked job_id=%s reason=duplicate_content_guard", job.job_id)
            cleanup = getattr(self.images, "discard_staged", None)
            if callable(cleanup):
                cleanup()
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
            cleanup = getattr(self.images, "discard_staged", None)
            if callable(cleanup):
                cleanup()
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
            if readability_issues:
                result = replace(
                    result,
                    issues=(*result.issues, *(replace(issue, blocking=False) for issue in readability_issues)),
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
                    "source_ids": tuple(str(value) for value in payload.get("source_ids", ())),
                    "seed_evidence_ids": tuple(str(value) for value in payload.get("seed_evidence_ids", ())),
                    "top_k": int(payload.get("top_k", 8)),
                    "candidate_limit": int(payload.get("candidate_limit", 100)),
                    "min_retrieval_score": float(
                        payload.get("min_retrieval_score", DEFAULT_MIN_RETRIEVAL_SCORE)
                    ),
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
            "min_retrieval_score": DEFAULT_MIN_RETRIEVAL_SCORE,
            "backlog_work_id": item.work_id,
        },
        f"generation:{item.work_id}",
    )
