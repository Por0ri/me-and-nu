"""Broad-web topic discovery kept separate from the trusted RAG evidence corpus.

Independent RAG material anchors the story. An identified speaker's words in a
firsthand interview can additionally be cited as a statement from that article.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Protocol, Sequence
from urllib.parse import unquote, urlsplit

from ani_rag.adapter import canonical_source_item_url
from ani_rag.catalog import LocalWorkCatalog
from ani_rag.content import web_claims_can_generate
from ani_rag.interfaces import WorkCatalogResolver, WorkIdentity
from ani_rag.models import RagContentType, WorkLinkStatus
from ani_rag.queues import QueueItem, QueueName, QueueRepository, QueueStatus, WebPipelineBacklog
from ani_rag.settings import EvergreenSourceRegistry, load_source_registry, require_source_url
from ani_rag.source_evidence import WEB_ASSESSMENT_POLICY_VERSION, interview_speaker_label, web_quote_chunk_id
from ani_rag.retrieval import (
    DEFAULT_MIN_RETRIEVAL_SCORE,
    EvidenceRetriever,
    RetrievalRequest,
)

logger = logging.getLogger(__name__)
pressure_log = logging.getLogger(f"{__name__}.pressure")
DEFAULT_WEB_DISCOVERY_BACKLOG_LIMIT = 4
WEB_DISCOVERY_RESUME_LIMIT = 2
REVISION_BACKLOG_LIMIT = 2


async def _web_pipeline_backlog(queues: QueueRepository) -> WebPipelineBacklog:
    snapshot = getattr(queues, "web_pipeline_backlog", None)
    if callable(snapshot):
        return await snapshot()
    # Older queue adapters only report the combined backlog, without revision detail.
    counter = getattr(queues, "count_active", None)
    if not callable(counter):
        return WebPipelineBacklog(0, 0)
    total = sum((
        await counter(QueueName.COLLECTION, handler_kind="web_candidate_assessment"),
        await counter(QueueName.GENERATION),
        await counter(QueueName.REVIEW),
    ))
    return WebPipelineBacklog(total, 0)


def web_discovery_backlog_limit() -> int:
    raw = os.environ.get("ANI_RAG_WEB_DISCOVERY_BACKLOG_LIMIT", str(DEFAULT_WEB_DISCOVERY_BACKLOG_LIMIT))
    try:
        limit = int(raw)
    except ValueError as error:
        raise ValueError("ANI_RAG_WEB_DISCOVERY_BACKLOG_LIMIT must be an integer") from error
    if not 1 <= limit <= 1_000:
        raise ValueError("ANI_RAG_WEB_DISCOVERY_BACKLOG_LIMIT must be between 1 and 1000")
    return limit


@dataclass(frozen=True)
class WebDiscoveryRequest:
    """One bounded broad-web search seeded by an existing local work ID."""

    query: str
    content_type: RagContentType
    menu_work_id: int
    query_variants: tuple[str, ...] = ()
    expected_work_titles: tuple[str, ...] = ()
    result_limit: int = 5
    top_k: int = 8
    candidate_limit: int = 100
    min_retrieval_score: float = DEFAULT_MIN_RETRIEVAL_SCORE

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("Web discovery needs a bounded query")
        if len(self.expected_work_titles) > 20 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 240
            for item in self.expected_work_titles
        ):
            raise ValueError("Web discovery supports up to twenty bounded work-title variants")
        if len(self.query_variants) > 10 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 500
            for item in self.query_variants
        ):
            raise ValueError("Web search supports up to ten non-empty query variants")
        if len({item.strip().casefold() for item in (self.query, *self.query_variants)}) != len(self.query_variants) + 1:
            raise ValueError("Web search query variants must be distinct")
        if self.menu_work_id <= 0:
            raise ValueError("Web discovery must be seeded by an existing local work ID")
        if not 1 <= self.result_limit <= 5:
            raise ValueError("Broad-web discovery is limited to five candidate pages per request")
        if not 1 <= self.top_k <= self.candidate_limit <= 1000:
            raise ValueError("RAG retrieval limits must satisfy 1 <= top_k <= candidate_limit <= 1000")
        if not 0.0 <= self.min_retrieval_score <= 1.0:
            raise ValueError("min_retrieval_score must be between 0 and 1")


@dataclass(frozen=True)
class RecurringWebDiscoveryPlan:
    """An explicitly configured, persistent schedule for broad-web searches."""

    plan_id: str
    request: WebDiscoveryRequest
    interval_seconds: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", self.plan_id):
            raise ValueError("Recurring web-discovery plan needs a stable safe plan_id")
        if not 60 <= self.interval_seconds <= 31_536_000:
            raise ValueError("Recurring web-discovery interval must be between one minute and one year")


@dataclass(frozen=True)
class WebCandidateHit:
    title: str
    url: str
    snippet: str = ""
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("Web candidate needs a title")
        _validate_candidate_url(self.url)
        if self.published_at is not None and (
            self.published_at.tzinfo is None or self.published_at.utcoffset() is None
        ):
            raise ValueError("Candidate publication time must be timezone-aware")

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").casefold().rstrip(".")


@dataclass(frozen=True)
class WebSearchPage:
    """One provider search page; continuation is opaque and provider-owned."""

    hits: tuple[WebCandidateHit, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        if self.next_cursor is not None and (not self.next_cursor or len(self.next_cursor) > 4096):
            raise ValueError("Search continuation token must be 1-4096 characters")


@dataclass(frozen=True)
class WebCandidatePage:
    hit: WebCandidateHit
    title: str
    final_url: str
    body: str
    language: str | None
    published_at: datetime | None
    fetched_at: datetime

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.body.strip():
            raise ValueError("Candidate page needs a title and readable original text")
        _validate_candidate_url(self.final_url)
        if (urlsplit(self.final_url).hostname or "").casefold().rstrip(".") != self.hit.host:
            raise ValueError("Candidate read must remain on the exact result host")
        for field_name, value in (("fetched_at", self.fetched_at), ("published_at", self.published_at)):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} must be timezone-aware")


class WebDiscoveryAdapter(Protocol):
    async def search_web(self, query: str, *, limit: int) -> Sequence[WebCandidateHit]:
        """Search the public web for bounded candidate URLs; do not crawl sites."""

    async def search_web_page(
        self, query: str, *, limit: int, cursor: str | None = None,
    ) -> WebSearchPage:
        """Return one page and only the continuation token supplied by the provider."""

    async def read_web_candidate(self, hit: WebCandidateHit) -> WebCandidatePage:
        """Read exactly one candidate URL with its host pinned for the request."""


class CandidatePageCache(Protocol):
    async def claim_web_candidate_page(
        self, source_url: str, *, now: datetime, cache_seconds: int = 2_592_000,
        lease_seconds: int = 900,
    ) -> Mapping[str, object]: ...

    async def complete_web_candidate_page(
        self, claim_id: str, *, source_url: str, final_url: str,
        page: dict[str, object], fetched_at: datetime, cache_seconds: int = 2_592_000,
    ) -> None: ...

    async def release_web_candidate_page(self, claim_id: str, *, error_code: str) -> None: ...


class WebDiscoveryPlanRepository(Protocol):
    async def register_web_discovery_plan(
        self, plan_id: str, request: Mapping[str, object], *, interval_seconds: int, now: datetime,
    ) -> None: ...

    async def disable_web_discovery_plan(self, plan_id: str, *, now: datetime) -> bool: ...

    async def claim_due_web_discovery_plans(
        self, *, now: datetime, lease_seconds: int = 300, limit: int = 20,
    ) -> Sequence[Mapping[str, object]]: ...

    async def complete_web_discovery_plan(
        self, plan_id: str, *, scheduled_for: datetime, lease_token: str, now: datetime,
    ) -> None: ...

    async def release_web_discovery_plan(
        self, plan_id: str, *, lease_token: str, error_code: str,
    ) -> None: ...


class CandidateEvidenceModel(Protocol):
    async def generate_json(
        self, *, system_prompt: str, user_payload: Mapping[str, object],
        schema_name: str, schema: Mapping[str, object],
    ) -> Mapping[str, object]: ...


def _validate_candidate_url(url: str) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("Web candidate URL must be an absolute credential-free HTTPS URL")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Web candidate URL has an invalid port") from error
    if port not in (None, 443):
        raise ValueError("Web candidate URL must use the standard HTTPS port")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".test", ".invalid")):
        raise ValueError("Local and reserved hostnames cannot become web candidates")
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ValueError("Web candidate host is malformed")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP-literal web candidates are not accepted")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _assessment_key_prefix(menu_work_id: int, content_type: RagContentType, url: str) -> str:
    canonical_url = canonical_source_item_url(url)
    url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
    return f"web_candidate:{menu_work_id}:{content_type.value}:{url_hash}"


def _reference_version(evidence_revision: str, payload: Mapping[str, object]) -> str:
    # A change to the retrieval policy can change the evidence presented to the verifier.
    return ":".join((
        evidence_revision, str(int(payload.get("top_k", 8))), str(int(payload.get("candidate_limit", 100))),
        str(float(payload.get("min_retrieval_score", DEFAULT_MIN_RETRIEVAL_SCORE))), WEB_ASSESSMENT_POLICY_VERSION,
    ))


def _job(
    queue: QueueName, payload: dict[str, object], dedupe_key: str, *,
    handler_kind: str | None = None,
) -> QueueItem:
    now = _now()
    return QueueItem(
        job_id=f"job_{hashlib.sha256(dedupe_key.encode()).hexdigest()[:28]}",
        queue=queue, status=QueueStatus.PENDING, created_at=now, updated_at=now,
        dedupe_key=dedupe_key, payload=payload, handler_kind=handler_kind,
    )


def _failure(job: QueueItem, stage: str, error: Exception) -> QueueItem:
    operator_action = bool(getattr(error, "operator_action_required", False))
    rate_limited = bool(getattr(error, "rate_limited", False))
    permanent = (
        operator_action or rate_limited or bool(getattr(error, "permanent_failure", False))
        or isinstance(error, (PermissionError, ValueError))
    )
    status = (
        QueueStatus.BLOCKED if permanent else
        QueueStatus.RETRY_WAIT if job.retry_count < job.max_retries else
        QueueStatus.FAILED
    )
    summary = getattr(error, "safe_summary", None)
    if not isinstance(summary, str) or not summary.strip():
        # Validation errors originate in our bounded request/page contracts and
        # identify the failed field. Avoid arbitrary provider exception text,
        # which may contain response data or secrets.
        summary = str(error) if isinstance(
            error, (ValueError, TypeError, NameError, PermissionError),
        ) else type(error).__name__
    summary = re.sub(r"https?://[^\s\]\[<>]+", "[url]", summary)
    summary = re.sub(r"(?i)(authorization|api[_-]?key|token)(\s*[:=]\s*)\S+", r"\1\2[redacted]", summary)
    return job.transition(status, now=_now(), error=f"{stage}:{summary[:300]}")


def _failure_log_detail(failed: QueueItem) -> str:
    return re.sub(r"[\r\n\t]+", " ", failed.last_error or "unknown_error")[:360]


async def enqueue_web_discovery(
    queues: QueueRepository, request: WebDiscoveryRequest, *, run_id: str | None = None,
) -> QueueItem:
    """Queue one broad search request; it does not contact MCP or start a worker."""

    effective_run_id = run_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", effective_run_id):
        raise ValueError("Web-discovery run_id must be a stable safe identifier")
    first_cursor_fingerprint = hashlib.sha256(b"<first-page>").hexdigest()[:16]
    key = f"web_discovery:{effective_run_id}:0:{first_cursor_fingerprint}"
    return await queues.enqueue(_job(QueueName.COLLECTION, {
        "query": request.query.strip(),
        "query_variants": [item.strip() for item in request.query_variants],
        "search_run_id": effective_run_id,
        "search_query_index": 0,
        "search_cursor": None,
        "content_type": request.content_type.value,
        "menu_work_id": request.menu_work_id,
        "expected_work_titles": list(request.expected_work_titles),
        "result_limit": request.result_limit,
        "top_k": request.top_k,
        "candidate_limit": request.candidate_limit,
        "min_retrieval_score": request.min_retrieval_score,
    }, key, handler_kind="web_discovery"))


async def register_recurring_web_discovery_plan(
    plans: WebDiscoveryPlanRepository, plan: RecurringWebDiscoveryPlan, *, now: datetime | None = None,
) -> None:
    """Persist a plan explicitly; merely composing a runtime never activates one."""
    request = {
        "query": plan.request.query.strip(),
        "query_variants": [item.strip() for item in plan.request.query_variants],
        "content_type": plan.request.content_type.value,
        "menu_work_id": plan.request.menu_work_id,
        "expected_work_titles": list(plan.request.expected_work_titles),
        "result_limit": plan.request.result_limit,
        "top_k": plan.request.top_k,
        "candidate_limit": plan.request.candidate_limit,
        "min_retrieval_score": plan.request.min_retrieval_score,
    }
    await plans.register_web_discovery_plan(
        plan.plan_id, request, interval_seconds=plan.interval_seconds, now=now or _now(),
    )


async def disable_recurring_web_discovery_plan(
    plans: WebDiscoveryPlanRepository, plan_id: str, *, now: datetime | None = None,
) -> bool:
    return await plans.disable_web_discovery_plan(plan_id, now=now or _now())


async def enqueue_due_web_discovery_plans(
    plans: WebDiscoveryPlanRepository, queues: QueueRepository, *, now: datetime | None = None,
    limit: int = 1,
) -> int:
    """Queue a small number of due plan runs, then advance their schedules."""
    scheduled_now = now or _now()
    if not 1 <= limit <= 20:
        raise ValueError("Due web-discovery plan dispatch limit must be between 1 and 20")
    due = await plans.claim_due_web_discovery_plans(now=scheduled_now, limit=limit)
    enqueued = 0
    for row in due:
        plan_id = str(row["plan_id"])
        scheduled_for_raw = row.get("scheduled_for")
        try:
            scheduled_for = datetime.fromisoformat(str(scheduled_for_raw))
            if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
                raise ValueError("scheduled_for must include a timezone")
            request_raw = row.get("request")
            if not isinstance(request_raw, Mapping):
                raise ValueError("persistent request body is malformed")
            request = WebDiscoveryRequest(
                query=str(request_raw["query"]),
                query_variants=tuple(str(value) for value in request_raw.get("query_variants", ())),
                content_type=RagContentType(str(request_raw["content_type"])),
                menu_work_id=int(request_raw["menu_work_id"]),
                expected_work_titles=tuple(str(value) for value in request_raw.get("expected_work_titles", ())),
                result_limit=int(request_raw.get("result_limit", 5)),
                top_k=int(request_raw.get("top_k", 8)),
                candidate_limit=int(request_raw.get("candidate_limit", 100)),
                min_retrieval_score=float(request_raw.get("min_retrieval_score", DEFAULT_MIN_RETRIEVAL_SCORE)),
            )
            run_digest = hashlib.sha256(
                f"{plan_id}:{scheduled_for.astimezone(timezone.utc).isoformat()}".encode("utf-8")
            ).hexdigest()[:32]
            await enqueue_web_discovery(queues, request, run_id=run_digest)
            lease_token = str(row["lease_token"])
            await plans.complete_web_discovery_plan(
                plan_id, scheduled_for=scheduled_for, lease_token=lease_token, now=scheduled_now,
            )
            enqueued += 1
        except Exception as error:
            lease_token = str(row.get("lease_token", ""))
            await plans.release_web_discovery_plan(
                plan_id, lease_token=lease_token, error_code=type(error).__name__,
            )
            logger.warning("web_discovery_plan_dispatch_failed plan_id=%s error_type=%s", plan_id, type(error).__name__)
    return enqueued


class WebSearchGate:
    """Share the search pause across paid discovery lanes in one crawler."""

    def __init__(self, queues: QueueRepository):
        self.queues = queues
        self._search_paused: bool | None = None
        self._pause_limit = web_discovery_backlog_limit()
        self._resume_limit = min(WEB_DISCOVERY_RESUME_LIMIT, self._pause_limit - 1)

    async def search_is_paused(self) -> bool:
        backlog = await _web_pipeline_backlog(self.queues)
        if backlog.active_revisions >= REVISION_BACKLOG_LIMIT or backlog.total_active >= self._pause_limit:
            paused = True
        elif backlog.total_active <= self._resume_limit:
            paused = False
        else:
            # Restart in the middle band conservatively; existing candidates still drain.
            paused = self._search_paused is not False
        if paused != self._search_paused:
            pressure_log.info(
                "web_search_%s total_active=%d active_revisions=%d",
                "paused" if paused else "enabled", backlog.total_active, backlog.active_revisions,
            )
            self._search_paused = paused
        return paused


def _stale_work_seed(expected: Sequence[str], current: Sequence[str]) -> bool:
    if not current:
        return True
    if not expected:
        return False
    current_keys = {_title_match_key(title) for title in current} - {""}
    return all(_title_match_key(title) not in current_keys for title in expected)


class WebDiscoveryWorker:
    """Search all web domains and enqueue individual one-page candidate assessments."""

    def __init__(
        self, queues: QueueRepository, adapter: WebDiscoveryAdapter, *, registry: EvergreenSourceRegistry | None = None,
        catalog_titles: Callable[[int], Sequence[str]] | None = None,
    ):
        self.queues, self.adapter = queues, adapter
        self.catalog_titles = catalog_titles
        self.registry = registry if registry is not None else load_source_registry()
        self.search_gate = WebSearchGate(queues)

    async def run_once(self) -> bool:
        if await self.search_gate.search_is_paused():
            return False
        job = await self.queues.claim(QueueName.COLLECTION, now=_now(), handler_kind="web_discovery")
        if job is None:
            return False
        payload = dict(job.payload or {})
        try:
            request = WebDiscoveryRequest(
                query=str(payload["query"]), content_type=RagContentType(str(payload["content_type"])),
                query_variants=tuple(str(item) for item in payload.get("query_variants", ()) if isinstance(item, str)),
                menu_work_id=int(payload["menu_work_id"]), result_limit=int(payload.get("result_limit", 5)),
                expected_work_titles=tuple(str(value) for value in payload.get("expected_work_titles", ())),
                top_k=int(payload.get("top_k", 8)), candidate_limit=int(payload.get("candidate_limit", 100)),
                min_retrieval_score=float(payload.get("min_retrieval_score", DEFAULT_MIN_RETRIEVAL_SCORE)),
            )
            if self.catalog_titles and _stale_work_seed(
                request.expected_work_titles, self.catalog_titles(request.menu_work_id),
            ):
                await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_now(), error="stale_catalog_work_seed"))
                return True
            source_ids = self.registry.reference_source_ids(request.content_type)
            evidence_revision = await self.queues.web_evidence_revision(
                request.menu_work_id, request.content_type, source_ids,
            )
            if evidence_revision is None:
                # Skip this occurrence only; the next scheduled occurrence checks fresh evidence.
                await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_now(), error="web_search_no_work_evidence"))
                logger.info("web_search_skipped work=%d type=%s reason=no_evidence",
                            request.menu_work_id, request.content_type.value)
                return True
            query_index = int(payload.get("search_query_index", 0))
            queries = (request.query, *request.query_variants)
            if not 0 <= query_index < len(queries):
                raise ValueError("Web search query cursor is outside the configured query plan")
            query = queries[query_index]
            search_cursor = payload.get("search_cursor")
            if search_cursor is not None and not isinstance(search_cursor, str):
                raise ValueError("Web search cursor must be a string")
            search_page_method = getattr(self.adapter, "search_web_page", None)
            if callable(search_page_method):
                page = await search_page_method(query, limit=request.result_limit, cursor=search_cursor)
                if not isinstance(page, WebSearchPage):
                    raise TypeError("search_web_page must return WebSearchPage")
                hits, next_cursor = page.hits, page.next_cursor
            else:
                if search_cursor is not None:
                    raise ValueError("This search adapter cannot resume a continuation cursor")
                hits = tuple(await self.adapter.search_web(query, limit=request.result_limit))
                next_cursor = None
            if len(hits) > request.result_limit:
                raise ValueError("Web search returned more candidates than requested")
            queued_candidates = 0
            filtered: dict[str, int] = {}
            for hit in hits:
                if request.expected_work_titles and not _candidate_mentions_work(
                    hit, request.expected_work_titles,
                ):
                    filtered["unrelated_work"] = filtered.get("unrelated_work", 0) + 1
                    continue
                rejection_reason = _candidate_pre_read_rejection_reason(hit, request.content_type)
                if rejection_reason is not None:
                    filtered[rejection_reason] = filtered.get(rejection_reason, 0) + 1
                    continue
                hit_payload = {
                    "query": query,
                    "content_type": request.content_type.value,
                    "menu_work_id": request.menu_work_id,
                    "expected_work_titles": list(request.expected_work_titles),
                    "top_k": request.top_k,
                    "candidate_limit": request.candidate_limit,
                    "min_retrieval_score": request.min_retrieval_score,
                    "candidate": {
                        "title": hit.title[:500], "url": hit.url,
                        "host": hit.host, "snippet": hit.snippet[:1200],
                        "published_at": hit.published_at.isoformat() if hit.published_at else None,
                    },
                }
                key_prefix = _assessment_key_prefix(request.menu_work_id, request.content_type, hit.url)
                revision_reader = getattr(self.queues, "web_candidate_assessment_revision", None)
                revision = (
                    await revision_reader(
                        hit.url, now=_now(), key_prefix=key_prefix,
                        reference_version=_reference_version(evidence_revision, hit_payload),
                    )
                    if callable(revision_reader) else "untracked"
                )
                if revision is None:
                    filtered["candidate_already_handled"] = filtered.get("candidate_already_handled", 0) + 1
                    continue
                await self.queues.enqueue(_job(
                    QueueName.COLLECTION, hit_payload,
                    f"{key_prefix}:{revision}",
                    handler_kind="web_candidate_assessment",
                ))
                queued_candidates += 1
            run_id = str(payload.get("search_run_id", job.job_id))
            if next_cursor is not None:
                next_index, next_page_cursor = query_index, next_cursor
            elif query_index + 1 < len(queries):
                next_index, next_page_cursor = query_index + 1, None
            else:
                next_index, next_page_cursor = -1, None
            if next_index >= 0:
                cursor_fingerprint = hashlib.sha256((next_page_cursor or "<first-page>").encode()).hexdigest()[:16]
                continuation = dict(payload)
                continuation.update({"search_query_index": next_index, "search_cursor": next_page_cursor})
                await self.queues.enqueue(_job(
                    QueueName.COLLECTION, continuation,
                    f"web_discovery:{run_id}:{next_index}:{cursor_fingerprint}",
                    handler_kind="web_discovery",
                ))
            await self.queues.save(job.transition(QueueStatus.SUCCEEDED, now=_now()))
            logger.info(
                "web_discovery_page_complete job_id=%s query_index=%d candidates=%d has_next=%s",
                job.job_id, query_index, queued_candidates, next_index >= 0,
            )
            if filtered:
                logger.info(
                    "web_discovery_candidates_filtered job_id=%s counts=%s",
                    job.job_id, ",".join(f"{key}:{value}" for key, value in sorted(filtered.items())),
                )
        except Exception as error:
            failed = _failure(job, "web_discovery_error", error)
            await self.queues.save(failed)
            logger.warning(
                "web_discovery_failed job_id=%s status=%s error=%s",
                job.job_id, failed.status.value, _failure_log_detail(failed),
            )
        return True


class WebCandidateAssessmentWorker:
    """Read a candidate, confirm its seeded work, and verify its claims against RAG before enqueue."""

    def __init__(
        self, queues: QueueRepository, adapter: WebDiscoveryAdapter,
        work_catalog: WorkCatalogResolver, retriever: EvidenceRetriever,
        evidence_model: CandidateEvidenceModel, page_cache: CandidatePageCache | None = None,
        *, registry: EvergreenSourceRegistry | None = None,
        catalog_titles: Callable[[int], Sequence[str]] | None = None,
        edition_catalog: LocalWorkCatalog | None = None,
    ):
        self.queues, self.adapter = queues, adapter
        self.work_catalog, self.retriever = work_catalog, retriever
        self.edition_catalog = edition_catalog
        self.catalog_titles = catalog_titles or getattr(work_catalog, "titles_for_work_id", None) or getattr(
            getattr(work_catalog, "catalog", None), "titles_for_work_id", None,
        )
        self.evidence_model = evidence_model
        self.registry = registry if registry is not None else load_source_registry()
        self.page_cache = page_cache
        self._reads_paused = False

    async def run_once(self) -> bool:
        backlog = await _web_pipeline_backlog(self.queues)
        paused = backlog.active_revisions >= REVISION_BACKLOG_LIMIT
        if paused != self._reads_paused:
            pressure_log.info(
                "web_candidate_reads_%s total_active=%d active_revisions=%d",
                "paused" if paused else "enabled", backlog.total_active, backlog.active_revisions,
            )
            self._reads_paused = paused
        if paused:
            return False
        job = await self.queues.claim(
            QueueName.COLLECTION, now=_now(), handler_kind="web_candidate_assessment",
        )
        if job is None:
            return False
        payload = dict(job.payload or {})
        candidate = payload.get("candidate")
        try:
            if not isinstance(candidate, dict):
                raise ValueError("Candidate queue item is malformed")
            hit = WebCandidateHit(
                title=str(candidate["title"]), url=str(candidate["url"]),
                snippet=str(candidate.get("snippet", "")),
                published_at=datetime.fromisoformat(str(candidate["published_at"]))
                if candidate.get("published_at") else None,
            )
            menu_work_id = int(payload["menu_work_id"])
            content_type = RagContentType(str(payload["content_type"]))
            expected_titles = tuple(
                str(value) for value in payload.get("expected_work_titles", ())
                if isinstance(value, str) and value.strip()
            )
            if self.catalog_titles:
                current = self.catalog_titles(menu_work_id)
                if _stale_work_seed(expected_titles, current):
                    await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_now(), error="stale_catalog_work_seed"))
                    return True
                expected_titles = expected_titles or tuple(current)
            if expected_titles and not _candidate_mentions_work(hit, expected_titles):
                error = "candidate_search_result_not_about_seeded_work"
                await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_now(), error=error))
                logger.info("web_candidate_blocked job_id=%s reason=%s", job.job_id, error)
                return True
            rejection_reason = _candidate_pre_read_rejection_reason(hit, content_type)
            if rejection_reason is not None:
                await self.queues.save(job.transition(
                    QueueStatus.BLOCKED, now=_now(), error=rejection_reason,
                ))
                logger.info(
                    "web_candidate_blocked job_id=%s reason=%s", job.job_id, rejection_reason,
                )
                return True
            source_ids = self.registry.reference_source_ids(content_type)
            evidence_revision = await self.queues.web_evidence_revision(menu_work_id, content_type, source_ids)
            if evidence_revision is None:
                await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_now(), error="candidate_no_work_evidence"))
                return True
            page = await self._read_candidate(hit, job)
            if page is None:
                return True
            claim_inputs = getattr(self.queues, "claim_web_assessment_inputs", None)
            if callable(claim_inputs):
                reference_version = _reference_version(evidence_revision, payload)
                if payload.get("reassess_generation_id"):
                    reference_version += ":reassess:" + str(payload["reassess_generation_id"])
                owned = await claim_inputs(
                    job, source_url=hit.url, key_prefix=_assessment_key_prefix(menu_work_id, content_type, hit.url),
                    page=_candidate_page_to_cache(page),
                    reference_version=reference_version,
                )
                if owned is None:
                    await self.queues.save(job.transition(
                        QueueStatus.BLOCKED, now=_now(), error="candidate_assessment_unchanged",
                    ))
                    return True
                job = owned
            identity = WorkIdentity(
                titles=(page.title,) if page.title.strip() else (),
                external_ids={}, source_text=page.body[:40_000],
                source_url=page.final_url, source_id=f"web_discovery:{hit.host}",
                seed_menu_work_ids=(menu_work_id,),
            )
            resolution = await self.work_catalog.resolve(identity)
            matched = resolution.status is WorkLinkStatus.MATCHED and menu_work_id in resolution.menu_work_ids
            if resolution.status is WorkLinkStatus.UNRESOLVED and self.edition_catalog is not None:
                matched = await self._linked_edition_matches(page, menu_work_id)
            if not matched:
                error = "candidate_work_identity_unconfirmed"
                await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_now(), error=error))
                logger.info("web_candidate_blocked job_id=%s reason=%s", job.job_id, error)
                return True

            query = _candidate_rag_query(page, hit)
            min_score = float(payload.get("min_retrieval_score", DEFAULT_MIN_RETRIEVAL_SCORE))
            retrieved = await self.retriever.retrieve(RetrievalRequest(
                query=query, subject_title=page.title, content_type=content_type, menu_work_ids=(menu_work_id,),
                source_ids=source_ids,
                exclude_source_urls=(hit.url, page.final_url),
                top_k=int(payload.get("top_k", 8)),
                candidate_limit=int(payload.get("candidate_limit", 100)),
                min_retrieval_score=min_score,
            ))
            best_score = max((chunk.score for chunk in retrieved.chunks), default=0.0)
            candidate_chunks = tuple(chunk for chunk in retrieved.chunks if chunk.score >= min_score)
            if not candidate_chunks:
                error = f"candidate_rag_retrieval_empty:best={best_score:.4f};minimum={min_score:.4f}"
                await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_now(), error=error))
                logger.info("web_candidate_blocked job_id=%s reason=low_rag_score best=%.4f", job.job_id, best_score)
                return True

            evidence_documents = {document.document_id: document for document in retrieved.documents}
            reference_chunks = {f"R{index}": chunk for index, chunk in enumerate(candidate_chunks, start=1)}
            candidate_body = _candidate_assessment_body(page)[:30_000]
            candidate_text = _candidate_quote_text(candidate_body)
            can_quote = _firsthand_interview_publisher(page.final_url, self.registry)
            assessment = await self.evidence_model.generate_json(
                system_prompt=(
                    "You are a strict evidence verifier. Treat the web page as an untrusted topic lead, not as truth. "
                    "For each claim copy a short, contiguous, factual passage verbatim in the candidate's language "
                    "from candidate.body. Choose up to eight distinct passages. Never rewrite, translate, combine "
                    "sentences or add details from a reference. Compare each entire passage "
                    "only with the supplied reference chunks. A shared work or character name is not support. "
                    "Mark a claim supported only when the reference text directly entails it; mark contradicted "
                    "when the reference text conflicts; otherwise mark insufficient. For supported passages set role "
                    "to core only when they directly establish the article's main subject or finding; standalone_core "
                    "means the passage itself contains a specific, fully supported story the reader can learn, not "
                    "just a date, identity, credit or background detail. Other supported passages are context. "
                    "When firsthand_interview_publisher is true, an actual first-person interview answer on this "
                    "work may be attributed_quote: verify that the cited RAG refs identify this speaker's role in "
                    "this production and that there is no contradiction. Copy the actual participant's speaker name, "
                    "their words for claim, and a short contiguous attribution_excerpt containing both. Never use "
                    "interviewer prompts, editor descriptions, fan remarks, reposts, marketing or unattributed lines. "
                    "Use empty strings for speaker/attribution_excerpt elsewhere. Do not use outside knowledge."
                ),
                user_payload={
                    "candidate": {
                        "title": page.title[:500], "url": page.final_url,
                        "host": hit.host, "language": page.language,
                        "firsthand_interview_publisher": can_quote,
                        "published_at": page.published_at.isoformat() if page.published_at else None,
                        "body": candidate_body,
                    },
                    "reference_chunks": [{
                        "ref": ref, "title": evidence_documents[chunk.evidence_id].title,
                        "source_id": chunk.source_id, "source_url": chunk.source_url,
                        "text": chunk.text[:1600],
                    } for ref, chunk in reference_chunks.items()],
                },
                schema_name="web_candidate_rag_claim_assessment",
                schema={
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "claims": {
                            "type": "array", "maxItems": 8,
                            "items": {
                                "type": "object", "additionalProperties": False,
                                "properties": {
                                    "claim": {"type": "string"},
                                    "status": {"type": "string", "enum": ["supported", "attributed_quote", "contradicted", "insufficient"]},
                                    "role": {"type": "string", "enum": ["standalone_core", "core", "context"]},
                                    "speaker": {"type": "string"},
                                    "attribution_excerpt": {"type": "string"},
                                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["claim", "status", "role", "evidence_refs", "speaker", "attribution_excerpt"],
                            },
                        },
                    },
                    "required": ["claims"],
                },
            )
            raw_claims = assessment.get("claims")
            if not isinstance(raw_claims, list):
                raise ValueError("RAG claim assessment returned no claims array")
            raw_claims = [dict(item) if isinstance(item, dict) else item for item in raw_claims]
            supported_claims: list[dict[str, object]] = []
            supported_chunks: dict[str, object] = {}
            seen_passages: set[str] = set()
            contradicted_count = 0
            insufficient_count = 0
            for item in raw_claims:
                if not isinstance(item, dict):
                    continue
                claim_text = item.get("claim")
                status = item.get("status")
                refs = item.get("evidence_refs")
                if not isinstance(claim_text, str) or not claim_text.strip() or not isinstance(refs, list):
                    continue
                if status == "contradicted":
                    contradicted_count += 1
                    continue
                if status == "insufficient":
                    insufficient_count += 1
                    continue
                if status not in {"supported", "attributed_quote"} or not refs:
                    continue
                passage = _candidate_quote_text(claim_text)
                if not passage or passage not in candidate_text or passage in seen_passages:
                    item["status"] = "insufficient"
                    insufficient_count += 1
                    continue
                if not all(isinstance(ref, str) and ref in reference_chunks for ref in refs):
                    continue
                cited_chunks = [reference_chunks[ref] for ref in refs]
                chunk_ids = list(dict.fromkeys(chunk.chunk_id for chunk in cited_chunks))
                record = {"claim": passage, "evidence_refs": refs, "chunk_ids": chunk_ids}
                if status == "attributed_quote":
                    speaker = item.get("speaker")
                    excerpt = item.get("attribution_excerpt")
                    original = claim_text.strip()
                    label = None
                    if can_quote and isinstance(speaker, str) and isinstance(excerpt, str) and len(excerpt) <= 1200:
                        label = interview_speaker_label(candidate_body, speaker.strip(), excerpt, original)
                    if not label:
                        item["status"] = "insufficient"
                        insufficient_count += 1
                        continue
                    record = {
                        "kind": "attributed_quote", "claim": original, "speaker": label,
                        "attribution_excerpt": excerpt, "speaker_context_chunk_ids": chunk_ids,
                        "chunk_ids": [web_quote_chunk_id(page.final_url, page.title[:500], candidate_body, original)],
                    }
                elif "role" in item:
                    role = item["role"]
                    if role not in {"standalone_core", "core", "context"}:
                        continue
                    record["kind"] = "rag_context" if role == "context" else "rag_core"
                    record["standalone"] = role == "standalone_core"
                seen_passages.add(passage)
                supported_claims.append(record)
                for chunk in cited_chunks:
                    supported_chunks[chunk.chunk_id] = chunk
            if contradicted_count:
                error = _candidate_assessment_diagnostic(
                    "candidate_has_rag_contradictions", supported=len(supported_claims),
                    insufficient=insufficient_count, contradicted=contradicted_count,
                    best_score=best_score, chunks=candidate_chunks,
                )
                traced_job = replace(job, payload={
                    **job.payload,
                    "rag_assessment_trace": _candidate_assessment_trace(
                        raw_claims, reference_chunks, evidence_documents,
                    ),
                })
                await self.queues.save(traced_job.transition(QueueStatus.BLOCKED, now=_now(), error=error))
                logger.info("web_candidate_blocked job_id=%s reason=rag_contradictions count=%d", job.job_id, contradicted_count)
                return True
            if not web_claims_can_generate(supported_claims):
                error = _candidate_assessment_diagnostic(
                    "candidate_insufficient_rag_supported_claims", supported=len(supported_claims),
                    insufficient=insufficient_count, contradicted=contradicted_count,
                    best_score=best_score, chunks=candidate_chunks,
                )
                traced_job = replace(job, payload={
                    **job.payload,
                    "rag_assessment_trace": _candidate_assessment_trace(
                        raw_claims, reference_chunks, evidence_documents,
                    ),
                })
                await self.queues.save(traced_job.transition(QueueStatus.BLOCKED, now=_now(), error=error))
                logger.info("web_candidate_blocked job_id=%s reason=insufficient_supported_claims count=%d", job.job_id, len(supported_claims))
                return True

            verified_query = " ".join(str(item["claim"]) for item in supported_claims)[:1200]
            verified_document_ids = tuple(sorted({chunk.evidence_id for chunk in supported_chunks.values()}))

            generation_payload = {
                "query": verified_query,
                "content_type": content_type.value,
                "menu_work_ids": (menu_work_id,),
                "source_ids": source_ids,
                "seed_evidence_ids": verified_document_ids,
                "top_k": int(payload.get("top_k", 8)),
                "candidate_limit": int(payload.get("candidate_limit", 100)),
                "min_retrieval_score": min_score,
                "revision_instructions": [
                    "Use only the verified passages below. Attribute interview statements to their named speakers: "
                    + " | ".join(str(item["claim"]) for item in supported_claims),
                ],
                "web_candidate": {
                    "title": page.title[:500], "url": page.final_url, "requested_url": hit.url,
                    "host": hit.host,
                    "language": page.language,
                    "published_at": page.published_at.isoformat() if page.published_at else None,
                    "fetched_at": page.fetched_at.isoformat(),
                    # The candidate is the editorial subject; RAG references are
                    # the fact-check layer. Keep the read page in the generation
                    # job so copy generation does not silently fall back to RAG
                    # reference pages as its only source material.
                    "original_text": candidate_body,
                    "assessment_query": query,
                    "rag_score": best_score,
                    "corroborating_document_ids": list(verified_document_ids),
                    "reference_versions": {key: evidence_documents[key].content_hash for key in verified_document_ids},
                    "corroborating_chunk_ids": list(supported_chunks),
                    "verified_claims": supported_claims,
                    "unsupported_claim_count": sum(
                        1 for item in raw_claims
                        if isinstance(item, dict) and item.get("status") == "insufficient"
                    ),
                },
            }
            candidate_key = hashlib.sha256(page.final_url.encode("utf-8")).hexdigest()[:24]
            generation_key = f"web_candidate_generation:{menu_work_id}:{content_type.value}:{candidate_key}"
            if payload.get("reassess_generation_id"):
                generation_key += ":reassess:" + str(payload["reassess_generation_id"])
                generation_payload["reassessed_generation"] = True
                for key in ("revision_of_draft_id", "revision_count", "backlog_work_id"):
                    if key in payload:
                        generation_payload[key] = payload[key]
                generation_payload["revision_instructions"].extend(payload.get("revision_instructions", ()))
            await self.queues.enqueue(_job(
                QueueName.GENERATION, generation_payload, generation_key,
            ))
            await self.queues.save(job.transition(QueueStatus.SUCCEEDED, now=_now()))
            logger.info(
                "web_candidate_accepted job_id=%s work_id=%d rag_score=%.4f verified_claims=%d",
                job.job_id, menu_work_id, best_score, len(supported_claims),
            )
        except Exception as error:
            failed = _failure(job, "web_candidate_error", error)
            await self.queues.save(failed)
            logger.warning(
                "web_candidate_failed job_id=%s status=%s error=%s",
                job.job_id, failed.status.value, _failure_log_detail(failed),
            )
        return True

    async def _linked_edition_matches(self, page: WebCandidatePage, work_id: int) -> bool:
        """A first-party article can explicitly name and link a cataloged special or season."""
        catalog = self.edition_catalog
        lookup = getattr(self.page_cache, "linked_ann_work_ids", None)
        if catalog is None or not callable(lookup) or urlsplit(page.final_url).hostname not in {
            "animenewsnetwork.com", "www.animenewsnetwork.com",
        }:
            return False
        base = {name.casefold() for name in catalog.search_titles_for_work_id(work_id)}
        editions = {name.casefold() for row in catalog.entries_for_work_id(work_id) for name in row["titles"]} - base
        heading = page.title.split(" - Anime News Network", 1)[0]
        links = re.findall(
            r"\[([^\]\n]+)\]\((https://(?:www\.)?animenewsnetwork\.com/encyclopedia/anime\.php\?id=\d+)\)",
            _candidate_assessment_body(page)[:4500], re.IGNORECASE,
        )
        confirmed_edition = False
        for label, url in dict.fromkeys(links):
            if not re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", heading, re.IGNORECASE):
                continue
            if label.casefold() not in editions | base:
                return False
            exact = catalog.resolve_sync(WorkIdentity((label,), {}))
            if exact.menu_work_ids != (work_id,):
                return False
            if label.casefold() in editions:
                if await lookup(label, url) != (work_id,):
                    return False
                confirmed_edition = True
        return confirmed_edition

    async def _read_candidate(self, hit: WebCandidateHit, job: QueueItem) -> WebCandidatePage | None:
        if self.page_cache is None:
            return await self.adapter.read_web_candidate(hit)
        claim = await self.page_cache.claim_web_candidate_page(hit.url, now=_now())
        state = str(claim.get("status", ""))
        if state == "site_cooldown":
            await self.queues.save(job.transition(
                QueueStatus.BLOCKED, now=_now(), error="candidate_site_read_cooldown",
            ))
            return None
        if state == "unsupported":
            await self.queues.save(job.transition(
                QueueStatus.BLOCKED, now=_now(), error="candidate_page_unsupported",
            ))
            return None
        if state == "cached":
            payload = claim.get("page")
            if not isinstance(payload, Mapping):
                raise ValueError("Cached candidate page payload is malformed")
            return _candidate_page_from_cache(hit, payload)
        if state in {"busy", "retry_later"}:
            retry_value = claim.get("retry_at") or claim.get("lease_expires_at")
            try:
                retry_at = datetime.fromisoformat(str(retry_value)) if retry_value else _now() + timedelta(seconds=30)
            except ValueError:
                retry_at = _now() + timedelta(seconds=30)
            if retry_at.tzinfo is None or retry_at.utcoffset() is None:
                retry_at = _now() + timedelta(seconds=30)
            if retry_at <= _now():
                retry_at = _now() + timedelta(seconds=5)
            await self.queues.save(job.transition(
                QueueStatus.RETRY_WAIT, now=retry_at,
                error="candidate_page_read_in_progress" if state == "busy" else "candidate_page_cache_backoff",
            ))
            return None
        if state != "acquired":
            raise RuntimeError("Candidate page cache returned an unknown claim state")
        claim_id = str(claim.get("claim_id", ""))
        try:
            page = await self.adapter.read_web_candidate(hit)
            payload = _candidate_page_to_cache(page)
            await self.page_cache.complete_web_candidate_page(
                claim_id, source_url=hit.url, final_url=page.final_url, page=payload,
                fetched_at=page.fetched_at,
            )
            # Return the same bounded body that all later consumers will read.
            return _candidate_page_from_cache(hit, payload)
        except Exception as error:
            await self.page_cache.release_web_candidate_page(
                claim_id,
                error_code=("firecrawl_site_unsupported" if getattr(error, "unsupported_site", False)
                            else "unsupported_target" if getattr(error, "unsupported_target", False)
                            else type(error).__name__),
            )
            raise


def _candidate_page_to_cache(page: WebCandidatePage) -> dict[str, object]:
    return {
        "title": page.title[:500], "final_url": page.final_url, "body": page.body[:80_000],
        "language": page.language,
        "published_at": page.published_at.isoformat() if page.published_at else None,
        "fetched_at": page.fetched_at.isoformat(),
    }


def _candidate_page_from_cache(hit: WebCandidateHit, payload: Mapping[str, object]) -> WebCandidatePage:
    title = payload.get("title")
    final_url = payload.get("final_url")
    body = payload.get("body")
    fetched_at = payload.get("fetched_at")
    published_at = payload.get("published_at")
    if not all(isinstance(value, str) for value in (title, final_url, body, fetched_at)):
        raise ValueError("Cached candidate page is missing required fields")
    return WebCandidatePage(
        hit=hit, title=title, final_url=final_url, body=body,
        language=str(payload["language"]) if payload.get("language") is not None else None,
        published_at=datetime.fromisoformat(str(published_at)) if published_at else None,
        fetched_at=datetime.fromisoformat(fetched_at),
    )


def _candidate_quote_text(text: str) -> str:
    # A Markdown link and its visible label are the same passage for attribution.
    plain = re.sub(r"\[([^\]\n]+)\]\(https?://[^\s)]+\)", r"\1", text)
    return " ".join(plain.replace("**", "").split())


def _firsthand_interview_publisher(url: str, registry: EvergreenSourceRegistry) -> bool:
    """Which publishers may substantiate their interviewee's own words on the final URL."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        return False
    path = unquote(parsed.path).casefold()
    if ".." in path.split("/") or "\\" in path:
        return False
    # Original editorial interview sections are independent of approved RAG reference feeds.
    if parsed.hostname in {"animenewsnetwork.com", "www.animenewsnetwork.com"} and path.startswith(
        ("/interview/", "/newtype/")
    ):
        return True
    if parsed.hostname == "yattatachi.com" and re.fullmatch(r"/[a-z0-9-]*interview[a-z0-9-]*/?", path):
        return True
    for source in registry.approved_sources:
        if source.source_grade not in {"primary", "firsthand_interview_statement"}:
            continue
        try:
            require_source_url(source, url)
        except PermissionError:
            continue
        return True
    return False


