"""Explicit composition for collection, generation, and independent review.

This module never opens a database, applies the schema, loads .env, calls MCP,
or starts a worker. Callers can compose each role independently and opt in to
its run_forever loop after supplying its own connection and dependencies.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ani_rag.adapter import MCPReadSearchAdapter
from ani_rag.agents.content_generation.agent import ContentGenerationAgent
from ani_rag.agents.mcp_crawler.agent import MCPCrawlerAgent, RecurringCollectionPlan
from ani_rag.agents.reviewer.agent import IndependentReviewAgent
from ani_rag.collector import CollectionRequest
from ani_rag.catalog import LLMWorkCatalogResolver, LocalWorkCatalog
from ani_rag.entity_linking import EntityCatalogRecord
from ani_rag.interfaces import WorkCatalogResolver
from ani_rag.model_services import (
    EvidenceBoundGenerationService,
    EvidenceBoundIndependentReviewer,
    StructuredModel,
)
from ani_rag.queues import QueueItem, QueueName, QueueStatus
from ani_rag.retrieval import DatabaseEvidenceRetriever
from ani_rag.settings import EvergreenSourceRegistry
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.storage.sqlite_workflow import (
    SQLiteDraftRepository,
    SQLiteImageReader,
    SQLiteReviewHistoryRepository,
)
from ani_rag.structured_mcp import StructuredCollectionRequest, StructuredMCPAdapter
from ani_rag.workers import (
    CollectionWorker,
    GenerationWorker,
    GenerationQueueReconciler,
    ImageReader,
    ReviewWorker,
    StructuredCollectionWorker,
)
from ani_rag.web_discovery import (
    WebCandidateAssessmentWorker,
    WebDiscoveryAdapter,
    WebDiscoveryRequest,
    WebDiscoveryWorker,
    enqueue_web_discovery,
)
from ani_rag.web_images import ImageSearchAdapter, WebSearchImageReader


@dataclass(frozen=True)
class GenerationReviewRuntime:
    """Constructed adapters and one-item workers; nothing is started automatically."""

    store: SQLiteRagStore
    images: ImageReader
    drafts: SQLiteDraftRepository
    generation_worker: GenerationWorker
    review_worker: ReviewWorker
    queue_reconciler: GenerationQueueReconciler


@dataclass(frozen=True)
class AniRagRuntime:
    """Convenience composition of all three independent role agents."""

    store: SQLiteRagStore
    images: ImageReader
    drafts: SQLiteDraftRepository
    collection_worker: CollectionWorker
    structured_collection_worker: StructuredCollectionWorker
    generation_worker: GenerationWorker
    review_worker: ReviewWorker
    queue_reconciler: GenerationQueueReconciler
    crawler_agent: MCPCrawlerAgent
    content_generation_agent: ContentGenerationAgent
    reviewer_agent: IndependentReviewAgent


def compose_crawler_agent(
    connection: sqlite3.Connection,
    *,
    registry: EvergreenSourceRegistry,
    url_mcp: MCPReadSearchAdapter,
    web_discovery_adapter: WebDiscoveryAdapter | None = None,
    structured_mcp: StructuredMCPAdapter,
    work_match_model: StructuredModel,
    work_catalog: WorkCatalogResolver | None = None,
    recurring_plans: tuple[RecurringCollectionPlan, ...] = (),
    image_asset_dir: Path | None = None,
    image_entity_catalog: tuple[EntityCatalogRecord, ...] = (),
) -> MCPCrawlerAgent:
    """Compose only the crawler role over a caller-owned RAG DB connection."""
    local_catalog = LocalWorkCatalog()
    store = SQLiteRagStore(connection, menu_work_id_floor=local_catalog.max_work_id)
    resolver = work_catalog or LLMWorkCatalogResolver(
        local_catalog, work_match_model, database_candidates=store.work_catalog_candidates,
    )
    images = SQLiteImageReader(store)
    web_discovery_worker = (
        WebDiscoveryWorker(store, web_discovery_adapter)
        if web_discovery_adapter is not None else None
    )
    web_candidate_worker = (
        WebCandidateAssessmentWorker(
            store, web_discovery_adapter, resolver, DatabaseEvidenceRetriever(store),
            evidence_model=work_match_model,
        )
        if web_discovery_adapter is not None else None
    )
    return MCPCrawlerAgent(
        url_worker=CollectionWorker(
            queues=store, registry=registry, adapter=url_mcp, evidence=store,
            work_catalog=resolver, image_repository=images, image_asset_dir=image_asset_dir,
            image_entity_catalog=image_entity_catalog,
        ),
        structured_worker=StructuredCollectionWorker(
            queues=store, registry=registry, adapter=structured_mcp, evidence=store,
            work_catalog=resolver, image_repository=images, image_asset_dir=image_asset_dir,
            image_entity_catalog=image_entity_catalog,
        ),
        web_discovery_worker=web_discovery_worker,
        web_candidate_worker=web_candidate_worker,
        recurring_plans=recurring_plans,
    )


def compose_content_generation_agent(
    connection: sqlite3.Connection,
    *,
    generation_model: StructuredModel,
    image_search_adapter: ImageSearchAdapter | None = None,
    image_asset_dir: Path | None = None,
) -> ContentGenerationAgent:
    """Compose only the database-polling generation role."""
    store = SQLiteRagStore(connection)
    images = _generation_image_reader(
        store, image_search_adapter=image_search_adapter, image_asset_dir=image_asset_dir,
    )
    drafts = SQLiteDraftRepository(store)
    worker = GenerationWorker(
        queues=store, backlog=store, retriever=DatabaseEvidenceRetriever(store),
        images=images, drafts=drafts,
        generation=EvidenceBoundGenerationService(generation_model),
    )
    return ContentGenerationAgent(worker, GenerationQueueReconciler(store, store))


def compose_reviewer_agent(
    connection: sqlite3.Connection,
    *,
    review_model: StructuredModel,
) -> IndependentReviewAgent:
    """Compose only the independent review role over its own DB connection."""
    store = SQLiteRagStore(connection)
    drafts = SQLiteDraftRepository(store)
    worker = ReviewWorker(
        queues=store, drafts=drafts,
        history=SQLiteReviewHistoryRepository(store),
        reviewer=EvidenceBoundIndependentReviewer(review_model),
    )
    return IndependentReviewAgent(worker)


def compose_generation_review_runtime(
    connection: sqlite3.Connection,
    *,
    generation_model: StructuredModel,
    review_model: StructuredModel,
    image_search_adapter: ImageSearchAdapter | None = None,
    image_asset_dir: Path | None = None,
) -> GenerationReviewRuntime:
    """Wire RAG's generation/review components over a caller-owned DB connection.

    ``SQLiteRagStore`` changes the supplied connection's row factory and takes
    exclusive ownership for the lifetime of the runtime. This function performs
    no SQL during composition; the caller must provision the separate RAG schema
    and open the connection with ``check_same_thread=False`` beforehand.
    """

    store = SQLiteRagStore(connection)
    images = _generation_image_reader(
        store, image_search_adapter=image_search_adapter, image_asset_dir=image_asset_dir,
    )
    drafts = SQLiteDraftRepository(store)
    generation_worker = GenerationWorker(
        queues=store,
        backlog=store,
        retriever=DatabaseEvidenceRetriever(store),
        images=images,
        drafts=drafts,
        generation=EvidenceBoundGenerationService(generation_model),
    )
    review_worker = ReviewWorker(
        queues=store,
        drafts=drafts,
        history=SQLiteReviewHistoryRepository(store),
        reviewer=EvidenceBoundIndependentReviewer(review_model),
    )
    return GenerationReviewRuntime(
        store=store,
        images=images,
        drafts=drafts,
        generation_worker=generation_worker,
        review_worker=review_worker,
        queue_reconciler=GenerationQueueReconciler(store, store),
    )


def compose_runtime(
    connection: sqlite3.Connection,
    *,
    registry: EvergreenSourceRegistry,
    url_mcp: MCPReadSearchAdapter,
    web_discovery_adapter: WebDiscoveryAdapter | None = None,
    image_search_adapter: ImageSearchAdapter | None = None,
    structured_mcp: StructuredMCPAdapter,
    generation_model: StructuredModel,
    review_model: StructuredModel,
    work_catalog: WorkCatalogResolver | None = None,
    work_match_model: StructuredModel | None = None,
    recurring_plans: tuple[RecurringCollectionPlan, ...] = (),
    image_asset_dir: Path | None = None,
    image_entity_catalog: tuple[EntityCatalogRecord, ...] = (),
) -> AniRagRuntime:
    """Wire the complete one-item lane using caller-owned adapters and storage.

    This only constructs objects. It does not inspect source configuration on
    disk, open a database, apply schema, call MCP/models, or start a worker.
    """

    local_catalog = LocalWorkCatalog()
    store = SQLiteRagStore(connection, menu_work_id_floor=local_catalog.max_work_id)
    resolver = work_catalog or LLMWorkCatalogResolver(
        local_catalog, work_match_model or generation_model,
        database_candidates=store.work_catalog_candidates,
    )
    effective_image_search = image_search_adapter or _image_search_capable(
        web_discovery_adapter, url_mcp,
    )
    images = _generation_image_reader(
        store, image_search_adapter=effective_image_search,
        image_asset_dir=image_asset_dir,
    )
    collection_images = SQLiteImageReader(store)
    drafts = SQLiteDraftRepository(store)
    collection_worker = CollectionWorker(
        queues=store, registry=registry, adapter=url_mcp, evidence=store,
        work_catalog=resolver, image_repository=collection_images, image_asset_dir=image_asset_dir,
        image_entity_catalog=image_entity_catalog,
    )
    structured_collection_worker = StructuredCollectionWorker(
        queues=store, registry=registry, adapter=structured_mcp, evidence=store,
        work_catalog=resolver, image_repository=collection_images, image_asset_dir=image_asset_dir,
        image_entity_catalog=image_entity_catalog,
    )
    web_discovery_worker = (
        WebDiscoveryWorker(store, web_discovery_adapter)
        if web_discovery_adapter is not None else None
    )
    web_candidate_worker = (
        WebCandidateAssessmentWorker(
            store, web_discovery_adapter, resolver, DatabaseEvidenceRetriever(store),
            evidence_model=work_match_model or generation_model,
        )
        if web_discovery_adapter is not None else None
    )
    generation_worker = GenerationWorker(
        queues=store, backlog=store, retriever=DatabaseEvidenceRetriever(store),
        images=images, drafts=drafts,
        generation=EvidenceBoundGenerationService(generation_model),
    )
    review_worker = ReviewWorker(
        queues=store, drafts=drafts, history=SQLiteReviewHistoryRepository(store),
        reviewer=EvidenceBoundIndependentReviewer(review_model),
    )
    return AniRagRuntime(
        store=store,
        images=images,
        drafts=drafts,
        collection_worker=collection_worker,
        structured_collection_worker=structured_collection_worker,
        generation_worker=generation_worker,
        review_worker=review_worker,
        queue_reconciler=GenerationQueueReconciler(store, store),
        crawler_agent=MCPCrawlerAgent(
            url_worker=collection_worker, structured_worker=structured_collection_worker,
            web_discovery_worker=web_discovery_worker,
            web_candidate_worker=web_candidate_worker,
            recurring_plans=recurring_plans,
        ),
        content_generation_agent=ContentGenerationAgent(
            generation_worker, GenerationQueueReconciler(store, store),
        ),
        reviewer_agent=IndependentReviewAgent(review_worker),
    )


def _image_search_capable(*candidates: object) -> ImageSearchAdapter | None:
    return next((item for item in candidates if callable(getattr(item, "search_images", None))), None)  # type: ignore[return-value]


def _generation_image_reader(
    store: SQLiteRagStore, *, image_search_adapter: ImageSearchAdapter | None,
    image_asset_dir: Path | None,
) -> ImageReader:
    local = SQLiteImageReader(store)
    if image_search_adapter is None:
        # Constructing the adapter is local-only. MCP connection/tool discovery
        # happens only when a generation job explicitly searches for images.
        from ani_rag.firecrawl_adapter import FirecrawlMCPAdapter

        image_search_adapter = FirecrawlMCPAdapter()
    destination = image_asset_dir or Path(__file__).resolve().parents[2] / "data" / "image_assets"
    return WebSearchImageReader(local, image_search_adapter, asset_dir=destination)


async def enqueue_web_discovery_request(
    runtime: AniRagRuntime, request: WebDiscoveryRequest,
) -> QueueItem:
    """Queue broad-web candidate discovery for a configured runtime; no worker runs here."""
    if runtime.crawler_agent.web_discovery_worker is None:
        raise RuntimeError("compose_runtime needs an explicit web_discovery_adapter for broad-web discovery")
    return await enqueue_web_discovery(runtime.store, request)


async def enqueue_url_collection(runtime: AniRagRuntime, request: CollectionRequest) -> QueueItem:
    """Queue exactly one bounded URL-source collection request; do not run it."""

    now = datetime.now(timezone.utc)
    return await runtime.store.enqueue(QueueItem(
        job_id=f"job_{uuid.uuid4().hex}",
        queue=QueueName.COLLECTION,
        status=QueueStatus.PENDING,
        created_at=now,
        updated_at=now,
        dedupe_key=(f"collection:url:{request.source_id}:{request.content_type.value}:"
                    f"{request.evidence_type.value}:{request.query.strip()}"),
        handler_kind="url",
        payload={
            "source_id": request.source_id,
            "content_type": request.content_type.value,
            "evidence_type": request.evidence_type.value,
            "query": request.query,
            "language": request.language,
            "result_limit": request.result_limit,
            **({"seed_menu_work_id": request.seed_menu_work_id}
               if request.seed_menu_work_id is not None else {}),
        },
    ))


async def enqueue_structured_collection(
    runtime: AniRagRuntime, request: StructuredCollectionRequest
) -> QueueItem:
    """Queue one bounded entity/property request; no structured MCP call occurs here."""

    now = datetime.now(timezone.utc)
    return await runtime.store.enqueue(QueueItem(
        job_id=f"job_{uuid.uuid4().hex}",
        queue=QueueName.COLLECTION,
        status=QueueStatus.PENDING,
        created_at=now,
        updated_at=now,
        dedupe_key=(f"collection:structured:{request.source_id}:{request.content_type.value}:"
                    f"{request.evidence_type.value}:{request.query.strip()}:"
                    f"{','.join(request.property_ids)}"),
        handler_kind="structured",
        payload={
            "source_id": request.source_id,
            "content_type": request.content_type.value,
            "evidence_type": request.evidence_type.value,
            "query": request.query,
            "language": request.language,
            "property_ids": request.property_ids,
            "result_limit": request.result_limit,
        },
    ))
