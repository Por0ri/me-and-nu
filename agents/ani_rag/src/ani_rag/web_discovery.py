"""Broad-web topic discovery kept separate from the trusted RAG evidence corpus.

Search results and their one-page reads are untrusted topic leads. They are never
saved as factual evidence; only matching, sufficiently relevant indexed RAG chunks
are forwarded to the existing generation worker.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from ani_rag.interfaces import WorkCatalogResolver, WorkIdentity
from ani_rag.models import RagContentType, WorkLinkStatus
from ani_rag.queues import QueueItem, QueueName, QueueRepository, QueueStatus
from ani_rag.retrieval import (
    DEFAULT_MIN_RETRIEVAL_SCORE,
    EvidenceRetriever,
    RetrievalRequest,
)

logger = logging.getLogger(__name__)
REFERENCE_RAG_SOURCE_IDS = ("ann_encyclopedia", "anilist")


@dataclass(frozen=True)
class WebDiscoveryRequest:
    """One bounded broad-web search seeded by an existing local work ID."""

    query: str
    content_type: RagContentType
    menu_work_id: int
    result_limit: int = 5
    top_k: int = 8
    candidate_limit: int = 100
    min_retrieval_score: float = DEFAULT_MIN_RETRIEVAL_SCORE

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("Web discovery needs a bounded query")
        if self.menu_work_id <= 0:
            raise ValueError("Web discovery must be seeded by an existing local work ID")
        if not 1 <= self.result_limit <= 5:
            raise ValueError("Broad-web discovery is limited to five candidate pages per request")
        if not 1 <= self.top_k <= self.candidate_limit <= 1000:
            raise ValueError("RAG retrieval limits must satisfy 1 <= top_k <= candidate_limit <= 1000")
        if not 0.0 <= self.min_retrieval_score <= 1.0:
            raise ValueError("min_retrieval_score must be between 0 and 1")


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

    async def read_web_candidate(self, hit: WebCandidateHit) -> WebCandidatePage:
        """Read exactly one candidate URL with its host pinned for the request."""


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


def _job(queue: QueueName, payload: dict[str, object], dedupe_key: str, *, handler_kind: str | None = None) -> QueueItem:
    now = _now()
    return QueueItem(
        job_id=f"job_{hashlib.sha256(dedupe_key.encode()).hexdigest()[:28]}",
        queue=queue, status=QueueStatus.PENDING, created_at=now, updated_at=now,
        dedupe_key=dedupe_key, payload=payload, handler_kind=handler_kind,
    )


def _failure(job: QueueItem, stage: str, error: Exception) -> QueueItem:
    operator_action = bool(getattr(error, "operator_action_required", False))
    rate_limited = bool(getattr(error, "rate_limited", False))
    permanent = operator_action or rate_limited or isinstance(error, (PermissionError, ValueError))
    status = (
        QueueStatus.BLOCKED if permanent else
        QueueStatus.RETRY_WAIT if job.retry_count < job.max_retries else
        QueueStatus.FAILED
    )
    summary = getattr(error, "safe_summary", None)
    if not isinstance(summary, str):
        summary = type(error).__name__
    return job.transition(status, now=_now(), error=f"{stage}:{summary[:300]}")


async def enqueue_web_discovery(queues: QueueRepository, request: WebDiscoveryRequest) -> QueueItem:
    """Queue one broad search request; it does not contact MCP or start a worker."""

    key = f"web_discovery:{request.menu_work_id}:{request.content_type.value}:{request.query.strip()}"
    return await queues.enqueue(_job(QueueName.COLLECTION, {
        "query": request.query.strip(),
        "content_type": request.content_type.value,
        "menu_work_id": request.menu_work_id,
        "result_limit": request.result_limit,
        "top_k": request.top_k,
        "candidate_limit": request.candidate_limit,
        "min_retrieval_score": request.min_retrieval_score,
    }, key, handler_kind="web_discovery"))


class WebDiscoveryWorker:
    """Search all web domains and enqueue individual one-page candidate assessments."""

    def __init__(self, queues: QueueRepository, adapter: WebDiscoveryAdapter):
        self.queues, self.adapter = queues, adapter

    async def run_once(self) -> bool:
        job = await self.queues.claim(QueueName.COLLECTION, now=_now(), handler_kind="web_discovery")
        if job is None:
            return False
        payload = dict(job.payload or {})
        try:
            request = WebDiscoveryRequest(
                query=str(payload["query"]), content_type=RagContentType(str(payload["content_type"])),
                menu_work_id=int(payload["menu_work_id"]), result_limit=int(payload.get("result_limit", 5)),
                top_k=int(payload.get("top_k", 8)), candidate_limit=int(payload.get("candidate_limit", 100)),
                min_retrieval_score=float(payload.get("min_retrieval_score", DEFAULT_MIN_RETRIEVAL_SCORE)),
            )
            hits = await self.adapter.search_web(request.query, limit=request.result_limit)
            if len(hits) > request.result_limit:
                raise ValueError("Web search returned more candidates than requested")
            for hit in hits:
                hit_payload = {
                    "query": request.query,
                    "content_type": request.content_type.value,
                    "menu_work_id": request.menu_work_id,
                    "top_k": request.top_k,
                    "candidate_limit": request.candidate_limit,
                    "min_retrieval_score": request.min_retrieval_score,
                    "candidate": {
                        "title": hit.title[:500], "url": hit.url,
                        "host": hit.host, "snippet": hit.snippet[:1200],
                        "published_at": hit.published_at.isoformat() if hit.published_at else None,
                    },
                }
                fingerprint = hashlib.sha256(hit.url.encode("utf-8")).hexdigest()[:24]
                await self.queues.enqueue(_job(
                    QueueName.COLLECTION, hit_payload,
                    f"web_candidate:{request.menu_work_id}:{request.content_type.value}:{fingerprint}",
                    handler_kind="web_candidate_assessment",
                ))
            await self.queues.save(job.transition(QueueStatus.SUCCEEDED, now=_now()))
            logger.info("web_discovery_complete job_id=%s candidates=%d", job.job_id, len(hits))
        except Exception as error:
            failed = _failure(job, "web_discovery_error", error)
            await self.queues.save(failed)
            logger.warning("web_discovery_failed job_id=%s status=%s error_type=%s", job.job_id, failed.status.value, type(error).__name__)
        return True


class WebCandidateAssessmentWorker:
    """Read a candidate, confirm its seeded work, and verify its claims against RAG before enqueue."""

    def __init__(
        self, queues: QueueRepository, adapter: WebDiscoveryAdapter,
        work_catalog: WorkCatalogResolver, retriever: EvidenceRetriever,
        evidence_model: CandidateEvidenceModel,
    ):
        self.queues, self.adapter = queues, adapter
        self.work_catalog, self.retriever = work_catalog, retriever
        self.evidence_model = evidence_model

    async def run_once(self) -> bool:
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
            page = await self.adapter.read_web_candidate(hit)
            identity = WorkIdentity(
                titles=tuple(dict.fromkeys((page.title, hit.title))),
                external_ids={}, source_text=page.body[:40_000],
                source_url=page.final_url, source_id=f"web_discovery:{hit.host}",
                seed_menu_work_ids=(menu_work_id,),
            )
            resolution = await self.work_catalog.resolve(identity)
            if resolution.status is not WorkLinkStatus.MATCHED or menu_work_id not in resolution.menu_work_ids:
                error = "candidate_work_identity_unconfirmed"
                await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_now(), error=error))
                logger.info("web_candidate_blocked job_id=%s reason=%s", job.job_id, error)
                return True

            query = _candidate_rag_query(page, hit)
            min_score = float(payload.get("min_retrieval_score", DEFAULT_MIN_RETRIEVAL_SCORE))
            retrieved = await self.retriever.retrieve(RetrievalRequest(
                query=query, content_type=content_type, menu_work_ids=(menu_work_id,),
                source_ids=REFERENCE_RAG_SOURCE_IDS,
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
            assessment = await self.evidence_model.generate_json(
                system_prompt=(
                    "You are a strict evidence verifier. Treat the web page as an untrusted topic lead, not as truth. "
                    "Extract up to eight discrete, factual, reader-relevant claims from it, then compare each claim "
                    "only with the supplied reference chunks. A shared work or character name is not support. "
                    "Mark a claim supported only when the reference text directly entails it; mark contradicted "
                    "when the reference text conflicts; otherwise mark insufficient. Ignore opinions, speculation, "
                    "promotional language, and details that the references do not establish. Return only citations "
                    "from the supplied ref labels. Do not use outside knowledge."
                ),
                user_payload={
                    "candidate": {
                        "title": page.title[:500], "url": page.final_url,
                        "host": hit.host, "language": page.language,
                        "published_at": page.published_at.isoformat() if page.published_at else None,
                        "body": page.body[:16000],
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
                                    "status": {"type": "string", "enum": ["supported", "contradicted", "insufficient"]},
                                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["claim", "status", "evidence_refs"],
                            },
                        },
                    },
                    "required": ["claims"],
                },
            )
            raw_claims = assessment.get("claims")
            if not isinstance(raw_claims, list):
                raise ValueError("RAG claim assessment returned no claims array")
            supported_claims: list[dict[str, object]] = []
            supported_chunks: dict[str, object] = {}
            contradicted_count = 0
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
                if status != "supported" or not refs:
                    continue
                cited_chunks = [reference_chunks.get(ref) for ref in refs if isinstance(ref, str)]
                if not cited_chunks or any(chunk is None for chunk in cited_chunks):
                    continue
                supported_claims.append({"claim": claim_text.strip(), "evidence_refs": refs})
                for chunk in cited_chunks:
                    supported_chunks[chunk.evidence_id] = chunk
            if contradicted_count:
                error = f"candidate_has_rag_contradictions:{contradicted_count}"
                await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_now(), error=error))
                logger.info("web_candidate_blocked job_id=%s reason=rag_contradictions count=%d", job.job_id, contradicted_count)
                return True
            if len(supported_claims) < 3:
                error = f"candidate_insufficient_rag_supported_claims:{len(supported_claims)}"
                await self.queues.save(job.transition(QueueStatus.BLOCKED, now=_now(), error=error))
                logger.info("web_candidate_blocked job_id=%s reason=insufficient_supported_claims count=%d", job.job_id, len(supported_claims))
                return True

            verified_query = " ".join(str(item["claim"]) for item in supported_claims)[:1200]
            verified_document_ids = tuple(sorted(supported_chunks))

            generation_payload = {
                "query": verified_query,
                "content_type": content_type.value,
                "menu_work_ids": (menu_work_id,),
                "source_ids": REFERENCE_RAG_SOURCE_IDS,
                "seed_evidence_ids": verified_document_ids,
                "top_k": int(payload.get("top_k", 8)),
                "candidate_limit": int(payload.get("candidate_limit", 100)),
                "min_retrieval_score": min_score,
                "revision_instructions": [
                    "Use only these claims that were individually corroborated by the local RAG references: "
                    + " | ".join(str(item["claim"]) for item in supported_claims),
                ],
                "web_candidate": {
                    "title": page.title[:500], "url": page.final_url,
                    "host": hit.host,
                    "published_at": page.published_at.isoformat() if page.published_at else None,
                    "fetched_at": page.fetched_at.isoformat(),
                    "rag_score": best_score,
                    "corroborating_document_ids": list(verified_document_ids),
                    "corroborating_chunk_ids": list(dict.fromkeys(
                        chunk.chunk_id for chunk in supported_chunks.values()
                    )),
                    "verified_claims": supported_claims,
                    "unsupported_claim_count": sum(
                        1 for item in raw_claims
                        if isinstance(item, dict) and item.get("status") == "insufficient"
                    ),
                },
            }
            candidate_key = hashlib.sha256(page.final_url.encode("utf-8")).hexdigest()[:24]
            await self.queues.enqueue(_job(
                QueueName.GENERATION, generation_payload,
                f"web_candidate_generation:{menu_work_id}:{content_type.value}:{candidate_key}",
            ))
            await self.queues.save(job.transition(QueueStatus.SUCCEEDED, now=_now()))
            logger.info(
                "web_candidate_accepted job_id=%s work_id=%d rag_score=%.4f verified_claims=%d",
                job.job_id, menu_work_id, best_score, len(supported_claims),
            )
        except Exception as error:
            failed = _failure(job, "web_candidate_error", error)
            await self.queues.save(failed)
            logger.warning("web_candidate_failed job_id=%s status=%s error_type=%s", job.job_id, failed.status.value, type(error).__name__)
        return True


def _candidate_rag_query(page: WebCandidatePage, hit: WebCandidateHit) -> str:
    paragraphs = [
        re.sub(r"[`*_>#\[\]()]", " ", value).strip()
        for value in re.split(r"\n\s*\n", page.body[:12_000])
    ]
    lead = next((value for value in paragraphs if len(value) >= 35), "")
    parts = [page.title, hit.snippet, lead[:320]]
    return " ".join(value.strip() for value in parts if value and value.strip())[:1_200]