def _candidate_assessment_body(page: WebCandidatePage) -> str:
    """Remove common Fandom chrome and use the article sections, not its prefix."""
    body = page.body.replace("\r\n", "\n").replace("\r", "\n")
    article_title = page.title.split("|", 1)[0].strip()
    title_key = _title_match_key(article_title)
    lines = body.splitlines()
    title_line = None
    for index, line in enumerate(lines):
        heading = re.match(r"^#{1,2}\s+(.+?)\s*$", line.strip())
        if heading and title_key and _title_match_key(heading.group(1)) == title_key:
            title_line = index
            break

    start = title_line if title_line is not None else 0
    contents = [
        index for index in range(start + 1, len(lines))
        if re.match(r"^##\s+Contents\s*$", lines[index].strip(), re.IGNORECASE)
    ]
    if contents:
        contents_index = contents[-1]
        section = next((
            index for index in range(contents_index + 1, len(lines))
            if re.match(r"^#{1,3}\s+\S", lines[index].strip())
            and not re.match(r"^##\s+Contents\s*$", lines[index].strip(), re.IGNORECASE)
        ), None)
        if section is not None:
            start = section
        elif title_line is not None:
            start = title_line + 1

    cleaned: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        line = re.sub(r"\\?\s*\[\s*Sign in to edit\].*$", "", line, flags=re.IGNORECASE).rstrip()
        line = re.sub(r"\\\[\s*$", "", line).rstrip()
        normalized = re.sub(r"[^a-z]+", " ", line.casefold()).strip()
        if normalized in {
            "sign in to edit", "history", "navigation", "more", "advertisement",
            "fandom homepage", "sign in", "register", "edit source", "view source",
        }:
            continue
        if re.fullmatch(r"\[\d+\].*article discussions.*", line.strip(), re.IGNORECASE):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()[:80_000]


