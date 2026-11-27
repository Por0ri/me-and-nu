"""Explicit, disabled-by-default composition of Celery and the native RAG runner.

Only the supplied isolated RAG copy is writable. Source preflight uses a read
transaction, pgvector is read-only, and service DB settings are never accepted.
"""
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import sqlite3
from typing import Mapping

from menu_worker.rag_contract import RagContractError, require, validate_request


@dataclass(frozen=True)
class RagNativeConfig:
    isolated_root: str
    database_path: str
    image_root: str
    api_key_file: str
    vector_dsn_file: str
    expected_vector_database: str
    catalog_work_ids: tuple
    enabled: bool = False
    confirm_isolated_copy: bool = False
    model: str = "gpt-6-luna"
    pricing: Mapping = field(default_factory=dict)
    offline: bool = False


def _paths(config):
    require(config.enabled is True, "rag_generation_disabled")
    require(config.confirm_isolated_copy is True, "rag_isolated_copy_confirmation_required")
    root, database, images = (Path(value) for value in (
        config.isolated_root, config.database_path, config.image_root))
    require(all(path.is_absolute() and path.resolve() == path for path in (root, database, images)),
            "unsafe_rag_runtime_path")
    require(root.is_dir() and database.is_file() and database.is_relative_to(root) and images.is_dir(),
            "rag_isolated_runtime_missing")
    require(isinstance(config.catalog_work_ids, (tuple, list, frozenset)) and config.catalog_work_ids
            and all(type(value) is int and value > 0 for value in config.catalog_work_ids), "invalid_rag_catalog")
    return database, images


