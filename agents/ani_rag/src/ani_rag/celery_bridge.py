"""Bounded, opt-in RAG generation/review for an external Celery attempt.

The caller owns a *separate writable* RAG database and admission policy. This
module never opens the service DB, crawls, publishes, starts a worker loop, or
executes a review-requested regeneration. Original evidence IDs stay intact.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Mapping
from uuid import UUID

from ani_rag.model_services import (
    EvidenceBoundGenerationService, EvidenceBoundIndependentReviewer,
    OpenAIResponsesStructuredModel,
)
from ani_rag.models import RagContentType
from ani_rag.queues import QueueItem, QueueName, QueueStatus
from ani_rag.retrieval import GenerationWorkRequest, DEFAULT_MIN_RETRIEVAL_SCORE
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.storage.sqlite_workflow import SQLiteDraftRepository, SQLiteReviewHistoryRepository
from ani_rag.workers import GenerationWorker, ReviewWorker


class BridgeError(ValueError):
    """A bounded attempt cannot safely be started or represented as completed."""


_ACTIVE_CONNECTIONS: set[int] = set()
_CONNECTION_GUARD = threading.Lock()


def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _json(value):
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


class UsageLedger:
    """Keep missing provider usage/cost explicit, including failed calls."""

    def __init__(self, *, simulated: bool = False, pricing: Mapping | None = None):
        self.records: list[dict] = []
        self.simulated = simulated
        self.pricing = dict(pricing or {})
        for rates in self.pricing.values():
            if not isinstance(rates, Mapping) or set(rates) != {"input", "output", "cached_input"}:
                raise BridgeError("invalid_model_pricing")
            if any(not Decimal(str(value)).is_finite() or Decimal(str(value)) < 0 for value in rates.values()):
                raise BridgeError("invalid_model_pricing")

    def record(self, stage, *, model=None, tokens=None, error=None, estimated_cost_usd=None):
        tokens = tokens if isinstance(tokens, dict) else {}
        tokens = {key: value for key, value in tokens.items()
                  if key in {"input_tokens", "output_tokens", "cached_input_tokens"}
                  and type(value) is int and value >= 0}
        amount = None
        if self.simulated:
            amount = "0"
        elif estimated_cost_usd is not None:
            candidate = Decimal(str(estimated_cost_usd))
            if candidate.is_finite() and candidate >= 0:
                amount = str(candidate)
        elif model in self.pricing and {"input_tokens", "output_tokens"} <= tokens.keys():
            rates = self.pricing[model]
            cached = tokens.get("cached_input_tokens", 0)
            if cached <= tokens["input_tokens"]:
                amount = str((Decimal(tokens["input_tokens"] - cached) * Decimal(str(rates["input"]))
                    + Decimal(cached) * Decimal(str(rates["cached_input"]))
                    + Decimal(tokens["output_tokens"]) * Decimal(str(rates["output"]))) / Decimal(1_000_000))
        self.records.append({"stage": stage, "model": model, "tokens": tokens or None,
                             "estimated_cost_usd": amount, "simulated": self.simulated,
                             "usage_complete": {"input_tokens", "output_tokens"} <= tokens.keys(),
                             "error": error})

    def provider(self, stage, response, *, model=None):
        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        tokens = {"input_tokens": getattr(usage, "input_tokens", None),
                  "output_tokens": getattr(usage, "output_tokens", None),
                  "cached_input_tokens": getattr(details, "cached_tokens", None)}
        # Embedding responses use prompt_tokens and have no output tokens.
        if stage == "query_embedding":
            tokens = {"input_tokens": getattr(usage, "prompt_tokens", None), "output_tokens": 0}
        self.record(stage, model=getattr(response, "model", None) or model, tokens=tokens)

    def summary(self):
        unknown = sum(row["estimated_cost_usd"] is None for row in self.records)
        known = sum((Decimal(row["estimated_cost_usd"]) for row in self.records
                     if row["estimated_cost_usd"] is not None), Decimal(0))
        return {"complete": unknown == 0, "known_estimated_cost_usd": str(known),
                "unknown_calls": unknown, "total_estimated_cost_usd": str(known) if not unknown else None}


class RecordedModel:
    """Wrap injected structured models; failed/unreported calls remain unknown."""

    def __init__(self, model, ledger: UsageLedger, stage: str):
        self.model, self.ledger, self.stage = model, ledger, stage

    async def _call(self, method, kwargs):
        before = len(self.ledger.records)
        try:
            result = await getattr(self.model, method)(**kwargs)
        except Exception as error:
            if len(self.ledger.records) == before:
                self.ledger.record(self.stage, model=getattr(self.model, "model", None),
                                   error=type(error).__name__)
            raise
        if len(self.ledger.records) == before:
            self.ledger.record(self.stage, model=getattr(result, "model_name", None),
                               tokens=getattr(result, "token_usage", None))
        return result

    async def generate_json(self, **kwargs):
        return await self._call("generate_json", kwargs)

    async def generate_json_with_images(self, **kwargs):
        if not callable(getattr(self.model, "generate_json_with_images", None)):
            kwargs.pop("image_data_urls", None)
            return await self._call("generate_json", kwargs)
        return await self._call("generate_json_with_images", kwargs)


class RecordingOpenAIModel(OpenAIResponsesStructuredModel):
    """Capture provider usage before JSON parsing can fail."""

    def __init__(self, *, ledger: UsageLedger, stage: str, **kwargs):
        super().__init__(**kwargs)
        self.client = self.client.with_options(max_retries=0)
        self.ledger, self.stage = ledger, stage

    def _log_usage(self, response, schema_name):
        self.ledger.provider(self.stage, response, model=self.model)


class RecordedQueryEmbeddings:
    def __init__(self, client, ledger: UsageLedger):
        self.client, self.ledger = client, ledger

    async def embed_query(self, text):
        before = len(self.ledger.records)
        try:
            response = await asyncio.to_thread(self.client.embeddings.create,
                model="text-embedding-3-small", input=text, dimensions=1024, encoding_format="float")
            self.ledger.provider("query_embedding", response, model="text-embedding-3-small")
            vector = tuple(float(value) for value in response.data[0].embedding)
            if len(response.data) != 1 or len(vector) != 1024 or not all(map(math.isfinite, vector)):
                raise BridgeError("invalid_query_embedding")
            return vector
        except Exception as error:
            if len(self.ledger.records) == before:
                self.ledger.record("query_embedding", model="text-embedding-3-small", error=type(error).__name__)
            raise


class _ScopedRetriever:
    def __init__(self, retriever, ledger, work_ids, source_ids):
        self.retriever, self.ledger = retriever, ledger
        self.work_ids, self.source_ids = set(work_ids), set(source_ids)

    async def retrieve(self, request):
        before = len(self.ledger.records)
        result = None
        try:
            result = await self.retriever.retrieve(request)
        finally:
            if (hasattr(self.retriever, "query_embeddings") and len(self.ledger.records) == before
                    and (result is None or result.documents)):
                self.ledger.record("query_embedding", error="injected_embedder_usage_unavailable")
        documents = {item.document_id: item for item in result.documents}
        if any(item.source_id not in self.source_ids or not self.work_ids.intersection(item.menu_work_ids)
               for item in result.documents):
            raise BridgeError("retrieval_scope_mismatch")
        if any(item.evidence_id not in documents or item.source_id != documents[item.evidence_id].source_id
               or item.source_url != documents[item.evidence_id].source_url for item in result.chunks):
            raise BridgeError("retrieval_provenance_mismatch")
        return result


class _ScopedQueues:
    """Use real queue persistence but never claim another external attempt's job."""

    def __init__(self, store, generation_id):
        self.store = store
        self.targets = {QueueName.GENERATION: generation_id}
        self.children: list[str] = []

    async def claim(self, queue, *, now, **_kwargs):
        target = self.targets.get(queue)
        if target is None:
            return None
        with self.store._transaction(immediate=True):
            row = self.store._connection.execute(
                "SELECT * FROM rag_queue_job WHERE job_id=? AND queue_name=? AND status='pending'",
                (target, queue.value)).fetchone()
            if row is None:
                return None
            previous = self.store._queue_item(row)
            stamp = now.isoformat()
            self.store._connection.execute(
                "UPDATE rag_queue_job SET status='running',updated_at=?,started_at=? WHERE job_id=? AND status='pending'",
                (stamp, stamp, target))
            self.store._insert_event(target, previous.status, QueueStatus.RUNNING, stamp, None)
            return previous.transition(QueueStatus.RUNNING, now=now)

    async def enqueue(self, item):
        if item.queue is QueueName.REVIEW:
            if QueueName.REVIEW in self.targets:
                raise BridgeError("multiple_review_jobs")
        else:
            # Preserve the proposed revision/evidence request without making it
            # executable by an unrelated legacy worker after this handler exits.
            item = replace(item, status=QueueStatus.BLOCKED,
                           last_error="celery_bounded_followup_deferred")
        saved = await self.store.enqueue(item)
        if saved.job_id != item.job_id:
            raise BridgeError("existing_followup_job")
        self.children.append(saved.job_id)
        if item.queue is QueueName.REVIEW:
            self.targets[QueueName.REVIEW] = saved.job_id
        return saved

    async def save(self, item):
        if item.job_id not in {*self.targets.values(), *self.children}:
            raise BridgeError("unbound_queue_write")
        await self.store.save(item)
        if item.status is QueueStatus.RETRY_WAIT:
            await self.store.save(item.transition(QueueStatus.BLOCKED, now=datetime.now(timezone.utc),
                                                  error="celery_bounded_retry_deferred"))