def _candidate_assessment_diagnostic(
    reason: str, *, supported: int, insufficient: int, contradicted: int,
    best_score: float, chunks: Sequence[object],
) -> str:
    document_ids = sorted({str(getattr(chunk, "evidence_id", "")) for chunk in chunks})
    return (
        f"{reason}:{supported};unsupported={insufficient};contradicted={contradicted};"
        f"best={best_score:.4f};chunks={len(chunks)};docs={','.join(document_ids[:4])}"
    )[:500]


def _candidate_assessment_trace(
    raw_claims: Sequence[object], references: Mapping[str, object],
    evidence_documents: Mapping[str, object],
) -> dict[str, object]:
    claims: list[dict[str, object]] = []
    counts = {"supported": 0, "insufficient": 0, "contradicted": 0, "invalid": 0}
    for item in raw_claims[:8]:
        if not isinstance(item, dict):
            counts["invalid"] += 1
            continue
        status = item.get("status")
        if status not in {"supported", "attributed_quote", "insufficient", "contradicted"}:
            counts["invalid"] += 1
            continue
        counts[str(status)] = counts.get(str(status), 0) + 1
        refs = item.get("evidence_refs")
        evidence: list[dict[str, str]] = []
        if isinstance(refs, list):
            for ref in refs[:4]:
                chunk = references.get(ref) if isinstance(ref, str) else None
                if chunk is None:
                    continue
                evidence_id = str(getattr(chunk, "evidence_id", ""))
                document = evidence_documents.get(evidence_id)
                evidence.append({
                    "ref": str(ref),
                    "chunk_id": str(getattr(chunk, "chunk_id", "")),
                    "document_id": evidence_id,
                    "source_id": str(getattr(chunk, "source_id", "")),
                    "source_url": str(getattr(chunk, "source_url", "")),
                    "document_title": str(getattr(document, "title", ""))[:180],
                })
        claims.append({
            "claim": str(item.get("claim", ""))[:280],
            "status": str(status),
            "evidence": evidence,
        })
    return {"counts": counts, "claims": claims}


