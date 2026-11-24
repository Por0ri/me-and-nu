"""SQLite repositories for the independent RAG lane.

This module accepts an already-open connection and never creates a database or
applies schema. The caller must provision a new ani_rag database separately and
enable SQLite foreign keys on that connection.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Mapping, Sequence
from urllib.parse import urlsplit

from ani_rag.adapter import canonical_source_item_url, canonical_source_url
from ani_rag.fact_subject import fact_subjects
from ani_rag.interfaces import SourceReadClaim, StoredEvidenceReference
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
)
from ani_rag.queues import QueueItem, QueueName, QueueStatus, WebPipelineBacklog
from ani_rag.profiles import get_content_profile
from ani_rag.retrieval import (
    GenerationWorkRequest,
    GenerationWorkStatus,
    RetrievalRequest,
    requested_dictionary_fields,
)
from ani_rag.settings import EvergreenSource, SourceStatus
from ani_rag.wikidata_appearance import CharacterCastMatch, archived_character_cast
from ani_rag.source_evidence import WEB_ASSESSMENT_POLICY_VERSION


# Historical ANN child entries can be linked to their parent without an independent match.
_USABLE_WORK_EVIDENCE_SQL = """d.parse_status IN ('parsed','partial')
    AND d.source_id NOT GLOB 'web_discovery:*'
    AND COALESCE(json_extract(d.metadata_json,'$.work_match_method'),'') <> 'ann_explicit_relationship_graph'
    AND (d.evidence_type <> 'editorial_review' OR (
        d.published_at IS NOT NULL AND trim(COALESCE(d.author_name,'')) <> ''
        AND json_array_length(d.claims_json) > 0))
    AND (d.evidence_type <> 'curation_result' OR (
        d.published_at IS NOT NULL AND json_extract(d.curation_context_json,'$.window_start') IS NOT NULL
        AND json_extract(d.curation_context_json,'$.window_end') IS NOT NULL
        AND json_extract(d.curation_context_json,'$.sample_size') IS NOT NULL
        AND trim(COALESCE(json_extract(d.curation_context_json,'$.aggregation_method'),'')) <> ''))
    AND (NULLIF(d.raw_text,'') IS NOT NULL OR EXISTS (
        SELECT 1 FROM rag_excerpt e WHERE e.document_id=d.document_id
    ))"""

# The review worker sets both fields. Older or imported jobs may carry only one.
_GENERATION_REVISION_SQL = """(dedupe_key GLOB 'revision:*'
    OR CAST(json_extract(payload_json, '$.revision_count') AS INTEGER) > 0)"""

_FIRECRAWL_SITE_COOLDOWN_SECONDS = 6 * 60 * 60


def _local_boot_at() -> str | None:
    """Conservative boot boundary for workers sharing this machine's local SQLite."""
    now = datetime.now(timezone.utc)
    try:
        if sys.platform == "win32":
            import ctypes

            ticks = ctypes.windll.kernel32.GetTickCount64
            ticks.restype = ctypes.c_ulonglong
            uptime = ticks() / 1000
        elif hasattr(time, "CLOCK_BOOTTIME"):
            uptime = time.clock_gettime(time.CLOCK_BOOTTIME)
        else:
            return None
    except (AttributeError, OSError):
        return None
    # Be conservative near boot; a running job is never aged out by its duration.
    return (now - timedelta(seconds=uptime + 60)).isoformat()


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _web_page_hash(page: Mapping[str, object]) -> str:
    # Fetch time and the query/snippet that led to the page do not change its contents.
    fields = {key: page.get(key) for key in ("title", "body", "language", "published_at")}
    fields["final_url"] = canonical_source_item_url(str(page["final_url"]))
    return "v2:" + hashlib.sha256(_json(fields).encode("utf-8")).hexdigest()


def _diversify_generation_batch(
    pending: Sequence[GenerationWorkRequest], limit: int,
) -> tuple[GenerationWorkRequest, ...]:
    """Dispatch older work first while balancing available works and content types."""
    remaining = list(enumerate(pending))
    selected: list[GenerationWorkRequest] = []
    work_counts: dict[int | str, int] = {}
    type_counts: dict[str, int] = {}
    pair_counts: dict[tuple[int | str, str], int] = {}
    selected_topics: list[tuple[int | str, set[str]]] = []

    while remaining and len(selected) < limit:
        def priority(item: tuple[int, GenerationWorkRequest]) -> tuple[int, int, int, float, int]:
            order, request = item
            work_key: int | str = min(request.menu_work_ids) if request.menu_work_ids else f"unlinked:{request.work_id}"
            kind = request.content_type.value
            topic_terms = _generation_topic_terms(request.query)
            similarities = [
                len(topic_terms & prior_terms) / max(1, len(topic_terms | prior_terms))
                for prior_work, prior_terms in selected_topics
                if prior_work == work_key and topic_terms and prior_terms
            ]
            return (
                work_counts.get(work_key, 0),
                type_counts.get(kind, 0),
                pair_counts.get((work_key, kind), 0),
                max(similarities, default=0.0),
                order,
            )

        chosen_index = min(range(len(remaining)), key=lambda index: priority(remaining[index]))
        _, request = remaining.pop(chosen_index)
        selected.append(request)
        work_key = min(request.menu_work_ids) if request.menu_work_ids else f"unlinked:{request.work_id}"
        kind = request.content_type.value
        work_counts[work_key] = work_counts.get(work_key, 0) + 1
        type_counts[kind] = type_counts.get(kind, 0) + 1
        pair_counts[(work_key, kind)] = pair_counts.get((work_key, kind), 0) + 1
        selected_topics.append((work_key, _generation_topic_terms(request.query)))
    return tuple(selected)


def _generation_topic_terms(query: str) -> set[str]:
    generic = {"anime", "animation", "guide", "profile", "information", "about", "story", "work"}
    return {
        term for term in re.findall(r"[0-9a-z가-힣]+", query.casefold())
        if len(term) > 1 and term not in generic
    }


_GENERIC_RETRIEVAL_TERMS = frozenset({
    "a", "an", "and", "about", "anime", "character", "characters", "birthday",
    "voice", "actor", "actors", "profile", "work", "guide", "people", "music",
    "the", "this", "that", "with", "from", "what", "who", "story",
})