def _bind(store, request, job_id, attempt_id, source_ids, threshold, simulated, namespace):
    if not isinstance(namespace, str) or not namespace.strip() or len(namespace) > 100:
        raise BridgeError("invalid_namespace")
    for value in (job_id, attempt_id):
        if str(UUID(value)) != value:
            raise BridgeError("invalid_external_identity")
    if len(request.menu_work_ids) != 1 or type(request.menu_work_ids[0]) is not int or request.menu_work_ids[0] <= 0:
        raise BridgeError("one_existing_work_required")
    if not source_ids or any(not isinstance(value, str) or not value.strip() for value in source_ids):
        raise BridgeError("explicit_source_scope_required")
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise BridgeError("invalid_retrieval_threshold")
    binding = {"namespace": namespace, "request": asdict(request), "source_ids": sorted(set(source_ids)),
               "min_retrieval_score": threshold, "simulated": simulated}
    digest = hashlib.sha256(_json(binding).encode()).hexdigest()
    connection = store._connection
    with store._transaction(immediate=True):
        connection.execute("""CREATE TABLE IF NOT EXISTS rag_celery_attempt(
            work_id TEXT PRIMARY KEY,namespace TEXT NOT NULL,job_id TEXT NOT NULL UNIQUE,attempt_id TEXT NOT NULL UNIQUE,
            input_sha256 TEXT NOT NULL,state TEXT NOT NULL,result_json TEXT,created_at TEXT NOT NULL)""")
        row = connection.execute(
            "SELECT * FROM rag_celery_attempt WHERE work_id=? OR job_id=? OR attempt_id=?",
            (request.work_id, job_id, attempt_id)).fetchone()
        if row:
            if (row["work_id"], row["namespace"], row["job_id"], row["attempt_id"], row["input_sha256"]) != (
                    request.work_id, namespace, job_id, attempt_id, digest):
                raise BridgeError("generation_already_bound")
            if row["state"] != "complete" or not row["result_json"]:
                raise BridgeError("generation_may_have_started")
            return digest, json.loads(row["result_json"])
        old = connection.execute("SELECT * FROM rag_generation_work WHERE work_id=?", (request.work_id,)).fetchone()
        if old and (old["status"] != "pending" or old["queue_job_id"] is not None):
            raise BridgeError("existing_generation_history")
        if connection.execute("SELECT 1 FROM rag_queue_job WHERE json_extract(payload_json,'$.backlog_work_id')=?",
                              (request.work_id,)).fetchone():
            raise BridgeError("existing_generation_queue_history")
        if old and (old["retrieval_query"] != request.query or old["content_type"] != request.content_type.value
                    or json.loads(old["seed_evidence_ids_json"]) != list(request.seed_evidence_ids)
                    or json.loads(old["menu_work_ids_json"]) != list(request.menu_work_ids)):
            raise BridgeError("existing_request_changed")
        marks = ",".join("?" for _ in request.seed_evidence_ids)
        rows = connection.execute(
            f"SELECT document_id,source_id FROM rag_document WHERE document_id IN ({marks})",
            request.seed_evidence_ids).fetchall()
        if {row["document_id"] for row in rows} != set(request.seed_evidence_ids) or any(
                row["source_id"] not in source_ids for row in rows):
            raise BridgeError("seed_source_scope_mismatch")
        for seed in request.seed_evidence_ids:
            if connection.execute("SELECT 1 FROM rag_work_link WHERE document_id=? AND menu_work_id=? AND status='matched'",
                                  (seed, request.menu_work_ids[0])).fetchone() is None:
                raise BridgeError("seed_work_scope_mismatch")
        from ani_rag.catalog import LocalWorkCatalog
        if not LocalWorkCatalog().titles_for_work_id(request.menu_work_ids[0]):
            raise BridgeError("unknown_work_id")
        connection.execute("INSERT INTO rag_celery_attempt VALUES(?,?,?,?,?,?,?,?)",
            (request.work_id, namespace, job_id, attempt_id, digest, "running", None, datetime.now(timezone.utc).isoformat()))
    return digest, None