def _candidate_rag_query(page: WebCandidatePage, hit: WebCandidateHit) -> str:
    body = _candidate_assessment_body(page)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)
    body = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body)
    body = re.sub(r"https?://\S+", " ", body)
    body = re.sub(r"<[^>]+>", " ", body)
    sections = re.split(r"(?m)(?=^#{1,3}\s+)", body)
    section_samples: list[str] = []
    for section in sections:
        lines = [line.strip() for line in section.splitlines() if line.strip()]
        if not lines:
            continue
        heading = re.sub(r"[`*_>#\[\]() ]", " ", lines[0]).strip()
        detail = next((
            re.sub(r"[`*_>#|\[\]() ]", " ", line).strip()
            for line in lines[1:]
            if len(re.sub(r"[^\w]", "", line)) >= 12 and "---" not in line
        ), "")
        sample = " ".join(value for value in (heading, detail[:110]) if value)
        if sample:
            section_samples.append(sample)
    if len(section_samples) > 8:
        last = len(section_samples) - 1
        section_samples = [section_samples[round(index * last / 7)] for index in range(8)]
    article_title = page.title.split("|", 1)[0].strip()
    parts = [article_title, hit.title, hit.snippet, *section_samples]
    return " ".join(value.strip() for value in parts if value and value.strip())[:1_200]