def _retrieval_terms(query: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        token for token in re.findall(r"[^\W_]+", query.casefold())
        if len(token) > 1 and token not in _GENERIC_RETRIEVAL_TERMS
    ))[:16]


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
        self._local_boot_at = _local_boot_at()

    async def register_source(self, source: EvergreenSource, *, only_if_missing: bool = False) -> None:
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
        await asyncio.to_thread(self._register_source, source, payload, only_if_missing)

    async def claim_source_read(
        self, *, source_id: str, source_native_id: str | None, source_url: str,
        now: datetime, refresh_after_seconds: int, lease_seconds: int = 900,
    ) -> SourceReadClaim:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Source read claim time must be timezone-aware")
        if not source_id.strip() or not 3_600 <= refresh_after_seconds <= 31_536_000:
            raise ValueError("Source read claim needs a source and a one-hour to one-year refresh interval")
        if not 30 <= lease_seconds <= 3_600:
            raise ValueError("Source read lease must be between 30 seconds and one hour")
        canonical_url = canonical_source_item_url(source_url)
        native_id = source_native_id.strip() if source_native_id and source_native_id.strip() else None
        return await asyncio.to_thread(
            self._claim_source_read, source_id, native_id, source_url, canonical_url,
            now, refresh_after_seconds, lease_seconds,
        )

    def _claim_source_read(
        self, source_id: str, source_native_id: str | None, source_url: str,
        canonical_url: str, now: datetime, refresh_after_seconds: int, lease_seconds: int,
    ) -> SourceReadClaim:
        now_text = now.astimezone(timezone.utc).isoformat()
        now_epoch = now.astimezone(timezone.utc).timestamp()
        lease_until = datetime.fromtimestamp(now_epoch + lease_seconds, timezone.utc).isoformat()
        claim_id = "src_" + hashlib.sha256(f"{source_id}\n{canonical_url}".encode("utf-8")).hexdigest()[:32]
        legacy_url = canonical_source_url(source_url)

        with self._transaction(immediate=True):
            existing = self._connection.execute(
                """SELECT * FROM rag_source_read_ledger WHERE source_id=?
                   AND (canonical_url=? OR (? IS NOT NULL AND source_native_id=?))
                   ORDER BY last_success_at DESC LIMIT 2""",
                (source_id, canonical_url, source_native_id, source_native_id),
            ).fetchall()
            if len(existing) > 1:
                raise RuntimeError("Source read ledger has conflicting URL and source-native ID rows")
            row = existing[0] if existing else None

            # Existing corpus rows predate this ledger. Seed the ledger from a
            # matching row so its first search result is not fetched again.
            if row is None:
                params: list[object] = [source_id]
                identity_terms = ["canonical_url IN (?,?)", "url IN (?,?)"]
                params.extend((canonical_url, legacy_url))
                params.extend((source_url, legacy_url))
                if source_native_id is not None:
                    identity_terms.append("external_id=?")
                    params.append(source_native_id)
                old_document = self._connection.execute(
                    "SELECT document_id,external_id,canonical_url,url,fetched_at,content_hash "
                    "FROM rag_document WHERE source_id=? AND (" + " OR ".join(identity_terms) + ") "
                    "ORDER BY fetched_at DESC LIMIT 1", params,
                ).fetchone()
                if old_document is not None:
                    old_fetch = _datetime(str(old_document["fetched_at"]))
                    if old_fetch is not None and old_fetch.tzinfo is None:
                        old_fetch = old_fetch.replace(tzinfo=timezone.utc)
                    last_success = old_fetch.astimezone(timezone.utc).isoformat() if old_fetch else now_text
                    due_at = datetime.fromtimestamp(
                        (old_fetch.timestamp() if old_fetch else now_epoch) + refresh_after_seconds,
                        timezone.utc,
                    ).isoformat()
                    old_url = canonical_source_item_url(str(old_document["url"]))
                    seeded_id = "src_" + hashlib.sha256(f"{source_id}\n{old_url}".encode("utf-8")).hexdigest()[:32]
                    self._connection.execute(
                        """INSERT OR IGNORE INTO rag_source_read_ledger(
                           claim_id,source_id,source_native_id,canonical_url,status,last_success_at,
                           next_check_at,content_hash,document_id,updated_at)
                           VALUES(?,?,?,?,'complete',?,?,?,?,?)""",
                        (seeded_id, source_id, old_document["external_id"] or source_native_id,
                         old_url, last_success, due_at, old_document["content_hash"],
                         old_document["document_id"], now_text),
                    )
                    row = self._connection.execute(
                        "SELECT * FROM rag_source_read_ledger WHERE source_id=? AND canonical_url=?",
                        (source_id, old_url),
                    ).fetchone()
                    if row is not None:
                        claim_id = str(row["claim_id"])

            if row is None:
                self._connection.execute(
                    """INSERT INTO rag_source_read_ledger(
                       claim_id,source_id,source_native_id,canonical_url,status,lease_expires_at,updated_at)
                       VALUES(?,?,?,?,'fetching',?,?)""",
                    (claim_id, source_id, source_native_id, canonical_url, lease_until, now_text),
                )
                return SourceReadClaim(claim_id, True)

            claim_id = str(row["claim_id"])
            if row["status"] == "fetching" and row["lease_expires_at"]:
                if str(row["lease_expires_at"]) > now_text:
                    return SourceReadClaim(claim_id, False, "in_flight")
            if row["last_success_at"] and row["next_check_at"] and str(row["next_check_at"]) > now_text:
                return SourceReadClaim(claim_id, False, "already_read_not_due")
            if row["next_check_at"] and not row["last_success_at"] and str(row["next_check_at"]) > now_text:
                return SourceReadClaim(claim_id, False, "retry_not_due")
            self._connection.execute(
                """UPDATE rag_source_read_ledger SET source_native_id=COALESCE(source_native_id,?),
                   canonical_url=?,status='fetching',lease_expires_at=?,last_error_code=NULL,updated_at=?
                   WHERE claim_id=?""",
                (source_native_id, canonical_url, lease_until, now_text, claim_id),
            )
            return SourceReadClaim(claim_id, True)

    async def complete_source_read(
        self, claim_id: str, *, source_native_id: str | None, document_id: str | None,
        content_hash: str, checked_at: datetime, refresh_after_seconds: int,
    ) -> None:
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("Source read completion time must be timezone-aware")
        await asyncio.to_thread(
            self._complete_source_read, claim_id, source_native_id, document_id,
            content_hash, checked_at, refresh_after_seconds,
        )

    def _complete_source_read(
        self, claim_id: str, source_native_id: str | None, document_id: str | None,
        content_hash: str, checked_at: datetime, refresh_after_seconds: int,
    ) -> None:
        if (document_id is not None and not document_id.strip()) or not content_hash.strip() or not 3_600 <= refresh_after_seconds <= 31_536_000:
            raise ValueError("Source read completion needs a content hash and valid refresh interval")
        checked = checked_at.astimezone(timezone.utc)
        next_check = datetime.fromtimestamp(checked.timestamp() + refresh_after_seconds, timezone.utc).isoformat()
        with self._transaction(immediate=True):
            cursor = self._connection.execute(
                """UPDATE rag_source_read_ledger SET source_native_id=COALESCE(?,source_native_id),
                   status='complete',last_success_at=?,next_check_at=?,lease_expires_at=NULL,
                   content_hash=?,document_id=?,failure_count=0,last_error_code=NULL,updated_at=?
                   WHERE claim_id=? AND status='fetching'""",
                (source_native_id, checked.isoformat(), next_check, content_hash, document_id,
                 checked.isoformat(), claim_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"No active source read claim {claim_id!r}")

    async def release_source_read(self, claim_id: str, *, error_code: str) -> None:
        await asyncio.to_thread(self._release_source_read, claim_id, error_code)

    def _release_source_read(self, claim_id: str, error_code: str) -> None:
        safe_code = re.sub(r"[^A-Za-z0-9_.-]", "_", error_code)[:80] or "read_failed"
        now = datetime.now(timezone.utc)
        with self._transaction(immediate=True):
            row = self._connection.execute(
                "SELECT failure_count FROM rag_source_read_ledger WHERE claim_id=? AND status='fetching'",
                (claim_id,),
            ).fetchone()
            if row is None:
                return
            failures = int(row["failure_count"]) + 1
            retry_seconds = min(86_400, 300 * (2 ** min(failures - 1, 8)))
            retry_at = datetime.fromtimestamp(now.timestamp() + retry_seconds, timezone.utc).isoformat()
            self._connection.execute(
                """UPDATE rag_source_read_ledger SET status='failed',lease_expires_at=NULL,
                   next_check_at=?,failure_count=?,last_error_code=?,updated_at=? WHERE claim_id=?""",
                (retry_at, failures, safe_code, now.isoformat(), claim_id),
            )

    async def register_web_discovery_plan(
        self, plan_id: str, request: dict[str, object], *, interval_seconds: int, now: datetime,
    ) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Web-discovery plan time must be timezone-aware")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", plan_id):
            raise ValueError("Web-discovery plan ID is malformed")
        if not 60 <= interval_seconds <= 31_536_000:
            raise ValueError("Web-discovery interval must be between one minute and one year")
        request_payload = _json(request)
        await asyncio.to_thread(
            self._register_web_discovery_plan, plan_id, request_payload, interval_seconds, now,
        )

    def _register_web_discovery_plan(
        self, plan_id: str, request_payload: str, interval_seconds: int, now: datetime,
    ) -> None:
        now_text = now.astimezone(timezone.utc).isoformat()
        with self._transaction(immediate=True):
            existing = self._connection.execute(
                "SELECT request_json,interval_seconds,is_enabled FROM rag_web_discovery_plan WHERE plan_id=?",
                (plan_id,),
            ).fetchone()
            if existing is None:
                self._connection.execute(
                    """INSERT INTO rag_web_discovery_plan(
                       plan_id,request_json,interval_seconds,is_enabled,next_run_at,created_at,updated_at)
                       VALUES(?,?,?,1,?,?,?)""",
                    (plan_id, request_payload, interval_seconds, now_text, now_text, now_text),
                )
                return
            config_changed = (
                str(existing["request_json"]) != request_payload
                or int(existing["interval_seconds"]) != interval_seconds
                or int(existing["is_enabled"]) != 1
            )
            if config_changed:
                self._connection.execute(
                    """UPDATE rag_web_discovery_plan SET request_json=?,interval_seconds=?,is_enabled=1,
                       next_run_at=?,lease_expires_at=NULL,last_error_code=NULL,updated_at=? WHERE plan_id=?""",
                    (request_payload, interval_seconds, now_text, now_text, plan_id),
                )

    async def disable_web_discovery_plan(self, plan_id: str, *, now: datetime) -> bool:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Web-discovery plan time must be timezone-aware")
        return await asyncio.to_thread(self._disable_web_discovery_plan, plan_id, now)

    def _disable_web_discovery_plan(self, plan_id: str, now: datetime) -> bool:
        with self._transaction(immediate=True):
            cursor = self._connection.execute(
                """UPDATE rag_web_discovery_plan SET is_enabled=0,lease_expires_at=NULL,updated_at=?
                   WHERE plan_id=?""",
                (now.astimezone(timezone.utc).isoformat(), plan_id),
            )
            return cursor.rowcount == 1

    async def claim_due_web_discovery_plans(
        self, *, now: datetime, lease_seconds: int = 300, limit: int = 20,
    ) -> tuple[dict[str, object], ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Web-discovery scheduler time must be timezone-aware")
        if not 30 <= lease_seconds <= 3_600 or not 1 <= limit <= 100:
            raise ValueError("Web-discovery scheduler claim bounds are invalid")
        return await asyncio.to_thread(
            self._claim_due_web_discovery_plans, now, lease_seconds, limit,
        )

    def _claim_due_web_discovery_plans(
        self, now: datetime, lease_seconds: int, limit: int,
    ) -> tuple[dict[str, object], ...]:
        now_utc = now.astimezone(timezone.utc)
        now_text = now_utc.isoformat()
        lease_until = datetime.fromtimestamp(
            now_utc.timestamp() + lease_seconds, timezone.utc,
        ).isoformat()
        claimed: list[dict[str, object]] = []
        with self._transaction(immediate=True):
            rows = self._connection.execute(
                """SELECT * FROM rag_web_discovery_plan WHERE is_enabled=1 AND next_run_at<=?
                   AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                   ORDER BY next_run_at,plan_id LIMIT ?""",
                (now_text, now_text, limit),
            ).fetchall()
            for row in rows:
                cursor = self._connection.execute(
                    """UPDATE rag_web_discovery_plan SET lease_expires_at=?,updated_at=?
                       WHERE plan_id=? AND is_enabled=1 AND next_run_at=?
                       AND (lease_expires_at IS NULL OR lease_expires_at<=?)""",
                    (lease_until, now_text, str(row["plan_id"]), str(row["next_run_at"]), now_text),
                )
                if cursor.rowcount != 1:
                    continue
                claimed.append({
                    "plan_id": str(row["plan_id"]),
                    "request": json.loads(row["request_json"]),
                    "interval_seconds": int(row["interval_seconds"]),
                    "scheduled_for": str(row["next_run_at"]),
                    "lease_token": lease_until,
                })
        return tuple(claimed)

    async def complete_web_discovery_plan(
        self, plan_id: str, *, scheduled_for: datetime, lease_token: str, now: datetime,
    ) -> None:
        if any(value.tzinfo is None or value.utcoffset() is None for value in (scheduled_for, now)):
            raise ValueError("Web-discovery plan completion times must be timezone-aware")
        await asyncio.to_thread(
            self._complete_web_discovery_plan, plan_id, scheduled_for, lease_token, now,
        )

    def _complete_web_discovery_plan(
        self, plan_id: str, scheduled_for: datetime, lease_token: str, now: datetime,
    ) -> None:
        now_utc = now.astimezone(timezone.utc)
        now_text = now_utc.isoformat()
        scheduled_text = scheduled_for.astimezone(timezone.utc).isoformat()
        with self._transaction(immediate=True):
            row = self._connection.execute(
                "SELECT interval_seconds FROM rag_web_discovery_plan WHERE plan_id=? AND lease_expires_at=?",
                (plan_id, lease_token),
            ).fetchone()
            if row is None:
                raise KeyError(f"No claimed web-discovery plan {plan_id!r}")
            next_run = datetime.fromtimestamp(
                now_utc.timestamp() + int(row["interval_seconds"]), timezone.utc,
            ).isoformat()
            cursor = self._connection.execute(
                """UPDATE rag_web_discovery_plan SET next_run_at=?,lease_expires_at=NULL,
                   last_enqueued_at=?,last_error_code=NULL,updated_at=? WHERE plan_id=? AND lease_expires_at=?""",
                (next_run, scheduled_text, now_text, plan_id, lease_token),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"No web-discovery plan {plan_id!r}")

    async def release_web_discovery_plan(
        self, plan_id: str, *, lease_token: str, error_code: str,
    ) -> None:
        await asyncio.to_thread(self._release_web_discovery_plan, plan_id, lease_token, error_code)

    def _release_web_discovery_plan(self, plan_id: str, lease_token: str, error_code: str) -> None:
        safe_code = re.sub(r"[^A-Za-z0-9_.-]", "_", error_code)[:80] or "dispatch_failed"
        now_text = datetime.now(timezone.utc).isoformat()
        with self._transaction(immediate=True):
            self._connection.execute(
                """UPDATE rag_web_discovery_plan SET lease_expires_at=NULL,last_error_code=?,updated_at=?
                   WHERE plan_id=? AND lease_expires_at=?""",
                (safe_code, now_text, plan_id, lease_token),
            )

    async def web_candidate_assessment_revision(
        self, source_url: str, *, now: datetime, key_prefix: str, reference_version: str,
    ) -> str | None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Candidate revision time must be timezone-aware")
        canonical_url = canonical_source_item_url(source_url)
        return await asyncio.to_thread(
            self._web_candidate_assessment_revision, canonical_url, now, key_prefix, reference_version,
        )

    def _web_candidate_assessment_revision(
        self, canonical_url: str, now: datetime, key_prefix: str, reference_version: str,
    ) -> str | None:
        now_utc = now.astimezone(timezone.utc)
        with self._lock:
            pending = self._connection.execute(
                """SELECT 1 FROM rag_queue_job WHERE queue_name='collection' AND dedupe_key GLOB ?
                   AND status IN ('pending','retry_wait','running')
                   AND NOT (status='running' AND COALESCE(
                       julianday(started_at)<julianday(?) AND julianday(updated_at)<julianday(?), 0))
                   LIMIT 1""", (key_prefix + ":*", self._local_boot_at, self._local_boot_at),
            ).fetchone()
            if pending:
                return None
            cooldown = self._web_candidate_site_cooldown(canonical_url, now_utc.isoformat())
            if cooldown and not self._connection.execute(
                """SELECT 1 FROM rag_web_candidate_page_cache WHERE canonical_url=?
                   AND status='ready' AND expires_at>?""", (canonical_url, now_utc.isoformat()),
            ).fetchone():
                return None
            row = self._connection.execute(
                """SELECT last_fetched_at,next_check_at,last_error_code,last_content_hash
                   FROM rag_web_candidate_url_ledger WHERE canonical_url=?""",
                (canonical_url,),
            ).fetchone()
            if row is None:
                return f"unread:{reference_version}"
            next_check = _datetime(row["next_check_at"])
            if row["last_error_code"] == "unsupported_target":
                return None
            if next_check is not None and next_check <= now_utc:
                return f"due:{next_check.isoformat()}:{reference_version}"
            if row["last_error_code"] and next_check is not None:
                return None
            signature = f"{row['last_content_hash']}:{reference_version}"
            if self._candidate_assessment_owner(key_prefix, signature):
                return None
            return f"read:{row['last_fetched_at']}:{reference_version}"

    def _web_candidate_site_cooldown(self, canonical_url: str, now_text: str) -> str | None:
        # Candidate URLs accept only HTTPS with an exact DNS host. Include the
        # slash so sibling domains or similarly named hosts cannot match.
        prefix = f"https://{urlsplit(canonical_url).netloc}/"
        row = self._connection.execute(
            """SELECT MAX(retry_after) FROM rag_web_candidate_page_cache
               WHERE canonical_url GLOB ? AND status='failed'
                 AND last_error_code='firecrawl_site_unsupported' AND retry_after>?""",
            (prefix + "*", now_text),
        ).fetchone()
        return row[0]

    def _candidate_assessment_owner(self, key_prefix: str, signature: str) -> str | None:
        # Re-evaluate historical holds once under the new policy. An identical
        # successful assessment has already handed the article to generation.
        previous = signature.removesuffix(":" + WEB_ASSESSMENT_POLICY_VERSION)
        row = self._connection.execute(
            """SELECT job_id FROM rag_queue_job WHERE queue_name='collection' AND dedupe_key GLOB ?
               AND (json_extract(payload_json,'$.assessment_inputs')=?
                    OR (status='succeeded' AND json_extract(payload_json,'$.assessment_inputs')=?))
               AND (status='succeeded'
                    OR (status='running' AND NOT COALESCE(
                        julianday(started_at)<julianday(?) AND julianday(updated_at)<julianday(?), 0))
                    OR (status='blocked' AND last_error GLOB 'candidate_*'))
               LIMIT 1""", (key_prefix + ":*", signature, previous, self._local_boot_at, self._local_boot_at),
        ).fetchone()
        return str(row["job_id"]) if row else None

    async def claim_web_assessment_inputs(
        self, job: QueueItem, *, source_url: str, key_prefix: str, page: Mapping[str, object],
        reference_version: str,
    ) -> QueueItem | None:
        return await asyncio.to_thread(
            self._claim_web_assessment_inputs, job, canonical_source_item_url(source_url),
            key_prefix, page, reference_version,
        )

    def _claim_web_assessment_inputs(
        self, job: QueueItem, canonical_url: str, key_prefix: str, page: Mapping[str, object],
        reference_version: str,
    ) -> QueueItem | None:
        page_hash = _web_page_hash(page)
        signature = f"{page_hash}:{reference_version}"
        with self._transaction(immediate=True):
            # Old caches hashed fetched_at as well. Upgrade the exact cached read lazily.
            self._connection.execute(
                """UPDATE rag_web_candidate_url_ledger SET last_content_hash=?
                   WHERE canonical_url=? AND last_fetched_at=? AND last_content_hash NOT LIKE 'v2:%'""",
                (page_hash, canonical_url, page["fetched_at"]),
            )
            owner = self._candidate_assessment_owner(key_prefix, signature)
            if owner is not None and owner != job.job_id:
                return None
            payload = {**(job.payload or {}), "assessment_inputs": signature}
            changed = self._connection.execute(
                "UPDATE rag_queue_job SET payload_json=? WHERE job_id=? AND status='running'",
                (_json(payload), job.job_id),
            )
            if changed.rowcount != 1:
                raise RuntimeError("Candidate assessment no longer owns a running job")
            return replace(job, payload=payload)

    async def claim_web_candidate_page(
        self, source_url: str, *, now: datetime, cache_seconds: int = 2_592_000,
        lease_seconds: int = 900,
    ) -> dict[str, object]:
        """Reuse one untrusted candidate-page read across work/type assessments."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Candidate page claim time must be timezone-aware")
        if not 3_600 <= cache_seconds <= 7_776_000 or not 30 <= lease_seconds <= 3_600:
            raise ValueError("Candidate page cache/lease interval is outside the supported range")
        canonical_url = canonical_source_item_url(source_url)
        cache_id = "web_" + hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:32]
        return await asyncio.to_thread(
            self._claim_web_candidate_page, canonical_url, cache_id, now, cache_seconds, lease_seconds,
        )

    def _claim_web_candidate_page(
        self, canonical_url: str, cache_id: str, now: datetime,
        cache_seconds: int, lease_seconds: int,
    ) -> dict[str, object]:
        now_utc = now.astimezone(timezone.utc)
        now_text = now_utc.isoformat()
        lease_until = datetime.fromtimestamp(now_utc.timestamp() + lease_seconds, timezone.utc).isoformat()
        with self._transaction(immediate=True):
            self._connection.execute(
                """INSERT INTO rag_web_candidate_url_ledger(
                   canonical_url,first_seen_at,last_seen_at,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(canonical_url) DO UPDATE SET
                   last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
                (canonical_url, now_text, now_text, now_text),
            )
            # Expire page bodies without deleting the durable URL history.
            self._connection.execute(
                "DELETE FROM rag_web_candidate_page_cache WHERE status='ready' AND expires_at<=?",
                (now_text,),
            )
            row = self._connection.execute(
                "SELECT * FROM rag_web_candidate_page_cache WHERE canonical_url=?",
                (canonical_url,),
            ).fetchone()
            if row is not None:
                current_id = str(row["cache_id"])
                if row["status"] == "ready" and row["expires_at"] and str(row["expires_at"]) > now_text:
                    return {"status": "cached", "claim_id": current_id, "page": json.loads(row["page_json"])}
                if row["status"] == "failed" and row["last_error_code"] == "unsupported_target":
                    return {"status": "unsupported", "claim_id": current_id, "error_code": "unsupported_target"}
                if row["status"] == "fetching" and row["lease_expires_at"] and str(row["lease_expires_at"]) > now_text:
                    return {"status": "busy", "claim_id": current_id, "lease_expires_at": str(row["lease_expires_at"])}
            cooldown = self._web_candidate_site_cooldown(canonical_url, now_text)
            if cooldown:
                # No retry-wait queue entry: deferred URLs are eligible on
                # rediscovery after this deadline, with a new assessment key.
                self._connection.execute(
                    """UPDATE rag_web_candidate_url_ledger SET next_check_at=?,last_error_code='site_cooldown'
                       WHERE canonical_url=?""", (cooldown, canonical_url),
                )
                return {"status": "site_cooldown", "retry_at": cooldown}
            if row is None:
                self._connection.execute(
                    """INSERT INTO rag_web_candidate_page_cache(
                       cache_id,canonical_url,status,lease_expires_at,updated_at)
                       VALUES(?,?,'fetching',?,?)""",
                    (cache_id, canonical_url, lease_until, now_text),
                )
                return {"status": "acquired", "claim_id": cache_id}
            if row["retry_after"] and str(row["retry_after"]) > now_text:
                return {"status": "retry_later", "claim_id": current_id,
                        "retry_at": str(row["retry_after"])}
            self._connection.execute(
                """UPDATE rag_web_candidate_page_cache SET status='fetching',lease_expires_at=?,
                   retry_after=NULL,last_error_code=NULL,updated_at=? WHERE cache_id=?""",
                (lease_until, now_text, current_id),
            )
            return {"status": "acquired", "claim_id": current_id}

    async def complete_web_candidate_page(
        self, claim_id: str, *, source_url: str, final_url: str,
        page: dict[str, object], fetched_at: datetime, cache_seconds: int = 2_592_000,
    ) -> None:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("Candidate page fetched_at must be timezone-aware")
        await asyncio.to_thread(
            self._complete_web_candidate_page, claim_id, source_url, final_url,
            page, fetched_at, cache_seconds,
        )

    def _complete_web_candidate_page(
        self, claim_id: str, source_url: str, final_url: str,
        page: dict[str, object], fetched_at: datetime, cache_seconds: int,
    ) -> None:
        if not 3_600 <= cache_seconds <= 7_776_000:
            raise ValueError("Candidate page cache interval is outside the supported range")
        canonical_requested = canonical_source_item_url(source_url)
        canonical_final = canonical_source_item_url(final_url)
        fetched = fetched_at.astimezone(timezone.utc)
        expires = datetime.fromtimestamp(fetched.timestamp() + cache_seconds, timezone.utc).isoformat()
        payload = _json(page)
        content_hash = _web_page_hash(page)
        with self._transaction(immediate=True):
            cursor = self._connection.execute(
                """UPDATE rag_web_candidate_page_cache SET final_canonical_url=?,status='ready',
                   page_json=?,fetched_at=?,expires_at=?,lease_expires_at=NULL,retry_after=NULL,
                   failure_count=0,last_error_code=NULL,updated_at=?
                   WHERE cache_id=? AND canonical_url=? AND status='fetching'""",
                (canonical_final, payload, fetched.isoformat(), expires, fetched.isoformat(),
                 claim_id, canonical_requested),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"No active candidate page claim {claim_id!r}")
            self._connection.execute(
                """INSERT INTO rag_web_candidate_url_ledger(
                   canonical_url,final_canonical_url,first_seen_at,last_seen_at,last_fetched_at,
                   next_check_at,last_content_hash,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(canonical_url) DO UPDATE SET
                   final_canonical_url=excluded.final_canonical_url,
                   last_fetched_at=excluded.last_fetched_at,next_check_at=excluded.next_check_at,
                   last_content_hash=excluded.last_content_hash,last_error_code=NULL,
                   updated_at=excluded.updated_at""",
                (canonical_requested, canonical_final, fetched.isoformat(), fetched.isoformat(),
                 fetched.isoformat(), expires, content_hash, fetched.isoformat()),
            )

    async def release_web_candidate_page(self, claim_id: str, *, error_code: str) -> None:
        await asyncio.to_thread(self._release_web_candidate_page, claim_id, error_code)

    def _release_web_candidate_page(self, claim_id: str, error_code: str) -> None:
        safe_code = re.sub(r"[^A-Za-z0-9_.-]", "_", error_code)[:80] or "read_failed"
        now = datetime.now(timezone.utc)
        with self._transaction(immediate=True):
            row = self._connection.execute(
                "SELECT failure_count FROM rag_web_candidate_page_cache WHERE cache_id=? AND status='fetching'",
                (claim_id,),
            ).fetchone()
            if row is None:
                return
            failures = int(row["failure_count"]) + 1
            delay = (_FIRECRAWL_SITE_COOLDOWN_SECONDS if safe_code == "firecrawl_site_unsupported"
                     else min(86_400, 300 * (2 ** min(failures - 1, 8))))
            retry_at = datetime.fromtimestamp(now.timestamp() + delay, timezone.utc).isoformat()
            self._connection.execute(
                """UPDATE rag_web_candidate_page_cache SET status='failed',lease_expires_at=NULL,
                   retry_after=?,failure_count=?,last_error_code=?,updated_at=? WHERE cache_id=?""",
                (retry_at, failures, safe_code, now.isoformat(), claim_id),
            )
            cache_row = self._connection.execute(
                "SELECT canonical_url FROM rag_web_candidate_page_cache WHERE cache_id=?",
                (claim_id,),
            ).fetchone()
            if cache_row is not None:
                self._connection.execute(
                    """UPDATE rag_web_candidate_url_ledger SET next_check_at=?,last_error_code=?,updated_at=?
                       WHERE canonical_url=?""",
                    (retry_at, safe_code, now.isoformat(), str(cache_row["canonical_url"])),
                )

    def _register_source(self, source: EvergreenSource, payload: str, only_if_missing: bool) -> None:
        source_id = source.source_id
        now = datetime.now(timezone.utc).isoformat()
        with self._transaction(immediate=only_if_missing):
            if only_if_missing and self._connection.execute(
                "SELECT 1 FROM rag_source WHERE source_id=?", (source_id,)
            ).fetchone():
                return
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
        self, document: EvidenceDocument, *, retrieval_query: str | None = None,
        schedule_generation: bool = True,
    ) -> StoredEvidenceReference:
        if schedule_generation and not (retrieval_query or "").strip():
            raise ValueError("A bounded retrieval query is required for generation backlog")
        return await asyncio.to_thread(
            self._upsert_and_schedule_generation, document, retrieval_query or "", schedule_generation,
        )

    def _upsert_and_schedule_generation(
        self, document: EvidenceDocument, query: str, schedule_generation: bool = True,
    ) -> StoredEvidenceReference:
        # Registration belongs to the source-aware resolver. A raw proposal
        # does not authorize a work link or a generation job.
        if document.new_work_registration is not None:
            registration = document.new_work_registration
            document = replace(
                document,
                work_link_status=WorkLinkStatus.UNRESOLVED,
                menu_work_ids=(),
                candidate_menu_work_ids=(),
                new_work_registration=None,
                metadata={
                    **document.metadata,
                    "unregistered_work_title": registration.name,
                    "work_registration_status": "pending_identity_review",
                },
            )
        schedule_generation = bool(schedule_generation and document.work_link_status is WorkLinkStatus.MATCHED and document.menu_work_ids)
        canonical_url = canonical_source_url(document.source_url)
        body_hash = document.content_hash
        now = datetime.now(timezone.utc).isoformat()
        query_digest = hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:20] if schedule_generation else None
        disposition = "inserted"
        canonical_id = document.document_id
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
                if stored_document.work_link_status is WorkLinkStatus.MATCHED:
                    if stored_document.source_id == "wikidata_character_catalog":
                        # A periodic reread can recover an unchanged document after cast evidence arrives.
                        identity_metadata = {
                            key: stored_document.metadata.get(key)
                            for key in ("work_match_method", "work_match_appearance_qids",
                                        "work_match_evidence_document_ids", "work_identity_error")
                        }
                        for key in ("seed_match_status", "work_match_seed_menu_work_id"):
                            if key in stored_document.metadata:
                                identity_metadata[key] = stored_document.metadata[key]
                        self._connection.execute(
                            "UPDATE rag_document SET metadata_json=json_patch(metadata_json,?) WHERE document_id=?",
                            (_json(identity_metadata), canonical_id),
                        )
                    self._connection.execute("DELETE FROM rag_work_link WHERE document_id=?", (canonical_id,))
                    for index, work_id in enumerate(stored_document.menu_work_ids):
                        self._connection.execute(
                            "INSERT INTO rag_work_link(link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on) VALUES(?,?,?,?,?,?)",
                            (f"wl_{canonical_id}_{index}", canonical_id, work_id, None,
                             WorkLinkStatus.MATCHED.value, stored_document.metadata.get("work_match_method")),
                        )
            else:
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
            if schedule_generation:
                work_id = f"gw_{canonical_id}_{query_digest}"
                self._connection.execute(
                    """INSERT OR IGNORE INTO rag_generation_work(
                         work_id,content_type,retrieval_query,seed_evidence_ids_json,menu_work_ids_json,
                         status,queue_job_id,dedupe_key,created_at,updated_at)
                       VALUES(?,?,?,?,?,'pending',NULL,?,?,?)""",
                    (work_id, document.content_type.value, query.strip(), _json([canonical_id]),
                     _json(list(stored_document.menu_work_ids) if disposition == "inserted" else list(document.menu_work_ids)), f"generation:{work_id}", now, now),
                )
            if schedule_generation and disposition == "duplicate" and stored_document.work_link_status is WorkLinkStatus.MATCHED:
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
        return StoredEvidenceReference(canonical_id, canonical_id, disposition)

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

    async def web_evidence_revision(
        self, menu_work_id: int, content_type: RagContentType, source_ids: tuple[str, ...],
    ) -> str | None:
        """Fingerprint only the usable references linked to this work and editorial lane."""
        if not source_ids:
            return None
        return await asyncio.to_thread(self._web_evidence_revision, menu_work_id, content_type, source_ids)

    def _web_evidence_revision(
        self, menu_work_id: int, content_type: RagContentType, source_ids: tuple[str, ...],
    ) -> str | None:
        evidence_types = get_content_profile(content_type).accepted_evidence_types
        source_marks = ",".join("?" for _ in source_ids)
        type_marks = ",".join("?" for _ in evidence_types)
        with self._lock:
            rows = self._connection.execute(
                f"""SELECT DISTINCT d.document_id, d.content_hash, d.source_id
                    FROM rag_work_link w JOIN rag_document d ON d.document_id=w.document_id
                    WHERE w.menu_work_id=? AND w.status='matched'
                      AND d.source_id IN ({source_marks}) AND d.evidence_type IN ({type_marks})
                      AND {_USABLE_WORK_EVIDENCE_SQL}
                    ORDER BY d.document_id""",
                (menu_work_id, *source_ids, *(kind.value for kind in evidence_types)),
            ).fetchall()
        if not rows:
            return None
        return hashlib.sha256(_json([tuple(row) for row in rows]).encode("utf-8")).hexdigest()

    def _fetch_candidates(self, request: RetrievalRequest) -> Sequence[EvidenceDocument]:
        with self._lock:
            accepted_types = tuple(
                item.value for item in get_content_profile(request.content_type).accepted_evidence_types
            )
            if not accepted_types:
                return ()
            type_marks = ",".join("?" for _ in accepted_types)
            work_marks = ",".join("?" for _ in request.menu_work_ids)
            selected: list[str] = []
            source_clause = ""
            source_params: tuple[object, ...] = ()
            if request.source_ids:
                source_marks = ",".join("?" for _ in request.source_ids)
                source_clause = f" AND source_id IN ({source_marks})"
                source_params = tuple(request.source_ids)
            if request.seed_evidence_ids:
                marks = ",".join("?" for _ in request.seed_evidence_ids)
                work_clause = ""
                if request.menu_work_ids:
                    work_clause = (
                        " AND EXISTS (SELECT 1 FROM rag_work_link w WHERE w.document_id=d.document_id"
                        f" AND w.status='matched' AND w.menu_work_id IN ({work_marks}))"
                    )
                rows = self._connection.execute(
                    f"SELECT d.document_id FROM rag_document d WHERE evidence_type IN ({type_marks}){source_clause} "
                    f"AND d.document_id IN ({marks}){work_clause} AND {_USABLE_WORK_EVIDENCE_SQL}",
                    (*accepted_types, *source_params, *request.seed_evidence_ids, *request.menu_work_ids),
                ).fetchall()
                selected.extend(str(row["document_id"]) for row in rows)
            remaining = request.candidate_limit - len(selected)
            if remaining > 0:
                params: list[object] = []
                from_clause = "rag_document d"
                where_clauses: list[str] = []
                if request.menu_work_ids:
                    from_clause = "rag_work_link w JOIN rag_document d ON d.document_id=w.document_id"
                    where_clauses.extend(("w.status='matched'", f"w.menu_work_id IN ({work_marks})"))
                    params.extend(request.menu_work_ids)
                where_clauses.append(_USABLE_WORK_EVIDENCE_SQL)
                where_clauses.append("d.evidence_type IN (" + type_marks + ")")
                params.extend(accepted_types)
                if request.source_ids:
                    source_marks = ",".join("?" for _ in request.source_ids)
                    where_clauses.append(f"d.source_id IN ({source_marks})")
                    params.extend(request.source_ids)
                fields = requested_dictionary_fields(request)
                field_match = ""
                field_params = []
                if fields:
                    subjects = fact_subjects(request.subject_title or request.query, request.menu_work_ids, fields)
                    field_match, field_params = subjects.sql_condition(fields)
                    where_clauses.append("(COALESCE(json_extract(d.metadata_json,'$.dataset'),'') != 'menu_dict' OR " + field_match + ")")
                    params.extend(field_params)
                query_terms = _retrieval_terms(request.query)
                if query_terms and not request.semantic_candidate_scan:
                    patterns = tuple(f"%{term}%" for term in query_terms)
                    title_terms = " OR ".join("lower(d.title) LIKE ?" for _ in patterns)
                    body_terms = " OR ".join("lower(d.raw_text) LIKE ?" for _ in patterns)
                    excerpt_terms = " OR ".join("lower(e.excerpt_text) LIKE ?" for _ in patterns)
                    text_match = (title_terms + " OR " + body_terms +
                                  " OR EXISTS (SELECT 1 FROM rag_excerpt e WHERE e.document_id=d.document_id AND (" + excerpt_terms + "))")
                    where_clauses.append("(" + text_match + (" OR " + field_match if fields else "") + ")")
                    params.extend(patterns)
                    params.extend(patterns)
                    params.extend(patterns)
                    params.extend(field_params)
                excluded = ""
                if selected:
                    excluded = " AND d.document_id NOT IN (" + ",".join("?" for _ in selected) + ")"
                    params.extend(selected)
                order = "d.published_at IS NULL, d.published_at DESC, d.fetched_at DESC, d.document_id"
                if fields:
                    order = f"CASE WHEN {field_match} THEN 1 ELSE 0 END DESC, " + order
                    params.extend(field_params)
                elif query_terms:
                    # Search the scoped pool by title and preserved text before the bounded vector pass.
                    # Keep the unscoped semantic path inexpensive by ranking titles alone.
                    terms = (_retrieval_terms(request.subject_title) if request.subject_title else query_terms)[:6] or query_terms[:6]
                    score = " + ".join("(instr(lower(d.title), ?) > 0) * 2" for _ in terms)
                    params.extend(terms)
                    if request.menu_work_ids:
                        text_hits = " + ".join("(instr(lower(e.excerpt_text), ?) > 0)" for _ in terms)
                        body_hits = " + ".join("(instr(lower(d.raw_text), ?) > 0)" for _ in terms)
                        score += (f" + CASE WHEN d.raw_text IS NOT NULL THEN {body_hits} "
                                  f"ELSE COALESCE((SELECT {text_hits} FROM rag_excerpt e "
                                  "WHERE e.document_id=d.document_id ORDER BY e.excerpt_id LIMIT 1), 0) END")
                        params.extend(terms)
                        params.extend(terms)
                    order = "(" + score + ") DESC, " + order
                params.append(remaining)
                group_clause = " GROUP BY d.document_id" if request.menu_work_ids else ""
                rows = self._connection.execute(
                    "SELECT d.document_id FROM " + from_clause + " WHERE " + " AND ".join(where_clauses) + excluded + group_clause +
                    " ORDER BY " + order + " LIMIT ?", params
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

    async def enqueue_generation_work(self, item: GenerationWorkRequest) -> bool:
        """Queue a caller-selected request only when its seed evidence has the requested work link."""
        return await asyncio.to_thread(self._enqueue_generation_work, item)

    def _enqueue_generation_work(self, item: GenerationWorkRequest) -> bool:
        seed_marks = ",".join("?" for _ in item.seed_evidence_ids)
        with self._lock:
            seed_rows = self._connection.execute(
                f"SELECT document_id FROM rag_document WHERE document_id IN ({seed_marks})",
                tuple(item.seed_evidence_ids),
            ).fetchall()
            if {str(row["document_id"]) for row in seed_rows} != set(item.seed_evidence_ids):
                raise ValueError("Every generation seed must exist as stored RAG evidence")
            if item.menu_work_ids:
                work_marks = ",".join("?" for _ in item.menu_work_ids)
                matched_rows = self._connection.execute(
                    f"""SELECT DISTINCT document_id FROM rag_work_link
                        WHERE document_id IN ({seed_marks}) AND menu_work_id IN ({work_marks})
                          AND status='matched'""",
                    (*item.seed_evidence_ids, *item.menu_work_ids),
                ).fetchall()
                if {str(row["document_id"]) for row in matched_rows} != set(item.seed_evidence_ids):
                    raise ValueError("Every seed must be explicitly matched to one of the requested local work IDs")
        payload = _json({
            "work_id": item.work_id,
            "content_type": item.content_type.value,
            "query": item.query,
            "seed_evidence_ids": item.seed_evidence_ids,
            "menu_work_ids": item.menu_work_ids,
            "top_k": item.top_k,
            "candidate_limit": item.candidate_limit,
        })
        dedupe_key = f"explicit:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"
        now = _iso(datetime.now(timezone.utc))
        with self._transaction():
            cursor = self._connection.execute(
                """INSERT OR IGNORE INTO rag_generation_work(
                     work_id,content_type,retrieval_query,seed_evidence_ids_json,menu_work_ids_json,
                     status,queue_job_id,dedupe_key,created_at,updated_at)
                   VALUES(?,?,?,?,?,'pending',NULL,?,?,?)""",
                (item.work_id, item.content_type.value, item.query, _json(list(item.seed_evidence_ids)),
                 _json(list(item.menu_work_ids)), dedupe_key, now, now),
            )
            return cursor.rowcount == 1

    def _pending_generation_work(self, limit: int) -> Sequence[GenerationWorkRequest]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM rag_generation_work WHERE status='pending' ORDER BY created_at,work_id LIMIT 500"
            ).fetchall()
            pending = tuple(GenerationWorkRequest(
                work_id=str(row["work_id"]), query=str(row["retrieval_query"]),
                content_type=RagContentType(row["content_type"]),
                seed_evidence_ids=tuple(json.loads(row["seed_evidence_ids_json"])),
                menu_work_ids=tuple(json.loads(row["menu_work_ids_json"])),
            ) for row in rows)
            return _diversify_generation_batch(pending, limit)


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

    async def count_active(self, queue: QueueName, *, handler_kind: str | None = None) -> int:
        return await asyncio.to_thread(self._count_active, queue, handler_kind)

    def _count_active(self, queue: QueueName, handler_kind: str | None) -> int:
        sql = "SELECT COUNT(*) FROM rag_queue_job WHERE queue_name=? AND status IN ('pending','running','retry_wait')"
        params: list[object] = [queue.value]
        if handler_kind is not None:
            sql += " AND handler_kind=?"
            params.append(handler_kind)
        with self._lock:
            row = self._connection.execute(sql, params).fetchone()
            return int(row[0])

    async def web_pipeline_backlog(self) -> WebPipelineBacklog:
        return await asyncio.to_thread(self._web_pipeline_backlog)

    def _web_pipeline_backlog(self) -> WebPipelineBacklog:
        with self._lock:
            row = self._connection.execute(
                f"""SELECT COUNT(*) AS total_active,
                           COUNT(*) FILTER (WHERE queue_name='generation'
                                            AND {_GENERATION_REVISION_SQL}) AS active_revisions
                    FROM rag_queue_job
                    WHERE status IN ('pending','running','retry_wait')
                      AND NOT (status='running' AND COALESCE(
                          julianday(started_at) < julianday(:boot)
                          AND julianday(updated_at) < julianday(:boot), 0))
                      AND (queue_name IN ('generation','review')
                           OR (queue_name='collection' AND handler_kind='web_candidate_assessment'))""",
                {"boot": self._local_boot_at},
            ).fetchone()
        return WebPipelineBacklog(total_active=row["total_active"], active_revisions=row["active_revisions"])

    def _enqueue(self, item: QueueItem) -> QueueItem:
        with self._transaction():
            return self._enqueue_in_transaction(item)

    def _enqueue_in_transaction(self, item: QueueItem) -> QueueItem:
        now = _iso(item.created_at or datetime.now(timezone.utc))
        updated = _iso(item.updated_at or datetime.now(timezone.utc))
        payload = _json(dict(item.payload or {}))
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

    async def claim(
        self, queue: QueueName, *, now: datetime, handler_kind: str | None = None,
        allow_scheduled_discovery: bool = True,
    ) -> QueueItem | None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Queue timestamps must be timezone-aware")
        return await asyncio.to_thread(self._claim, queue, now, handler_kind, allow_scheduled_discovery)

    def _claim(
        self, queue: QueueName, now: datetime, handler_kind: str | None, allow_scheduled_discovery: bool,
    ) -> QueueItem | None:
        stamp = _iso(now)
        with self._transaction(immediate=True):
            sql = """SELECT * FROM rag_queue_job WHERE queue_name=?
                     AND (status='pending' OR (status='retry_wait' AND updated_at<=?))"""
            params: list[object] = [queue.value, stamp]
            if handler_kind is not None:
                sql += " AND handler_kind=?"
                params.append(handler_kind)
            if queue is QueueName.COLLECTION and handler_kind == "url" and not allow_scheduled_discovery:
                # The recurring planner always sets this prefix, including in older DBs.
                # Ad hoc requests for evidence can have either a separate key or no key.
                sql += " AND (dedupe_key IS NULL OR dedupe_key NOT GLOB 'recurring_collection:*')"
            priority = (
                f"CASE WHEN {_GENERATION_REVISION_SQL} THEN 0 ELSE 1 END, "
                if queue is QueueName.GENERATION else ""
            )
            sql += f" ORDER BY {priority}created_at,job_id LIMIT 1"
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
                    or expected.dedupe_key != item.dedupe_key or expected.handler_kind != item.handler_kind):
                raise ValueError("Queue item does not match a valid transition from the stored state")
            terminal = item.status in {QueueStatus.SUCCEEDED, QueueStatus.BLOCKED, QueueStatus.FAILED, QueueStatus.CANCELLED}
            cursor = self._connection.execute(
                "UPDATE rag_queue_job SET status=?,retry_count=?,last_error=?,payload_json=?,updated_at=?,finished_at=? WHERE job_id=? AND status=?",
                (item.status.value, item.retry_count, item.last_error, _json(dict(item.payload or {})),
                 _iso(item.updated_at), _iso(item.updated_at) if terminal else None,
                 item.job_id, previous.status.value),
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

    async def character_cast_match(self, payload: Mapping) -> CharacterCastMatch | None:
        def read() -> CharacterCastMatch | None:
            with self._lock:
                return archived_character_cast(self._connection, payload)
        return await asyncio.to_thread(read)

    async def linked_ann_work_ids(self, title: str, url: str) -> tuple[int, ...]:
        """Confirm the exact linked encyclopedia page with its stored, resolved work identity."""
        def read() -> tuple[int, ...]:
            with self._lock:
                rows = self._connection.execute(
                    """SELECT DISTINCT l.menu_work_id,l.status FROM rag_document d JOIN rag_work_link l USING(document_id)
                       WHERE d.source_id='ann_encyclopedia' AND d.canonical_url=? AND d.title=? COLLATE NOCASE
                         AND d.parse_status IN ('parsed','partial') AND COALESCE(
                             json_extract(d.metadata_json,'$.work_match_method'),'') <> 'ann_explicit_relationship_graph'""",
                    (canonical_source_url(url), title),
                ).fetchall()
                if any(row["status"] != "matched" or row["menu_work_id"] is None for row in rows):
                    return ()
                return tuple(sorted({row["menu_work_id"] for row in rows}))
        return await asyncio.to_thread(read)