async def run_single_request(connection: sqlite3.Connection, **kwargs) -> dict:
    """Run one external attempt; a SQLite connection is exclusively caller-owned."""
    identity = id(connection)
    with _CONNECTION_GUARD:
        if identity in _ACTIVE_CONNECTIONS:
            raise BridgeError("connection_already_in_use")
        _ACTIVE_CONNECTIONS.add(identity)
    try:
        return await _run_single_request(connection, **kwargs)
    finally:
        with _CONNECTION_GUARD:
            _ACTIVE_CONNECTIONS.remove(identity)


async def _run_single_request(connection: sqlite3.Connection, *, request: GenerationWorkRequest,
        job_id: str, attempt_id: str, retriever, images, generation_model=None, review_model=None,
        namespace: str, source_ids: tuple[str, ...], min_retrieval_score: float = DEFAULT_MIN_RETRIEVAL_SCORE,
        allow_paid: bool = False, offline: bool = False, usage: UsageLedger | None = None,
        generation_service=None, reviewer=None) -> dict:
    """Run exactly one generation and its one review in caller-owned isolation.

    ``offline`` marks injected fixture outputs permanently non-production.
    ``allow_paid`` is required for real execution; it does not grant admission
    or enforce daily limits, which belong to the external Celery controller.
    Test-only service injections are rejected outside offline mode.
    """
    if not offline and not allow_paid:
        raise BridgeError("paid_generation_disabled")
    if offline and any(isinstance(model, OpenAIResponsesStructuredModel) for model in (generation_model, review_model)):
        raise BridgeError("provider_model_in_offline_mode")
    if not offline and (generation_service is not None or reviewer is not None):
        raise BridgeError("service_override_requires_offline_mode")
    if connection.in_transaction:
        raise BridgeError("caller_transaction_must_be_idle")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise BridgeError("foreign_keys_required")
    ledger = usage or UsageLedger(simulated=offline)
    if ledger.simulated != offline or ledger.records:
        raise BridgeError("fresh_matching_usage_ledger_required")
    store = SQLiteRagStore(connection)
    digest, cached = _bind(store, request, job_id, attempt_id, source_ids, min_retrieval_score, offline, namespace)
    if cached is not None:
        return cached
    result = {"schema_version": 1, "producer": "ani_rag", "simulated": offline, "namespace": namespace,
              "review_mode": "offline_fixture" if offline else "llm", "job_id": job_id,
              "external_job_id": job_id, "generation_attempt_id": attempt_id,
              "work_id": request.work_id, "request": _jsonable(asdict(request)),
              "source_ids": sorted(set(source_ids)), "input_sha256": digest,
              "outcome": "held", "producer_status": "not_started", "draft": None, "review": None,
              "evidence": {}, "retrieved_chunks": {}, "images": {}, "rendered_cards": []}
    try:
        await store.enqueue_generation_work(request)
        now = datetime.now(timezone.utc)
        generation_id = "celery_generation_" + attempt_id
        job = QueueItem(generation_id, QueueName.GENERATION, created_at=now, updated_at=now,
            dedupe_key="celery_generation:" + request.work_id, max_retries=0,
            payload={"query": request.query, "content_type": request.content_type.value,
                     "menu_work_ids": request.menu_work_ids, "seed_evidence_ids": request.seed_evidence_ids,
                     "source_ids": source_ids, "top_k": request.top_k, "candidate_limit": request.candidate_limit,
                     "min_retrieval_score": min_retrieval_score, "backlog_work_id": request.work_id})
        saved = await store.enqueue(job)
        if saved.job_id != generation_id or saved.status is not QueueStatus.PENDING:
            raise BridgeError("existing_generation_queue")
        await store.mark_generation_enqueued(request.work_id, generation_id)
        queues = _ScopedQueues(store, generation_id)
        drafts = SQLiteDraftRepository(store)
        generator = generation_service or EvidenceBoundGenerationService(RecordedModel(generation_model, ledger, "generation"))
        checker = reviewer or EvidenceBoundIndependentReviewer(RecordedModel(review_model, ledger, "review"))
        generation = GenerationWorker(queues, store, _ScopedRetriever(retriever, ledger, request.menu_work_ids, source_ids),
                                      images, drafts, generator)
        review = ReviewWorker(queues, drafts, SQLiteReviewHistoryRepository(store), checker)
        await generation.run_once()
        generation_row = await store.get(generation_id)
        result.update(producer_status=generation_row.status.value, reason=generation_row.last_error)
        review_id = queues.targets.get(QueueName.REVIEW)
        if review_id is not None:
            await review.run_once()
            review_job = await store.get(review_id)
            draft_id = review_job.payload["draft_id"]
            context = await drafts.load_for_review(draft_id)
            draft_status = connection.execute("SELECT status FROM rag_content_draft WHERE draft_id=?", (draft_id,)).fetchone()[0]
            histories = connection.execute("SELECT * FROM rag_review_history WHERE draft_id=? ORDER BY reviewed_at,review_id", (draft_id,)).fetchall()
            result.update(draft=_jsonable(asdict(context.draft)), producer_status=draft_status,
                          evidence={key: _jsonable(asdict(value)) for key, value in context.evidence.items()},
                          retrieved_chunks={key: _jsonable(asdict(value)) for key, value in context.retrieved_chunks.items()},
                          images={key: _jsonable(asdict(value)) for key, value in context.images.items()},
                          reason=review_job.last_error)
            if histories:
                row = histories[-1]
                result["review"] = {**json.loads(row["result_json"]), "review_id": row["review_id"], "reviewed_at": row["reviewed_at"]}
                judgment = connection.execute("SELECT model_name,token_cost_json FROM rag_judgment_log WHERE draft_id=? AND judged_at=?",
                                              (draft_id, row["reviewed_at"])).fetchone()
                if judgment is not None:
                    result["review"].update(model_name=judgment["model_name"],
                        token_usage=json.loads(judgment["token_cost_json"]) if judgment["token_cost_json"] else None)
            for page, card in enumerate(context.draft.cards, 1):
                png = context.rendered_cards[card.card_id]
                from PIL import Image
                with Image.open(BytesIO(png)) as opened:
                    opened.load()
                    width, height = opened.size
                result["rendered_cards"].append({"card_id": card.card_id, "page": page,
                    "sha256": hashlib.sha256(png).hexdigest(), "png_base64": base64.b64encode(png).decode("ascii"),
                    "width": width, "height": height})
            verdict = result["review"]
            if draft_status == "ready_for_downstream":
                if (review_job.status is not QueueStatus.SUCCEEDED or len(histories) != 1 or not verdict
                        or verdict.get("decision") != "pass" or any(item.get("blocking") is not False
                            for item in verdict.get("issues", []))):
                    raise BridgeError("independent_review_incomplete")
                result["outcome"] = "ready_for_staging"
            elif draft_status == "rejected_archived":
                result["outcome"] = "rejected"
        elif generation_row.status is QueueStatus.FAILED:
            result["outcome"] = "failed"
        result["followup_job_ids"] = queues.children
    except Exception as error:
        result.update(outcome="failed", reason=str(error) if isinstance(error, BridgeError) else type(error).__name__)
    result["usage_records"] = ledger.records
    result["usage_summary"] = {"llm_calls": len(ledger.records),
        "input_tokens": sum((row["tokens"] or {}).get("input_tokens", 0) for row in ledger.records),
        "output_tokens": sum((row["tokens"] or {}).get("output_tokens", 0) for row in ledger.records)}
    result["cost_accounting"] = ledger.summary()
    with store._transaction(immediate=True):
        connection.execute("UPDATE rag_celery_attempt SET state='complete',result_json=? WHERE work_id=? AND attempt_id=? AND state='running'",
                           (_json(result), request.work_id, attempt_id))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated-db", type=Path, required=True)
    parser.add_argument("--confirm-isolated-copy", action="store_true")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--pgvector-dsn-file", type=Path, required=True)
    parser.add_argument("--expected-vector-database", required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--allow-paid-model-calls", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_paid_model_calls or not args.confirm_isolated_copy:
        parser.error("Paid calls are disabled; an explicitly isolated copy and paid-call opt-in are both required")
    if not args.isolated_db.is_file() or not args.image_root.is_dir() or args.output.exists():
        parser.error("Existing isolated DB/image root and a new output file are required")
    # Imported only after the explicit opt-in. No .env discovery or service DB.
    from ani_rag.storage.postgres_vector import connect_postgres, PostgresVectorIndex
    from ani_rag.storage.sqlite_workflow import SQLiteImageReader
    from ani_rag.retrieval import VectorEvidenceRetriever
    from openai import OpenAI
    data = json.loads(args.request.read_text(encoding="utf-8"))
    raw = data["request"]
    request = GenerationWorkRequest(**{**raw, "content_type": RagContentType(raw["content_type"]),
        "menu_work_ids": tuple(raw["menu_work_ids"]), "seed_evidence_ids": tuple(raw["seed_evidence_ids"])})
    ledger = UsageLedger()
    key = args.api_key_file.read_text(encoding="utf-8").strip()
    with sqlite3.connect(args.isolated_db.resolve(), timeout=60, check_same_thread=False) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        vector = connect_postgres(args.pgvector_dsn_file.read_text(encoding="utf-8").strip(),
                                  expected_database=args.expected_vector_database, read_only=True)
        try:
            store = SQLiteRagStore(connection)
            images = SQLiteImageReader(store)
            # Refuse paths outside the explicitly selected preserved asset root.
            root = args.image_root.resolve()
            for row in connection.execute("SELECT local_path FROM rag_image_asset WHERE local_path IS NOT NULL"):
                if not Path(row[0]).resolve().is_relative_to(root):
                    raise BridgeError("image_path_outside_explicit_root")
            retriever = VectorEvidenceRetriever(store, PostgresVectorIndex(vector), RecordedQueryEmbeddings(OpenAI(api_key=key, max_retries=0), ledger))
            result = asyncio.run(run_single_request(connection, request=request,
                job_id=data["job_id"], attempt_id=data["generation_attempt_id"], retriever=retriever, images=images,
                source_ids=tuple(data["source_ids"]), namespace=data["namespace"], allow_paid=True, usage=ledger,
                generation_model=RecordingOpenAIModel(api_key=key, model=args.model, ledger=ledger, stage="generation"),
                review_model=RecordingOpenAIModel(api_key=key, model=args.model, ledger=ledger, stage="review")))
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(_json(result))
            print(_json({"outcome": result["outcome"], "job_id": result["job_id"], "output": str(args.output.resolve())}))
            return 0 if result["outcome"] != "failed" else 1
        finally:
            vector.close()


if __name__ == "__main__":
    raise SystemExit(main())