def _candidate_mentions_work(hit: WebCandidateHit, titles: Sequence[str]) -> bool:
    """Skip obvious off-topic search results before paying to read their pages."""
    haystack = _title_match_key(f"{hit.title} {hit.snippet}")
    if not haystack:
        return False
    for title in titles:
        needle = _title_match_key(title)
        # Very short Latin abbreviations create accidental matches; permit short
        # titles only for CJK names that have at least two visible characters.
        if len(needle) < 3:
            if len(needle) < 2 or not any("CJK" in unicodedata.name(char, "") for char in title):
                continue
        if needle in haystack:
            return True
    return False


def _candidate_pre_read_rejection_reason(
    hit: WebCandidateHit, content_type: RagContentType,
) -> str | None:
    """Reject obvious navigation or non-editorial index pages before page scraping."""
    parsed = urlsplit(hit.url)
    path_parts = tuple(part.casefold() for part in parsed.path.split("/") if part)
    navigation_paths = {
        "search", "search-results", "results", "login", "signin", "sign-in",
        "signup", "sign-up", "register", "account", "profile", "category",
        "categories", "tag", "tags", "author", "authors", "archive", "archives",
        "feed", "feeds", "rss", "sitemap",
    }
    # Check only the route root: article URLs may legitimately contain a
    # later /category/ or /author/ segment in their canonical path.
    if path_parts and path_parts[0] in navigation_paths:
        return "navigation_or_search_page"
    if not path_parts and parsed.query:
        return "site_root_query_page"
    if parsed.path.casefold().endswith(("/feed", "/feed/", ".xml", ".rss")):
        return "feed_or_machine_document"
    host = hit.host
    if (host == "pinterest.com" or host.endswith(".pinterest.com")) and path_parts[:1] == ("ideas",):
        return "image_ideas_index"
    if (
        (host == "fandom.com" or host.endswith(".fandom.com"))
        and len(path_parts) >= 2
        and path_parts[-2] == "wiki"
        and unquote(path_parts[-1]).casefold().startswith("category:")
    ):
        return "wiki_category_index"

    # A general Spotify playlist is a track index, not a music announcement or
    # editorial article. Keep it available to curation plans, where playlists
    # can be the subject, but do not scrape it as a music-news candidate.
    if (
        content_type is RagContentType.MUSIC
        and parsed.hostname
        and parsed.hostname.casefold().rstrip(".") in {"open.spotify.com", "spotify.com"}
        and len(path_parts) >= 2
        and path_parts[0] == "playlist"
    ):
        return "music_playlist_index"

    title_key = _title_match_key(hit.title)
    if title_key in {
        "login", "signin", "sign in", "create account", "page not found", "search",
        "search results", "category", "tag", "archive", "404 not found",
    }:
        return "non_content_result_title"
    return None


def _title_match_key(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", value).casefold()
        if char.isalnum()
    )
