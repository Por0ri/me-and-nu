"""Evidence chunking and conservative lexical reranking behind a DB retrieval port."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol, Sequence

from ani_rag.models import EvidenceDocument, RagContentType, WorkLinkStatus

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    content_type: RagContentType
    menu_work_ids: tuple[int, ...] = ()
    seed_evidence_ids: tuple[str, ...] = ()
    top_k: int = 8
    candidate_limit: int = 100

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("RAG retrieval needs an explicit query")
        if not 1 <= self.top_k <= self.candidate_limit <= 1000:
            raise ValueError("Retrieval limits must satisfy 1 <= top_k <= candidate_limit <= 1000")
        if any(work_id <= 0 for work_id in self.menu_work_ids):
            raise ValueError("Retrieval work IDs must be positive")


@dataclass(frozen=True)
class EvidenceChunk:
    chunk_id: str
    evidence_id: str
    content_type: RagContentType
    title: str
    text: str
    locator: str
    source_kind: str
    source_index: int | None
    start_offset: int
    end_offset: int
    source_id: str
    source_url: str
    language: str
    published_at: datetime | None
    menu_work_ids: tuple[int, ...]
    score: float = 0.0


@dataclass(frozen=True)
class RetrievedEvidence:
    retrieval_id: str
    documents: tuple[EvidenceDocument, ...]
    chunks: tuple[EvidenceChunk, ...]
    query: str
    content_type: RagContentType
    menu_work_ids: tuple[int, ...]
    seed_evidence_ids: tuple[str, ...]
    retrieved_at: datetime
    backend: str = "lexical"

    def __post_init__(self) -> None:
        if not self.retrieval_id.strip() or not self.query.strip() or not self.backend.strip():
            raise ValueError("Retrieval trace needs an ID, query, and backend")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if any(work_id <= 0 for work_id in self.menu_work_ids):
            raise ValueError("Retrieval work IDs must be positive")


class GenerationWorkStatus(StrEnum):
    ENQUEUED = "enqueued"
    DRAFTED = "drafted"
    WAITING_FOR_EVIDENCE = "waiting_for_evidence"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class GenerationWorkRequest:
    work_id: str
    query: str
    content_type: RagContentType
    seed_evidence_ids: tuple[str, ...]
    menu_work_ids: tuple[int, ...] = ()
    top_k: int = 8
    candidate_limit: int = 100

    def __post_init__(self) -> None:
        if not self.work_id.strip() or not self.query.strip() or not self.seed_evidence_ids:
            raise ValueError("Generation work needs a stable ID, query, and at least one stored seed")
        if not 1 <= self.top_k <= self.candidate_limit <= 1000:
            raise ValueError("Generation retrieval limits must satisfy 1 <= top_k <= candidate_limit <= 1000")


class EvidenceCandidateRepository(Protocol):
    async def fetch_candidates(self, request: RetrievalRequest) -> Sequence[EvidenceDocument]:
        """Fetch bounded DB candidates and include seed IDs; preserve document IDs for hydration."""


class GenerationBacklogRepository(Protocol):
    async def pending_generation_work(self, *, limit: int) -> Sequence[GenerationWorkRequest]:
        """Read transactional outbox/backlog rows, including pre-existing eligible evidence."""

    async def mark_generation_enqueued(self, work_id: str, job_id: str) -> None:
        """Mark a backlog row only after idempotent queue insertion succeeds."""

    async def mark_generation_result(self, work_id: str, status: GenerationWorkStatus, *, error: str | None = None) -> None:
        """Record a draft, wait-for-evidence, or terminal failure without losing history."""


class EvidenceRetriever(Protocol):
    async def retrieve(self, request: RetrievalRequest) -> RetrievedEvidence:
        """Retrieve source-of-truth documents plus the exact chunks used for ranking."""


def chunk_document(document: EvidenceDocument, *, max_chars: int = 1200, overlap: int = 120) -> tuple[EvidenceChunk, ...]:
    if max_chars <= 0 or overlap < 0 or overlap >= max_chars:
        raise ValueError("Chunk size must be positive and overlap smaller than chunk size")
    sources = _document_sources(document)
    chunks: list[EvidenceChunk] = []
    seen: set[str] = set()
    step = max_chars - overlap
    for source_kind, locator, source_index, text in sources:
        for start in range(0, len(text), step):
            raw = text[start:start + max_chars]
            left_trimmed = len(raw) - len(raw.lstrip())
            right_bound = len(raw.rstrip())
            value = raw.strip()
            if not value:
                continue
            actual_start = start + left_trimmed
            actual_end = start + right_bound
            signature = hashlib.sha256(value.encode("utf-8")).hexdigest()
            if signature in seen:
                continue
            seen.add(signature)
            chunk_id = hashlib.sha256(f"{document.document_id}\n{source_kind}\n{source_index}\n{locator}\n{actual_start}\n{value}".encode("utf-8")).hexdigest()
            chunks.append(EvidenceChunk(
                chunk_id=chunk_id,
                evidence_id=document.document_id,
                content_type=document.content_type,
                title=document.title,
                text=value,
                locator=f"{locator}:{actual_start}-{actual_end}",
                source_kind=source_kind,
                source_index=source_index,
                start_offset=actual_start,
                end_offset=actual_end,
                source_id=document.source_id,
                source_url=document.source_url,
                language=document.language,
                published_at=document.published_at,
                menu_work_ids=document.menu_work_ids,
            ))
            if start + max_chars >= len(text):
                break
    return tuple(chunks)


def _document_sources(document: EvidenceDocument) -> list[tuple[str, str, int | None, str]]:
    """Expose Kitsu character records as independently retrievable evidence chunks.

    The stored raw JSON remains the canonical source. Chunking its whole included
    array as one text blob made character searches retrieve arbitrary slices and
    encouraged generation from the anime synopsis instead of character profiles.
    """
    if document.source_id == "kitsu_anime_catalog" and document.body:
        try:
            payload = json.loads(document.body)
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            rows = payload.get("data")
            anime = rows[0] if isinstance(rows, list) and rows else rows
            if isinstance(anime, dict) and anime.get("type") == "anime":
                attributes = anime.get("attributes")
                attributes = attributes if isinstance(attributes, dict) else {}
                title = attributes.get("canonicalTitle") or document.title
                titles = attributes.get("titles")
                localized = []
                if isinstance(titles, dict):
                    localized = [str(value) for value in titles.values() if isinstance(value, str) and value.strip()]
                work_label = " / ".join(dict.fromkeys([str(title), *localized]))
                sources: list[tuple[str, str, int | None, str]] = []
                synopsis = attributes.get("synopsis")
                if isinstance(synopsis, str) and synopsis.strip():
                    sources.append(("body", "kitsu:anime/synopsis", 0,
                                    f"Work: {work_label}\nAnime synopsis: {synopsis.strip()}"))

                included = payload.get("included")
                included = included if isinstance(included, list) else []
                character_roles: dict[str, str] = {}
                for record in included:
                    if not isinstance(record, dict) or record.get("type") != "animeCharacters":
                        continue
                    relationships = record.get("relationships")
                    character = relationships.get("character") if isinstance(relationships, dict) else None
                    relation_data = character.get("data") if isinstance(character, dict) else None
                    character_id = relation_data.get("id") if isinstance(relation_data, dict) else None
                    relation_attrs = record.get("attributes")
                    role = relation_attrs.get("role") if isinstance(relation_attrs, dict) else None
                    if character_id is not None:
                        character_roles[str(character_id)] = str(role or "unspecified")

                characters = [record for record in included
                              if isinstance(record, dict) and record.get("type") == "characters"]
                # Main cast first makes a title-only collection task surface useful
                # profiles without pretending the source query selected a character.
                characters.sort(key=lambda record: (
                    0 if character_roles.get(str(record.get("id"))) == "main" else 1,
                    str((record.get("attributes") or {}).get("name", "")).casefold()
                    if isinstance(record.get("attributes"), dict) else "",
                ))
                for index, record in enumerate(characters, start=1):
                    record_id = str(record.get("id") or "").strip()
                    record_attrs = record.get("attributes")
                    record_attrs = record_attrs if isinstance(record_attrs, dict) else {}
                    name = record_attrs.get("name")
                    description = record_attrs.get("description")
                    if not record_id or not isinstance(name, str) or not name.strip():
                        continue
                    if not isinstance(description, str) or not description.strip():
                        continue
                    role = character_roles.get(record_id, "unspecified")
                    text = (f"Work: {work_label}\nCharacter profile: {name.strip()}\n"
                            f"Anime character role: {role}\nDescription: {description.strip()}")
                    sources.append(("body", f"kitsu:characters/{record_id}/{name.strip()}", index, text))
                return sources

    sources: list[tuple[str, str, int | None, str]] = []
    if document.body and document.body.strip():
        sources.append(("body", "body", None, document.body))
    sources.extend(("excerpt", excerpt.locator or f"excerpt:{index}", index, excerpt.text)
                   for index, excerpt in enumerate(document.excerpts))
    return sources


def _terms(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN.findall(value)}


def lexical_score(query: str, chunk: EvidenceChunk, *, seed_ids: frozenset[str] = frozenset()) -> float:
    query_terms = _terms(query)
    if not query_terms:
        return 0.0
    text_terms = _terms(f"{chunk.title} {chunk.text}")
    overlap = len(query_terms & text_terms)
    score = overlap / len(query_terms)
    if query.casefold().strip() in f"{chunk.title} {chunk.text}".casefold():
        score += 0.25
    if chunk.evidence_id in seed_ids:
        score += 0.05
    if chunk.locator.startswith("kitsu:characters/"):
        # A broad character-lane query should prefer the source's designated main
        # cast profiles, while still allowing exact name terms to dominate.
        if "Anime character role: main" in chunk.text:
            score += 0.12
    return score


class DatabaseEvidenceRetriever:
    """Rerank DB/FTS/vector candidates locally and retain per-claim source chunks.

    The candidate repository is a Protocol; no DB adapter or embedding service is
    silently selected. Vector search may propose candidates, never replace originals.
    """

    def __init__(self, candidates: EvidenceCandidateRepository) -> None:
        self.candidates = candidates

    async def retrieve(self, request: RetrievalRequest) -> RetrievedEvidence:
        documents = await self.candidates.fetch_candidates(request)
        unique_documents = {document.document_id: document for document in documents[:request.candidate_limit]}
        allowed: list[EvidenceDocument] = []
        for document in unique_documents.values():
            if document.content_type is not request.content_type:
                continue
            if request.menu_work_ids and (
                document.work_link_status is not WorkLinkStatus.MATCHED
                or not set(document.menu_work_ids).intersection(request.menu_work_ids)
            ):
                continue
            allowed.append(document)
        seed_ids = frozenset(request.seed_evidence_ids)
        ranked = [
            (lexical_score(request.query, chunk, seed_ids=seed_ids), document, chunk)
            for document in allowed
            for chunk in chunk_document(document)
        ]
        ranked = [row for row in ranked if row[0] > 0]
        ranked.sort(key=lambda row: (
            -row[0], row[1].published_at is None,
            -(row[1].published_at.timestamp()) if row[1].published_at else 0.0,
            row[1].document_id, row[2].locator,
        ))
        picked_chunks: list[EvidenceChunk] = []
        picked_docs: dict[str, EvidenceDocument] = {}
        for score, document, chunk in ranked:
            picked_chunks.append(EvidenceChunk(**{**chunk.__dict__, "score": score}))
            picked_docs.setdefault(document.document_id, document)
            if len(picked_chunks) >= request.top_k:
                break
        return RetrievedEvidence(
            retrieval_id=f"ret_{uuid.uuid4().hex}",
            documents=tuple(picked_docs.values()),
            chunks=tuple(picked_chunks),
            query=request.query,
            content_type=request.content_type,
            menu_work_ids=request.menu_work_ids,
            seed_evidence_ids=request.seed_evidence_ids,
            retrieved_at=datetime.now(timezone.utc),
        )