@contextmanager
def _readonly(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection


def _content_hash(body, excerpts):
    text = "\n".join(f"{item.get('locator') or ''}\n{item['text']}" for item in excerpts)
    return hashlib.sha256(f"{body or ''}\n{text}".encode("utf-8")).hexdigest()


def _document(connection, document_id, request, *, exact_lane):
    row = connection.execute("SELECT * FROM rag_document WHERE document_id=?", (document_id,)).fetchone()
    require(row is not None, "rag_seed_missing")
    require(row["source_id"] in request.allowed_source_ids and row["parse_status"] in {"parsed", "partial"},
            "rag_evidence_source_mismatch")
    require(connection.execute("SELECT 1 FROM rag_source WHERE source_id=?", (row["source_id"],)).fetchone() is not None,
            "rag_source_unregistered")
    links = connection.execute("SELECT menu_work_id,status FROM rag_work_link WHERE document_id=?", (document_id,)).fetchall()
    require(any(link["menu_work_id"] == request.menu_work_id and link["status"] == "matched" for link in links)
            and all(link["status"] not in {"unresolved", "ambiguous"} for link in links), "rag_evidence_work_mismatch")
    if exact_lane and row["content_type"] != request.content_type:
        require(connection.execute("SELECT 1 FROM rag_document_content_type WHERE document_id=? AND content_type=?",
                                   (document_id, request.content_type)).fetchone() is not None,
                "rag_evidence_type_mismatch")
    from ani_rag.models import RagContentType
    from ani_rag.profiles import get_content_profile
    from ani_rag.storage.excerpt_order import ExcerptOrderError, ordered_excerpt_rows
    accepted = {item.value for item in get_content_profile(RagContentType(request.content_type)).accepted_evidence_types}
    require(row["evidence_type"] in accepted, "rag_evidence_profile_mismatch")
    rows = connection.execute(
        "SELECT excerpt_id,locator,excerpt_text FROM rag_excerpt WHERE document_id=?", (document_id,)
    ).fetchall()
    try:
        ordered = ordered_excerpt_rows(document_id, rows)
    except ExcerptOrderError as error:
        raise RagContractError("rag_evidence_content_changed") from error
    excerpts = [{"locator": item["locator"], "text": item["excerpt_text"]} for item in ordered]
    require(_content_hash(row["raw_text"], excerpts) == row["content_hash"], "rag_evidence_content_changed")
    return row


def _preflight(config, job):
    database, _images = _paths(config)
    validate_pricing(config)
    request = validate_request(job["request"])
    require(job.get("input_sha256") == request.input_sha256, "rag_request_hash_changed")
    require(request.menu_work_id in config.catalog_work_ids, "rag_work_not_in_service_catalog")
    with _readonly(database) as connection:
        for document_id, expected in request.seed_evidence:
            row = _document(connection, document_id, request, exact_lane=True)
            require(row["content_hash"] == expected, "rag_seed_hash_changed")
    return {"seed_count": len(request.seed_evidence), "input_sha256": request.input_sha256,
            "menu_work_id": request.menu_work_id, "provider_calls": 0, "database_writes": 0}


def validate_pricing(config):
    """Missing reviewed rates must stop before any billable provider request.

    Values are explicit USD per million tokens, never fetched or guessed here.
    Unrecognized response model versions remain unknown in the usage ledger.
    """
    if config.offline is True:
        return
    require(isinstance(config.pricing, Mapping), "rag_model_pricing_required")
    for model in (config.model, "text-embedding-3-small"):
        rates = config.pricing.get(model)
        require(isinstance(rates, Mapping) and set(rates) == {"input", "output", "cached_input"},
                "rag_model_pricing_required")
        try:
            amounts = {key: Decimal(str(value)) for key, value in rates.items()}
        except (InvalidOperation, ValueError, TypeError):
            require(False, "invalid_rag_model_pricing")
        require(all(value.is_finite() and value >= 0 for value in amounts.values())
                and amounts["input"] > 0 and (model != config.model or amounts["output"] > 0),
                "invalid_rag_model_pricing")


def _verify_result(config, job, result):
    database, _images = _paths(config)
    request = validate_request(job["request"])
    require(isinstance(result, dict) and isinstance(result.get("evidence"), dict), "invalid_rag_native_evidence")
    evidence = result["evidence"]
    if result.get("outcome") == "ready_for_staging":
        require(bool(evidence), "rag_result_evidence_missing")
    expected_seeds = dict(request.seed_evidence)
    with _readonly(database) as connection:
        # A writer may use only a subset of the retrieved seed material. All
        # original seeds remain immutable, while only cited evidence is exported.
        for document_id, expected in expected_seeds.items():
            seed = _document(connection, document_id, request, exact_lane=True)
            require(seed["content_hash"] == expected, "rag_seed_changed_during_generation")
        for document_id, document in evidence.items():
            require(isinstance(document, dict) and document.get("document_id") == document_id,
                    "rag_result_evidence_identity")
            row = _document(connection, document_id, request, exact_lane=document_id in expected_seeds)
            require(document.get("source_id") == row["source_id"] and document.get("source_url") == row["url"]
                    and document.get("content_type") == row["content_type"]
                    and document.get("work_link_status") == "matched"
                    and request.menu_work_id in document.get("menu_work_ids", []), "rag_result_evidence_scope_changed")
            require(_content_hash(document.get("body"), document.get("excerpts", [])) == row["content_hash"],
                    "rag_result_evidence_content_changed")
            if document_id in expected_seeds:
                require(row["content_hash"] == expected_seeds[document_id], "rag_seed_changed_during_generation")
    result["external_input_sha256"] = request.input_sha256
    return result


class _GuardedModel:
    def __init__(self, model, pulse):
        self.model, self.pulse = model, pulse

    async def generate_json(self, **kwargs):
        self.pulse()
        result = await self.model.generate_json(**kwargs)
        self.pulse()
        return result

    async def generate_json_with_images(self, **kwargs):
        self.pulse()
        result = await self.model.generate_json_with_images(**kwargs)
        self.pulse()
        return result


class _GuardedEmbeddings:
    def __init__(self, embeddings, pulse):
        self.embeddings, self.pulse = embeddings, pulse

    async def embed_query(self, text):
        self.pulse()
        result = await self.embeddings.embed_query(text)
        self.pulse()
        return result


@contextmanager
def _runtime(config, connection, pulse):
    # Provider packages and credential files are touched only inside the
    # explicitly enabled run after source preflight and the caller's start fence.
    from ani_rag.celery_bridge import UsageLedger, RecordingOpenAIModel, RecordedQueryEmbeddings
    from ani_rag.retrieval import VectorEvidenceRetriever
    from ani_rag.storage.postgres_vector import connect_postgres, PostgresVectorIndex
    from ani_rag.storage.sqlite_core import SQLiteRagStore
    from ani_rag.storage.sqlite_workflow import SQLiteImageReader
    from openai import OpenAI
    pulse()
    key = Path(config.api_key_file).read_text(encoding="utf-8").strip()
    dsn = Path(config.vector_dsn_file).read_text(encoding="utf-8").strip()
    require(bool(key) and bool(dsn), "rag_explicit_credentials_required")
    ledger = UsageLedger(pricing=config.pricing)
    vector = connect_postgres(dsn, expected_database=config.expected_vector_database, read_only=True)
    root = Path(config.image_root)

    class Images(SQLiteImageReader):
        def eligible(self, asset):
            path = Path(asset.local_path)
            return (path.is_absolute() and path.resolve().is_relative_to(root) and path.is_file()
                    and path.stat().st_size <= 20 * 1024 * 1024 and asset.content_sha256
                    and hashlib.sha256(path.read_bytes()).hexdigest() == asset.content_sha256)

        async def candidates_for(self, evidence):
            return tuple(asset for asset in await super().candidates_for(evidence) if self.eligible(asset))

        async def get_many(self, ids):
            assets = await super().get_many(ids)
            require(all(self.eligible(asset) for asset in assets.values()), "rag_selected_image_changed")
            return assets

    try:
        store = SQLiteRagStore(connection)
        embeddings = _GuardedEmbeddings(RecordedQueryEmbeddings(OpenAI(api_key=key, max_retries=0), ledger), pulse)
        generator = RecordingOpenAIModel(api_key=key, model=config.model, ledger=ledger, stage="generation")
        reviewer = RecordingOpenAIModel(api_key=key, model=config.model, ledger=ledger, stage="review")
        yield {"retriever": VectorEvidenceRetriever(store, PostgresVectorIndex(vector), embeddings),
               "images": Images(store), "generation_model": _GuardedModel(generator, pulse),
               "review_model": _GuardedModel(reviewer, pulse), "usage": ledger}
    finally:
        vector.close()


def build_native_callbacks(config: RagNativeConfig, *, runtime_factory=None):
    """Return (preflight, run_native) for rag_tasks.execute_delivery.

    Config creation and this factory do no I/O. Injected runtimes are accepted
    only in offline mode, whose output cannot pass the production task validator.
    """
    def preflight(job):
        return _preflight(config, job)

    async def run_native(job, pulse):
        require(config.enabled is True, "rag_generation_disabled")
        require(config.offline is True if runtime_factory is not None else config.offline is False,
                "rag_offline_runtime_required")
        preflight(job)
        pulse()
        database, _images = _paths(config)
        from ani_rag.celery_bridge import run_single_request
        from ani_rag.models import RagContentType
        from ani_rag.retrieval import GenerationWorkRequest
        request = validate_request(job["request"])
        native = GenerationWorkRequest(request.work_id, request.query, RagContentType(request.content_type),
            tuple(key for key, _ in request.seed_evidence), (request.menu_work_id,))
        with closing(sqlite3.connect(database, timeout=60, check_same_thread=False)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            with (runtime_factory or _runtime)(config, connection, pulse) as dependencies:
                result = await run_single_request(connection, request=native, namespace=request.namespace,
                    job_id=job["job_id"], attempt_id=job["generation_attempt_id"],
                    source_ids=request.allowed_source_ids, allow_paid=not config.offline,
                    offline=config.offline, **dependencies)
        # Return measured usage even when the lease/provenance check fails after
        # the producer completed. The outer delivery handler will preserve it.
        try:
            pulse()
            return _verify_result(config, job, result)
        except Exception as error:
            result.update(outcome="failed", producer_status="native_verification_failed",
                          reason=getattr(error, "code", "rag_post_generation_verification_failed"))
            return result

    return preflight, run_native
