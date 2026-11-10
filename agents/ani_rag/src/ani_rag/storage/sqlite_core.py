"""SQLite repositories for the independent RAG lane.

This module accepts an already-open connection and never creates a database or
applies schema. The caller must provision a new ani_rag database separately and
enable SQLite foreign keys on that connection.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from ani_rag.adapter import canonical_source_url
from ani_rag.catalog import DEFAULT_REFERENCE_DIR, LocalWorkCatalog, temporary_work_id
from ani_rag.interfaces import StoredEvidenceReference, WorkIdentity
from ani_rag.models import (
    AuthorAttribution,
    CollectionMethod,
    CurationContext,
    EvidenceDocument,
    EvidenceExcerpt,
    EvidenceType,
    ParseStatus,
    RagContentType,
    WorkLinkStatus,
    WorkRegistration,
)
from ani_rag.queues import QueueItem, QueueName, QueueStatus
from ani_rag.retrieval import (
    GenerationWorkRequest,
    GenerationWorkStatus,
    RetrievalRequest,
)
from ani_rag.settings import EvergreenSource, SourceStatus


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _evidence_content_hash(document: EvidenceDocument) -> str:
    source_text = document.body or ""
    excerpt_fingerprint = "\n".join(
        f"{item.locator or ''}\n{item.text}" for item in document.excerpts
    )
    return hashlib.sha256(f"{source_text}\n{excerpt_fingerprint}".encode("utf-8")).hexdigest()


_AGENT_WORK_FIELDS = (
    "menu_work_id", "title_ko", "title_ko_source", "title_ko_check", "title_romaji",
    "title_english", "title_native", "first_year", "last_year", "n_entries",
    "popularity", "anilist_ids",
)
_EXTERNAL_FIELDS = (
    "menu_work_id", "source_id", "external_id", "source_url", "canonical_title",
    "format", "year", "registered_at",
)


@contextmanager
def _shared_catalog_lock(directory: Path) -> Iterator[None]:
    lock_path = directory / ".menu_work_catalog.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+b") as stream:
        if os.name == "nt":
            import msvcrt

            if lock_path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_csv(path: Path, fields: Sequence[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames and not set(fields).issubset(reader.fieldnames):
            raise ValueError(f"Unexpected columns in shared catalog file: {path.name}")
        return list(reader)


def _atomic_csv_write(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8-sig", newline="", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _persist_agent_work(menu_work_id: int, registration: WorkRegistration) -> None:
    directory = DEFAULT_REFERENCE_DIR
    works_path = directory / "agent_works.csv"
    external_path = directory / "menu_work_external_entries.csv"
    normalized_name = "".join(
        character for character in unicodedata.normalize("NFKC", registration.name).casefold()
        if character.isalnum()
    )
    title_fields = {"title_ko": "", "title_romaji": "", "title_english": "", "title_native": ""}
    for title in (registration.name, *registration.aliases):
        if re.search(r"[\uac00-\ud7a3]", title):
            title_fields["title_ko"] = title_fields["title_ko"] or title
        elif re.search(r"[\u3040-\u30ff\u3400-\u9fff]", title):
            title_fields["title_native"] = title_fields["title_native"] or title
        else:
            title_fields["title_english"] = title_fields["title_english"] or title
            title_fields["title_romaji"] = title_fields["title_romaji"] or title
    work_row = {
        "menu_work_id": str(menu_work_id),
        **title_fields,
        "title_ko_source": "",
        "title_ko_check": "temporary; team review pending" if title_fields["title_ko"] else "",
        "first_year": "",
        "last_year": "",
        "n_entries": "0",
        "popularity": "0",
        "anilist_ids": "",
    }
    with _shared_catalog_lock(directory):
        works = _read_csv(works_path, _AGENT_WORK_FIELDS)
        existing = next((row for row in works if row.get("menu_work_id") == str(menu_work_id)), None)
        if existing is not None:
            existing_names = [existing.get(field, "") for field in ("title_ko", "title_romaji", "title_english", "title_native")]
            existing_keys = {
                "".join(character for character in unicodedata.normalize("NFKC", value).casefold() if character.isalnum())
                for value in existing_names if value
            }
            if normalized_name not in existing_keys:
                raise ValueError(f"Temporary work ID collision in agent_works.csv: {menu_work_id}")
        else:
            works.append(work_row)
            _atomic_csv_write(works_path, _AGENT_WORK_FIELDS, works)

        for source_id, external_id in registration.external_ids.items():
            if "kitsu" not in source_id.casefold() or not external_id.isdigit():
                continue
            external_rows = _read_csv(external_path, _EXTERNAL_FIELDS)
            match = next((row for row in external_rows if row.get("source_id") == "kitsu_anime_catalog" and row.get("external_id") == external_id), None)
            if match is not None:
                if match.get("menu_work_id") != str(menu_work_id):
                    raise ValueError(f"Kitsu ID already maps to a different menu_work_id: {external_id}")
                continue
            external_rows.append({
                "menu_work_id": str(menu_work_id),
                "source_id": "kitsu_anime_catalog",
                "external_id": external_id,
                "source_url": f"https://kitsu.io/anime/{external_id}",
                "canonical_title": registration.name,
                "format": "",
                "year": "",
                "registered_at": datetime.now(timezone.utc).isoformat(),
            })
            _atomic_csv_write(external_path, _EXTERNAL_FIELDS, external_rows)


class SQLiteRagStore:
    """RAG-only persistence adapter over a caller-owned SQLite connection.

    It does not open a path, execute DDL, seed source rows, or migrate a database.
    The caller must supply a connection created with ``check_same_thread=False``.
    A shared connection is serialized and its blocking sqlite3 calls run in a
    worker thread. Callers must not use the connection outside this adapter.
    """

    def __init__(self, connection: sqlite3.Connection, *, menu_work_id_floor: int = 0) -> None:
        if menu_work_id_floor < 0:
            raise ValueError("menu_work_id_floor cannot be negative")
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._menu_work_id_floor = menu_work_id_floor

    async def register_source(self, source: EvergreenSource) -> None:
        """Register only a source explicitly approved in the RAG allowlist."""
        if source.status is not SourceStatus.APPROVED:
            raise PermissionError("Only an approved RAG source can be registered for persistence")
        payload = _json({
            "source_id": source.source_id,
            "name": source.name,
            "mcp_server": source.mcp_server,
            "content_types": [item.value for item in source.content_types],
            "allowed_tools": sorted(source.allowed_tools),
            "allowed_hosts": sorted(source.allowed_hosts),
            "tool_bindings": dict(source.tool_bindings),
            "status": source.status.value,
            "usage_notes": source.usage_notes,
            "topic_id": source.topic_id,
            "media_name": source.name,
            "source_kind": source.source_kind,
            "collect_method": source.collect_method,
            "site_url": source.site_url,
            "feed_url": source.feed_url,
            "source_grade": source.source_grade,
            "copyright_status": source.copyright_status,
            "license_basis": source.license_basis,
            "is_collecting": source.is_collecting,
        })
        await asyncio.to_thread(self._register_source, source, payload)

    def _register_source(self, source: EvergreenSource, payload: str) -> None:
        source_id = source.source_id
        now = datetime.now(timezone.utc).isoformat()
        with self._transaction():
            self._connection.execute(
                """INSERT INTO rag_source(source_id,topic_id,content_types_json,media_name,source_kind,collect_method,
                   site_url,feed_url,source_grade,copyright_status,license_basis,is_collecting,
                   configuration_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_id) DO UPDATE SET
                     topic_id=excluded.topic_id,media_name=excluded.media_name,
                     content_types_json=excluded.content_types_json,
                     source_kind=excluded.source_kind,collect_method=excluded.collect_method,
                     site_url=excluded.site_url,feed_url=excluded.feed_url,source_grade=excluded.source_grade,
                     copyright_status=excluded.copyright_status,license_basis=excluded.license_basis,
                     is_collecting=excluded.is_collecting,
                     configuration_json=excluded.configuration_json, updated_at=excluded.updated_at""",
                (source_id,source.topic_id,_json([item.value for item in source.content_types]),
                 source.name,source.source_kind,source.collect_method,
                 source.site_url,source.feed_url,source.source_grade,source.copyright_status,
                 source.license_basis,int(source.is_collecting),payload,now,now),
            )

    async def upsert_and_schedule_generation(
        self, document: EvidenceDocument, *, retrieval_query: str
    ) -> StoredEvidenceReference:
        if not retrieval_query.strip():
            raise ValueError("A bounded retrieval query is required for generation backlog")
        return await asyncio.to_thread(self._upsert_and_schedule_generation, document, retrieval_query)

    def _upsert_and_schedule_generation(self, document: EvidenceDocument, query: str) -> StoredEvidenceReference:
        canonical_url = canonical_source_url(document.source_url)
        body_hash = _evidence_content_hash(document)
        now = datetime.now(timezone.utc).isoformat()
        query_digest = hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:20]
        disposition = "inserted"
        canonical_id = document.document_id
        agent_work_row: tuple[int, WorkRegistration] | None = None
        with self._transaction(immediate=True):
            source = self._connection.execute(
                "SELECT source_site_id FROM rag_source WHERE source_id=?", (document.source_id,)
            ).fetchone()
            if source is None:
                raise ValueError(f"Source {document.source_id!r} must be explicitly registered before evidence storage")
            existing = self._connection.execute(
                "SELECT document_id FROM rag_document WHERE source_id=? AND canonical_url=? AND content_hash=?",
                (document.source_id, canonical_url, body_hash),
            ).fetchone()
            stored_document = document
            if existing is not None:
                canonical_id = str(existing["document_id"])
                disposition = "duplicate"
                stored_document = replace(document, document_id=canonical_id)
                new_work_row: tuple[int, WorkRegistration] | None = None
                if document.new_work_registration is not None:
                    assigned_id, is_new = self._resolve_or_assign_work_id(document.new_work_registration)
                    if assigned_id == temporary_work_id(
                        document.new_work_registration.name,
                        document.new_work_registration.external_ids,
                    ):
                        agent_work_row = (assigned_id, document.new_work_registration)
                    stored_document = replace(
                        stored_document, work_link_status=WorkLinkStatus.MATCHED,
                        menu_work_ids=(assigned_id,), candidate_menu_work_ids=(),
                        new_work_registration=None,
                        metadata={**document.metadata,
                                  "work_match_method": document.metadata.get("work_match_method", "llm_source_verified")},
                    )
                    if is_new:
                        new_work_row = (assigned_id, document.new_work_registration)
                if stored_document.work_link_status is WorkLinkStatus.MATCHED:
                    self._connection.execute("DELETE FROM rag_work_link WHERE document_id=?", (canonical_id,))
                    if new_work_row is not None:
                        assigned_id, registration = new_work_row
                        self._connection.execute(
                            """INSERT INTO rag_work_catalog(menu_work_id,canonical_name,aliases_json,external_ids_json,
                               topic_id,identity_source_document_id,identity_source_url,identity_evidence_quote,created_at)
                               VALUES(?,?,?,?,NULL,?,?,?,?)""",
                            (assigned_id, registration.name, _json(list(registration.aliases)),
                             _json(dict(registration.external_ids)), canonical_id,
                             stored_document.source_url, registration.evidence_quote, now),
                        )
                    for index, work_id in enumerate(stored_document.menu_work_ids):
                        self._connection.execute(
                            "INSERT INTO rag_work_link(link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on) VALUES(?,?,?,?,?,?)",
                            (f"wl_{canonical_id}_{index}", canonical_id, work_id, None,
                             WorkLinkStatus.MATCHED.value, stored_document.metadata.get("work_match_method")),
                        )
            else:
                new_work_row: tuple[int, WorkRegistration] | None = None
                if document.new_work_registration is not None:
                    assigned_id, is_new = self._resolve_or_assign_work_id(document.new_work_registration)
                    if assigned_id == temporary_work_id(
                        document.new_work_registration.name,
                        document.new_work_registration.external_ids,
                    ):
                        agent_work_row = (assigned_id, document.new_work_registration)
                    stored_document = replace(
                        document, work_link_status=WorkLinkStatus.MATCHED,
                        menu_work_ids=(assigned_id,), candidate_menu_work_ids=(),
                        new_work_registration=None,
                        metadata={**document.metadata,
                                  "work_match_method": document.metadata.get("work_match_method", "llm_source_verified")},
                    )
                    if is_new:
                        new_work_row = (assigned_id, document.new_work_registration)
                curation = None
                if stored_document.curation_context is not None:
                    curation = _json({
                        "window_start": _iso(stored_document.curation_context.window_start),
                        "window_end": _iso(stored_document.curation_context.window_end),
                        "sample_size": stored_document.curation_context.sample_size,
                        "aggregation_method": stored_document.curation_context.aggregation_method,
                        "source_context": stored_document.curation_context.source_context,
                    })
                self._connection.execute(
                    """INSERT INTO rag_document(
                         document_id,source_id,source_site_id,event_detail_id,external_id,content_type,evidence_type,collection_method,
                         title,raw_text,original_language,url,canonical_url,fetched_at,published_at,valid_from,valid_until,
                         author_name,author_attribution,curation_context_json,temporal_context,
                         metadata_json,raw_payload_json,parse_status,parse_error,purge_at,claims_json,content_hash,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (stored_document.document_id, stored_document.source_id, source["source_site_id"], stored_document.event_detail_id, stored_document.source_native_id,
                     stored_document.content_type.value, stored_document.evidence_type.value, stored_document.collection_method.value, stored_document.title,
                     stored_document.body, stored_document.language, stored_document.source_url, canonical_url, _iso(stored_document.retrieved_at), _iso(stored_document.published_at),
                     _iso(stored_document.valid_from), _iso(stored_document.valid_until), stored_document.author_name,
                     stored_document.author_attribution.value, curation, stored_document.temporal_context,
                     _json({**stored_document.metadata, **({"publisher": stored_document.publisher} if stored_document.publisher else {})}),
                     _json(stored_document.raw_payload), stored_document.parse_status.value,
                     stored_document.parse_error, _iso(stored_document.purge_at), _json(stored_document.claims), body_hash, now),
                )
                for index, excerpt in enumerate(stored_document.excerpts):
                    self._connection.execute(
                        "INSERT INTO rag_excerpt(excerpt_id,document_id,locator,excerpt_text,supports_claim_index) VALUES(?,?,?,?,?)",
                        (self._excerpt_id(stored_document.document_id, index), stored_document.document_id,
                         excerpt.locator, excerpt.text, excerpt.supports_claim_index),
                    )
                if new_work_row is not None:
                    assigned_id, registration = new_work_row
                    self._connection.execute(
                        """INSERT INTO rag_work_catalog(menu_work_id,canonical_name,aliases_json,external_ids_json,
                           topic_id,identity_source_document_id,identity_source_url,identity_evidence_quote,created_at)
                           VALUES(?,?,?,?,NULL,?,?,?,?)""",
                        (assigned_id, registration.name, _json(list(registration.aliases)),
                         _json(dict(registration.external_ids)), stored_document.document_id,
                         stored_document.source_url, registration.evidence_quote, now),
                    )
                links = [(WorkLinkStatus.MATCHED, work_id, None) for work_id in stored_document.menu_work_ids]
                links.extend((WorkLinkStatus.AMBIGUOUS, None, work_id) for work_id in stored_document.candidate_menu_work_ids)
                if not links:
                    links.append((stored_document.work_link_status, None, None))
                for index, (status, work_id, candidate_id) in enumerate(links):
                    self._connection.execute(
                        "INSERT INTO rag_work_link(link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on) VALUES(?,?,?,?,?,?)",
                        (f"wl_{stored_document.document_id}_{index}", stored_document.document_id, work_id,
                         candidate_id, status.value, stored_document.metadata.get("work_match_method")),
                    )
            work_id = f"gw_{canonical_id}_{query_digest}"
            self._connection.execute(
                """INSERT OR IGNORE INTO rag_generation_work(
                     work_id,content_type,retrieval_query,seed_evidence_ids_json,menu_work_ids_json,
                     status,queue_job_id,dedupe_key,created_at,updated_at)
                   VALUES(?,?,?,?,?,'pending',NULL,?,?,?)""",
                (work_id, document.content_type.value, query.strip(), _json([canonical_id]),
                 _json(list(stored_document.menu_work_ids) if disposition == "inserted" else list(document.menu_work_ids)), f"generation:{work_id}", now, now),
            )
            if disposition == "duplicate" and stored_document.work_link_status is WorkLinkStatus.MATCHED:
                self._connection.execute(
                    "UPDATE rag_generation_work SET menu_work_ids_json=?,updated_at=? WHERE seed_evidence_ids_json=? AND status IN ('pending','enqueued','waiting_for_evidence')",
                    (_json(list(stored_document.menu_work_ids)), now, _json([canonical_id])),
                )
                backlog_rows = self._connection.execute(
                    "SELECT queue_job_id FROM rag_generation_work WHERE seed_evidence_ids_json=? AND queue_job_id IS NOT NULL",
                    (_json([canonical_id]),),
                ).fetchall()
                for backlog_row in backlog_rows:
                    queued = self._connection.execute(
                        "SELECT payload_json FROM rag_queue_job WHERE job_id=? AND status='pending'",
                        (backlog_row["queue_job_id"],),
                    ).fetchone()
                    if queued is not None:
                        queue_payload = json.loads(queued["payload_json"])
                        queue_payload["menu_work_ids"] = list(stored_document.menu_work_ids)
                        self._connection.execute(
                            "UPDATE rag_queue_job SET payload_json=?,updated_at=? WHERE job_id=? AND status='pending'",
                            (_json(queue_payload), now, backlog_row["queue_job_id"]),
                        )
        if agent_work_row is not None:
            _persist_agent_work(*agent_work_row)
        return StoredEvidenceReference(canonical_id, canonical_id, disposition)

    def _resolve_or_assign_work_id(self, registration: WorkRegistration) -> tuple[int, bool]:
        """Resolve DB-local duplicates or assign the shared deterministic temporary ID."""
        rows = self._connection.execute(
            "SELECT menu_work_id,canonical_name,aliases_json,external_ids_json FROM rag_work_catalog"
        ).fetchall()
        wanted_name = registration.name.casefold().strip()
        wanted_external = {(key.casefold().strip(), value.casefold().strip()) for key, value in registration.external_ids.items()}
        for row in rows:
            name = str(row["canonical_name"]).casefold().strip()
            existing_external = json.loads(row["external_ids_json"])
            external = {(str(key).casefold().strip(), str(value).casefold().strip()) for key, value in existing_external.items()}
            if wanted_name == name or wanted_external & external:
                return int(row["menu_work_id"]), False
        registration_titles = (registration.name, *registration.aliases)
        local = LocalWorkCatalog()
        local_result = local.resolve_sync(
            WorkIdentity(titles=registration_titles, external_ids=registration.external_ids)
        )
        if local_result.status is WorkLinkStatus.MATCHED:
            return local_result.menu_work_ids[0], True
        if local_result.status is WorkLinkStatus.AMBIGUOUS:
            raise ValueError("New-work registration conflicts with multiple shared catalog IDs")
        assigned_id = temporary_work_id(registration.name, registration.external_ids)
        known_titles = local.titles_for_work_id(assigned_id)
        normalized_title = "".join(
            character for character in unicodedata.normalize("NFKC", registration.name).casefold()
            if character.isalnum()
        )
        known_keys = {
            "".join(character for character in unicodedata.normalize("NFKC", value).casefold() if character.isalnum())
            for value in known_titles
        }
        if known_keys and normalized_title not in known_keys:
            raise ValueError(f"Temporary work ID collision with shared catalog ID {assigned_id}")
        if any(int(row["menu_work_id"]) == assigned_id for row in rows):
            raise ValueError(f"Temporary work ID collision with RAG database ID {assigned_id}")
        return assigned_id, True

    async def fetch_candidates(self, request: RetrievalRequest) -> Sequence[EvidenceDocument]:
        return await asyncio.to_thread(self._fetch_candidates, request)

    async def work_catalog_candidates(self) -> tuple[dict[str, object], ...]:
        """Read RAG-created work identities for matching; this does not create or migrate schema."""
        return await asyncio.to_thread(self._work_catalog_candidates)

    def _work_catalog_candidates(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT menu_work_id,canonical_name,aliases_json,external_ids_json FROM rag_work_catalog ORDER BY menu_work_id"
            ).fetchall()
            return tuple({
                "menu_work_id": int(row["menu_work_id"]),
                "canonical_name": str(row["canonical_name"]),
                "titles": [str(row["canonical_name"]), *json.loads(row["aliases_json"])],
                "external_ids": json.loads(row["external_ids_json"]),
            } for row in rows)

    def _fetch_candidates(self, request: RetrievalRequest) -> Sequence[EvidenceDocument]:
        with self._lock:
            selected: list[str] = []
            if request.seed_evidence_ids:
                marks = ",".join("?" for _ in request.seed_evidence_ids)
                rows = self._connection.execute(
                    f"SELECT document_id FROM rag_document WHERE content_type=? AND document_id IN ({marks})",
                    (request.content_type.value, *request.seed_evidence_ids),
                ).fetchall()
                selected.extend(str(row["document_id"]) for row in rows)
            remaining = request.candidate_limit - len(selected)
            if remaining > 0:
                params: list[object] = [request.content_type.value]
                work_clause = ""
                if request.menu_work_ids:
                    work_marks = ",".join("?" for _ in request.menu_work_ids)
                    work_clause = f" AND EXISTS (SELECT 1 FROM rag_work_link w WHERE w.document_id=d.document_id AND w.status='matched' AND w.menu_work_id IN ({work_marks}))"
                    params.extend(request.menu_work_ids)
                excluded = ""
                if selected:
                    excluded = " AND d.document_id NOT IN (" + ",".join("?" for _ in selected) + ")"
                    params.extend(selected)
                params.append(remaining)
                rows = self._connection.execute(
                    "SELECT d.document_id FROM rag_document d WHERE d.content_type=?" + work_clause + excluded +
                    " ORDER BY d.published_at IS NULL, d.published_at DESC, d.fetched_at DESC LIMIT ?", params
                ).fetchall()
                selected.extend(str(row["document_id"]) for row in rows)
            return tuple(self._load_document(document_id) for document_id in selected[:request.candidate_limit])

    def _load_document(self, document_id: str) -> EvidenceDocument:
        row = self._connection.execute("SELECT * FROM rag_document WHERE document_id=?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        excerpt_rows = self._connection.execute(
            "SELECT locator,excerpt_text,supports_claim_index FROM rag_excerpt WHERE document_id=? ORDER BY excerpt_id",
            (document_id,),
        ).fetchall()
        link_rows = self._connection.execute(
            "SELECT menu_work_id,candidate_menu_work_id,status FROM rag_work_link WHERE document_id=? ORDER BY link_id",
            (document_id,),
        ).fetchall()
        statuses = {WorkLinkStatus(item["status"]) for item in link_rows}
        status = next(iter(statuses)) if len(statuses) == 1 else WorkLinkStatus.UNRESOLVED
        menu_ids = tuple(sorted({int(item["menu_work_id"]) for item in link_rows if item["menu_work_id"] is not None}))
        candidate_ids = tuple(sorted({int(item["candidate_menu_work_id"]) for item in link_rows if item["candidate_menu_work_id"] is not None}))
        if menu_ids:
            status = WorkLinkStatus.MATCHED
        elif candidate_ids:
            status = WorkLinkStatus.AMBIGUOUS
        curation_raw = json.loads(row["curation_context_json"]) if row["curation_context_json"] else None
        curation = CurationContext(
            window_start=date.fromisoformat(curation_raw["window_start"]) if curation_raw and curation_raw.get("window_start") else None,
            window_end=date.fromisoformat(curation_raw["window_end"]) if curation_raw and curation_raw.get("window_end") else None,
            sample_size=curation_raw.get("sample_size") if curation_raw else None,
            aggregation_method=curation_raw.get("aggregation_method") if curation_raw else None,
            source_context=curation_raw.get("source_context") if curation_raw else None,
        ) if curation_raw is not None else None
        return EvidenceDocument(
            document_id=str(row["document_id"]), content_type=RagContentType(row["content_type"]),
            title=str(row["title"]), source_url=str(row["url"]), source_id=str(row["source_id"]),
            retrieved_at=datetime.fromisoformat(row["fetched_at"]), evidence_type=EvidenceType(row["evidence_type"]),
            collection_method=CollectionMethod(row["collection_method"]), language=str(row["original_language"]),
            source_native_id=row["external_id"], publisher=json.loads(row["metadata_json"]).get("publisher"), body=row["raw_text"],
            raw_payload=json.loads(row["raw_payload_json"]), event_detail_id=row["event_detail_id"],
            parse_status=ParseStatus(row["parse_status"]), parse_error=row["parse_error"], purge_at=_datetime(row["purge_at"]),
            excerpts=tuple(EvidenceExcerpt(item["excerpt_text"], item["locator"], item["supports_claim_index"]) for item in excerpt_rows),
            published_at=_datetime(row["published_at"]), valid_from=_datetime(row["valid_from"]), valid_until=_datetime(row["valid_until"]),
            work_link_status=status, menu_work_ids=menu_ids, candidate_menu_work_ids=candidate_ids,
            author_name=row["author_name"], author_attribution=AuthorAttribution(row["author_attribution"]),
            claims=tuple(json.loads(row["claims_json"])), curation_context=curation,
            temporal_context=row["temporal_context"], metadata=json.loads(row["metadata_json"]),
        )

    async def pending_generation_work(self, *, limit: int) -> Sequence[GenerationWorkRequest]:
        if not 1 <= limit <= 500:
            raise ValueError("Backlog limit must be between 1 and 500")
        return await asyncio.to_thread(self._pending_generation_work, limit)

    def _pending_generation_work(self, limit: int) -> Sequence[GenerationWorkRequest]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM rag_generation_work WHERE status='pending' ORDER BY created_at,work_id LIMIT ?", (limit,)
            ).fetchall()
            return tuple(GenerationWorkRequest(
                work_id=str(row["work_id"]), query=str(row["retrieval_query"]),
                content_type=RagContentType(row["content_type"]),
                seed_evidence_ids=tuple(json.loads(row["seed_evidence_ids_json"])),
                menu_work_ids=tuple(json.loads(row["menu_work_ids_json"])),
            ) for row in rows)

    async def mark_generation_enqueued(self, work_id: str, job_id: str) -> None:
        await asyncio.to_thread(self._mark_generation_enqueued, work_id, job_id)

    def _mark_generation_enqueued(self, work_id: str, job_id: str) -> None:
        self._update_generation_work(work_id, GenerationWorkStatus.ENQUEUED, job_id=job_id)

    async def mark_generation_result(self, work_id: str, status: GenerationWorkStatus, *, error: str | None = None) -> None:
        await asyncio.to_thread(self._update_generation_work, work_id, status, None, error)

    def _update_generation_work(self, work_id: str, status: GenerationWorkStatus, job_id: str | None = None, error: str | None = None) -> None:
        now = datetime.now().astimezone().isoformat()
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE rag_generation_work SET status=?,queue_job_id=COALESCE(?,queue_job_id),last_error=?,updated_at=? WHERE work_id=?",
                (status.value, job_id, error, now, work_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown generation work {work_id!r}")

    async def enqueue(self, item: QueueItem) -> QueueItem:
        return await asyncio.to_thread(self._enqueue, item)

    def _enqueue(self, item: QueueItem) -> QueueItem:
        now = _iso(item.created_at or datetime.now(timezone.utc))
        updated = _iso(item.updated_at or datetime.now(timezone.utc))
        payload = _json(dict(item.payload or {}))
        with self._transaction():
            inserted = self._connection.execute(
                """INSERT OR IGNORE INTO rag_queue_job(
                     job_id,queue_name,handler_kind,dedupe_key,payload_json,status,retry_count,
                     max_retries,last_error,created_at,updated_at,started_at,finished_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item.job_id, item.queue.value, item.handler_kind, item.dedupe_key, payload,
                 item.status.value, item.retry_count, item.max_retries, item.last_error,
                 now, updated, None, updated if item.status in {QueueStatus.BLOCKED, QueueStatus.CANCELLED} else None),
            )
            if item.dedupe_key is not None:
                row = self._connection.execute(
                    "SELECT * FROM rag_queue_job WHERE queue_name=? AND dedupe_key=?",
                    (item.queue.value, item.dedupe_key),
                ).fetchone()
            else:
                row = self._connection.execute("SELECT * FROM rag_queue_job WHERE job_id=?", (item.job_id,)).fetchone()
            if row is None:
                raise RuntimeError("Queue insert did not produce a retrievable row")
            if inserted.rowcount == 1:
                self._insert_event(item.job_id, None, item.status, updated, item.last_error)
            return self._queue_item(row)

    async def claim(self, queue: QueueName, *, now: datetime, handler_kind: str | None = None) -> QueueItem | None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Queue timestamps must be timezone-aware")
        return await asyncio.to_thread(self._claim, queue, now, handler_kind)

    def _claim(self, queue: QueueName, now: datetime, handler_kind: str | None) -> QueueItem | None:
        stamp = _iso(now)
        with self._transaction(immediate=True):
            sql = "SELECT * FROM rag_queue_job WHERE queue_name=? AND status IN ('pending','retry_wait')"
            params: list[object] = [queue.value]
            if handler_kind is not None:
                sql += " AND handler_kind=?"
                params.append(handler_kind)
            sql += " ORDER BY created_at,job_id LIMIT 1"
            row = self._connection.execute(sql, params).fetchone()
            if row is None:
                return None
            previous = self._queue_item(row)
            claimed = previous.transition(QueueStatus.RUNNING, now=now)
            cursor = self._connection.execute(
                "UPDATE rag_queue_job SET status='running',updated_at=?,started_at=? WHERE job_id=? AND status=?",
                (stamp, stamp, previous.job_id, previous.status.value),
            )
            if cursor.rowcount != 1:
                return None
            self._insert_event(previous.job_id, previous.status, QueueStatus.RUNNING, stamp, None)
            return claimed

    async def save(self, item: QueueItem) -> None:
        await asyncio.to_thread(self._save_queue_item, item)

    def _save_queue_item(self, item: QueueItem) -> None:
        if item.updated_at is None or item.updated_at.tzinfo is None:
            raise ValueError("Persisted queue transitions need an aware updated_at")
        with self._transaction(immediate=True):
            row = self._connection.execute("SELECT * FROM rag_queue_job WHERE job_id=?", (item.job_id,)).fetchone()
            if row is None:
                raise KeyError(item.job_id)
            previous = self._queue_item(row)
            expected = previous.transition(item.status, now=item.updated_at, error=item.last_error)
            if (expected.retry_count != item.retry_count or expected.queue is not item.queue
                    or expected.dedupe_key != item.dedupe_key or dict(expected.payload or {}) != dict(item.payload or {})):
                raise ValueError("Queue item does not match a valid transition from the stored state")
            terminal = item.status in {QueueStatus.SUCCEEDED, QueueStatus.BLOCKED, QueueStatus.FAILED, QueueStatus.CANCELLED}
            cursor = self._connection.execute(
                "UPDATE rag_queue_job SET status=?,retry_count=?,last_error=?,updated_at=?,finished_at=? WHERE job_id=? AND status=?",
                (item.status.value, item.retry_count, item.last_error, _iso(item.updated_at),
                 _iso(item.updated_at) if terminal else None, item.job_id, previous.status.value),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Queue state changed concurrently")
            self._insert_event(item.job_id, previous.status, item.status, _iso(item.updated_at), item.last_error)

    async def get(self, job_id: str) -> QueueItem | None:
        return await asyncio.to_thread(self._get_queue_item, job_id)

    def _get_queue_item(self, job_id: str) -> QueueItem | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM rag_queue_job WHERE job_id=?", (job_id,)).fetchone()
            return self._queue_item(row) if row is not None else None

    @staticmethod
    def _queue_item(row: sqlite3.Row) -> QueueItem:
        return QueueItem(
            job_id=str(row["job_id"]), queue=QueueName(row["queue_name"]), status=QueueStatus(row["status"]),
            retry_count=int(row["retry_count"]), max_retries=int(row["max_retries"]),
            last_error=row["last_error"], created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]), dedupe_key=row["dedupe_key"],
            payload=json.loads(row["payload_json"]), handler_kind=row["handler_kind"],
        )

    def _insert_event(self, job_id: str, before: QueueStatus | None, after: QueueStatus, stamp: str | None, error: str | None) -> None:
        event_id = f"evt_{hashlib.sha256(f'{job_id}:{stamp}:{before}:{after}'.encode()).hexdigest()[:28]}"
        self._connection.execute(
            "INSERT OR IGNORE INTO rag_job_event(event_id,job_id,from_status,to_status,event_at,error,detail_json) VALUES(?,?,?,?,?,?,?)",
            (event_id, job_id, before.value if before else None, after.value, stamp or datetime.now(timezone.utc).isoformat(), error, "{}"),
        )

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[None]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    @staticmethod
    def _excerpt_id(document_id: str, index: int) -> str:
        return f"ex_{hashlib.sha256(f'{document_id}:{index}'.encode()).hexdigest()[:28]}"
