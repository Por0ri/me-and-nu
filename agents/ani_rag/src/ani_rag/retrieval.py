"""Evidence chunking and conservative lexical reranking behind a DB retrieval port."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import struct
import threading
import uuid
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Iterable, Mapping, Protocol, Sequence

from ani_rag.adapter import canonical_article_comparison_url
from ani_rag.article_credits import ArticleCredit, chunk_credit_matches
from ani_rag.episode_evidence import EpisodeTarget, episode_reference_matches
from ani_rag.fact_subject import fact_subjects
from ani_rag.models import EvidenceDocument, RagContentType, WorkLinkStatus
from ani_rag.profiles import get_content_profile

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
DEFAULT_MIN_RETRIEVAL_SCORE = 0.20


_EPISODE_COUNT = re.compile(
    r"몇\s*(?:부작|화|회)(?!\s*(?:에서?|부터|까지|쯤))|(?:화|회)\s*수(?:[가를는야?\s]|$)|"
    r"(?:회차|에피소드)\s*(?:수|개수)|총\s*(?:회차|화수)|"
    r"\b(?:episode[ _]count|number of episodes|how many episodes)\b|\bepisodes\s*[?？]?$|全何話|話数", re.I
)
_DURATION = re.compile(
    r"러닝\s*타임|런닝\s*타임|상영\s*시간|(?:한\s*(?:편|회|화)|회당|편당)\s*(?:몇\s*분|길이|시간)|"
    r"방영\s*시간(?!\s*(?:대|표))|\b(?:run\s*time|duration(?:_min)?|episode length|how long is (?:an|one) episode)\b|上映時間|所要時間", re.I
)
_AIRTIME = re.compile(r"몇\s*시|시간대|시간표|무슨\s*요일|방송\s*편성|몇\s*요일")
_VOICE_CAST = re.compile(r"성우|목소리\s*(?:연기|담당)|\b(?:voice[ _]actors?|voice cast|voiced by|voices for)\b|声優", re.I)
_KOREAN_CAST = re.compile(r"한국(?:어|판)?|한국\s*더빙|우리말|\bKorean\b|韓国語", re.I)
_JAPANESE_CAST = re.compile(r"일본(?:어|판)?|원어|\bJapanese\b|日本語", re.I)


def requested_dictionary_fields(request: RetrievalRequest) -> tuple[str, ...]:
    """Recognize focused, work-scoped fact queries; full web articles use normal relevance."""
    query = (request.subject_title or request.query).strip()
    if not request.menu_work_ids or request.seed_evidence_ids or len(query) > 160:
        return ()
    fields = []
    if _EPISODE_COUNT.search(query):
        fields.append("episodes")
    if _DURATION.search(query) and not _AIRTIME.search(query):
        fields.append("duration_min")
    if _VOICE_CAST.search(query):
        korean = _KOREAN_CAST.search(query)
        japanese = _JAPANESE_CAST.search(query)
        if korean or not japanese:
            fields.append("voice_actor_ko")
        if japanese or not korean:
            fields.extend(("voice_actor", "jp_cast"))
    return tuple(fields)


def _matches_dictionary_field(document: EvidenceDocument, fields: tuple[str, ...]) -> bool:
    return bool(fields and document.metadata.get("dataset") == "menu_dict"
                and document.metadata.get("menu_dict_field") in fields)


def _canonical_excluded_url(url: str) -> str:
    try:
        return canonical_article_comparison_url(url)
    except ValueError:
        return url.strip()


def _canonical_excluded_urls(urls: Sequence[str]) -> frozenset[str]:
    return frozenset(_canonical_excluded_url(url) for url in urls)


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    content_type: RagContentType
    menu_work_ids: tuple[int, ...] = ()
    source_ids: tuple[str, ...] = ()
    seed_evidence_ids: tuple[str, ...] = ()
    exclude_source_urls: tuple[str, ...] = ()
    top_k: int = 8
    candidate_limit: int = 100
    min_retrieval_score: float = DEFAULT_MIN_RETRIEVAL_SCORE
    semantic_candidate_scan: bool = False
    subject_title: str | None = None
    corroborating_chunks: tuple[str, ...] = ()
    article_credits: tuple[ArticleCredit, ...] = ()
    episode_targets: tuple[EpisodeTarget, ...] = ()

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("RAG retrieval needs an explicit query")
        if not 1 <= self.top_k <= self.candidate_limit <= 1000:
            raise ValueError("Retrieval limits must satisfy 1 <= top_k <= candidate_limit <= 1000")
        if not math.isfinite(self.min_retrieval_score) or not 0.0 <= self.min_retrieval_score <= 1.0:
            raise ValueError("min_retrieval_score must be finite and between 0 and 1")
        if any(work_id <= 0 for work_id in self.menu_work_ids):
            raise ValueError("Retrieval work IDs must be positive")
        if any(not source_id.strip() for source_id in self.source_ids):
            raise ValueError("Retrieval source IDs must be non-empty")
        if any(not source_url.strip() for source_url in self.exclude_source_urls):
            raise ValueError("Excluded source URLs must be non-empty")


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


def _preferred_credit_chunks(
    request: RetrievalRequest, chunks: Iterable[tuple[float, EvidenceChunk]],
) -> set[str]:
    if not request.article_credits:
        return set()
    ranked = []
    for score, chunk in chunks:
        if score >= request.min_retrieval_score:
            matches = chunk_credit_matches(request.article_credits, chunk.text, source_url=chunk.source_url)
            if matches:
                ranked.append((-matches, -score, chunk.chunk_id))
    # Retain general story and production context for the rest of the verifier's references.
    slots = max(1, request.top_k // 2)
    return {chunk_id for _, _, chunk_id in sorted(ranked)[:slots]}


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
    async def enqueue_generation_work(self, item: GenerationWorkRequest) -> bool:
        """Add one explicitly selected, evidence-seeded generation request idempotently."""

    async def pending_generation_work(self, *, limit: int) -> Sequence[GenerationWorkRequest]:
        """Read transactional outbox/backlog rows, including pre-existing eligible evidence."""

    async def mark_generation_enqueued(self, work_id: str, job_id: str) -> None:
        """Mark a backlog row only after idempotent queue insertion succeeds."""

    async def mark_generation_result(self, work_id: str, status: GenerationWorkStatus, *, error: str | None = None) -> None:
        """Record a draft, wait-for-evidence, or terminal failure without losing history."""


class EvidenceRetriever(Protocol):
    async def retrieve(self, request: RetrievalRequest) -> RetrievedEvidence:
        """Retrieve source-of-truth documents plus the exact chunks used for ranking."""


class QueryEmbeddingModel(Protocol):
    async def embed_query(self, text: str) -> Sequence[float]:
        """Embed one query using the same model and dimensions as the vector sidecar."""


class VectorIndexReader(Protocol):
    def fetch_chunks(
        self, document_ids: tuple[str, ...], query_vector: tuple[float, ...],
    ) -> Sequence[Mapping[str, object]]:
        """Read indexed chunks for scoped documents with cosine_similarity scores.

        Implementations must preserve chunk/document/source identity and return
        similarity (higher is closer), not distance. The relational repository
        remains authoritative for candidate eligibility and work links.
        """


class OpenAIQueryEmbeddingModel:
    """Small adapter for the existing text-embedding-3-small sidecar contract."""

    def __init__(self, client: object) -> None:
        self.client = client

    async def embed_query(self, text: str) -> Sequence[float]:
        def call():
            embeddings = getattr(self.client, "embeddings", None)
            create = getattr(embeddings, "create", None)
            if not callable(create):
                raise TypeError("Embedding client must expose embeddings.create")
            return create(
                model="text-embedding-3-small", input=text,
                dimensions=1024, encoding_format="float",
            )
        response = await asyncio.to_thread(call)
        data = getattr(response, "data", None)
        if not data or len(data) != 1:
            raise RuntimeError("Query embedding response must contain exactly one vector")
        vector = tuple(float(value) for value in data[0].embedding)
        if len(vector) != 1024 or not all(math.isfinite(value) for value in vector):
            raise RuntimeError("Query embedding does not match the 1024-dimension sidecar")
        return vector


def chunk_document(document: EvidenceDocument, *, max_chars: int = 1200, overlap: int = 120) -> tuple[EvidenceChunk, ...]:
    if max_chars <= 0 or overlap < 0 or overlap >= max_chars:
        raise ValueError("Chunk size must be positive and overlap smaller than chunk size")
    sources = document_chunk_sources(document)
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


def document_chunk_sources(document: EvidenceDocument) -> list[tuple[str, str, int | None, str]]:
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
        excluded_urls = _canonical_excluded_urls(request.exclude_source_urls)
        accepted_evidence_types = set(get_content_profile(request.content_type).accepted_evidence_types)
        fields = requested_dictionary_fields(request)
        subjects = fact_subjects(request.subject_title or request.query, request.menu_work_ids, fields,
                                 article=bool(request.subject_title))
        allowed: list[EvidenceDocument] = []
        for document in unique_documents.values():
            # A source document's collection lane does not define every format it
            # can support. Reuse it only when this requested lane explicitly
            # accepts the preserved evidence type (for example, ANN reference
            # facts collected through the guide lane can support people/music).
            if document.evidence_type not in accepted_evidence_types:
                continue
            if fields and document.metadata.get("dataset") == "menu_dict" and not subjects.accepts(document, fields):
                continue
            if fields and document.metadata.get("dataset") != "menu_dict" and not subjects.accepts_external(document):
                continue
            if request.menu_work_ids and (
                document.work_link_status is not WorkLinkStatus.MATCHED
                or not set(document.menu_work_ids).intersection(request.menu_work_ids)
            ):
                continue
            if excluded_urls and _canonical_excluded_url(document.source_url) in excluded_urls:
                continue
            allowed.append(document)
        seed_ids = frozenset(request.seed_evidence_ids)
        # A translated field name is one lexical match, even if the fact itself is in Japanese.
        field_overlap = 1 / max(len(_terms(request.query)), 1)
        ranked = [
            (max(lexical_score(request.query, chunk, seed_ids=seed_ids),
                 field_overlap if _matches_dictionary_field(document, fields) else 0.0), document, chunk)
            for document in allowed
            for chunk in chunk_document(document)
        ]
        ranked = [row for row in ranked if row[0] > 0]
        pinned = frozenset(request.corroborating_chunks)
        preferred = _preferred_credit_chunks(request, ((score, chunk) for score, _, chunk in ranked))
        ranked.sort(key=lambda row: (
            not (row[2].chunk_id in pinned and row[0] >= request.min_retrieval_score),
            not _matches_dictionary_field(row[1], fields),
            row[2].chunk_id not in preferred,
            not (row[0] >= request.min_retrieval_score and episode_reference_matches(
                request.episode_targets, row[2].title, row[2].text,
            )),
            -row[0], row[1].published_at is None,
            -(row[1].published_at.timestamp()) if row[1].published_at else 0.0,
            row[1].document_id, row[2].locator,
        ))
        picked_chunks: list[EvidenceChunk] = []
        picked_docs: dict[str, EvidenceDocument] = {}
        cast_seen = set()
        for score, document, chunk in ranked:
            cast_key = subjects.cast_key(document)
            if cast_key is not None:
                if cast_key in cast_seen:
                    continue
                cast_seen.add(cast_key)
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


class VectorEvidenceRetriever:
    """Rank bounded, work/source-filtered DB candidates with stored cosine vectors.

    The vector sidecar proposes supporting chunks; all candidate documents are
    still hydrated and scoped by the relational repository. Unindexed new docs
    use the existing lexical retriever until the incremental index catches up.
    """

    def __init__(
        self, candidates: EvidenceCandidateRepository,
        vector_index: sqlite3.Connection | VectorIndexReader,
        query_embeddings: QueryEmbeddingModel,
    ) -> None:
        self.candidates = candidates
        self.vector_index = vector_index
        self.query_embeddings = query_embeddings
        self._lock = threading.RLock()

    async def retrieve(self, request: RetrievalRequest) -> RetrievedEvidence:
        scoped_request = RetrievalRequest(**{
            **request.__dict__, "semantic_candidate_scan": True,
        })
        documents = await self.candidates.fetch_candidates(scoped_request)
        accepted_evidence_types = set(get_content_profile(request.content_type).accepted_evidence_types)
        fields = requested_dictionary_fields(request)
        subjects = fact_subjects(request.subject_title or request.query, request.menu_work_ids, fields,
                                 article=bool(request.subject_title))
        documents = tuple(
            document for document in documents
            if document.evidence_type in accepted_evidence_types
            and (not fields or document.metadata.get("dataset") != "menu_dict" or subjects.accepts(document, fields))
            and (not fields or document.metadata.get("dataset") == "menu_dict" or subjects.accepts_external(document))
            and (not request.menu_work_ids or (
                document.work_link_status is WorkLinkStatus.MATCHED
                and bool(set(document.menu_work_ids).intersection(request.menu_work_ids))
            ))
            and _canonical_excluded_url(document.source_url) not in _canonical_excluded_urls(request.exclude_source_urls)
        )
        if not documents:
            return RetrievedEvidence(
                retrieval_id=f"ret_{uuid.uuid4().hex}", documents=(), chunks=(),
                query=request.query, content_type=request.content_type,
                menu_work_ids=request.menu_work_ids, seed_evidence_ids=request.seed_evidence_ids,
                retrieved_at=datetime.now(timezone.utc), backend="vector_cosine",
            )
        query_vector = tuple(await self.query_embeddings.embed_query(request.query))
        if len(query_vector) != 1024 or not all(math.isfinite(value) for value in query_vector):
            raise ValueError("Query vector must contain 1024 finite values")
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        if query_norm <= 0:
            raise ValueError("Query vector cannot be zero")
        document_map = {item.document_id: item for item in documents}
        chunk_rows = await asyncio.to_thread(self._fetch_index_chunks, tuple(document_map), query_vector)
        if not chunk_rows:
            return await DatabaseEvidenceRetriever(self.candidates).retrieve(request)
        ranked: list[tuple[float, EvidenceChunk, EvidenceDocument]] = []
        pinned = frozenset(request.corroborating_chunks)
        current_chunks: dict[str, dict[str, EvidenceChunk]] = {}
        for row in chunk_rows:
            document = document_map.get(str(row["document_id"]))
            if document is None:
                continue
            if row["chunk_id"] in pinned:
                # An old vector can survive a source edit until the next index refresh.
                # Rebuild the cited slice from the current relational source before reusing it.
                if document.document_id not in current_chunks:
                    current_chunks[document.document_id] = {
                        c.chunk_id: c for c in chunk_document(document)
                    }
                original = current_chunks[document.document_id].get(row["chunk_id"])
                if (original is None or original.text != row["chunk_text"]
                        or original.source_url != row["source_url"] or original.source_id != row["source_id"]):
                    continue
            if "cosine_similarity" in row.keys():
                # PostgreSQL's <=> is a distance; the index adapter supplies
                # 1 - distance, preserving the existing score direction.
                if row["cosine_similarity"] is None:
                    continue
                similarity = float(row["cosine_similarity"])
                if not math.isfinite(similarity):
                    raise ValueError("Vector index returned a non-finite cosine similarity")
            else:
                vector = struct.unpack("<1024f", row["vector_f32"])
                vector_norm = math.sqrt(sum(value * value for value in vector))
                if vector_norm <= 0:
                    continue
                similarity = sum(left * right for left, right in zip(query_vector, vector, strict=True)) / (query_norm * vector_norm)
            score = max(0.0, min(1.0, similarity))
            ranked.append((score, EvidenceChunk(
                chunk_id=str(row["chunk_id"]), evidence_id=str(row["document_id"]),
                content_type=RagContentType(str(row["content_type"])),
                title=str(row["title"]), text=str(row["chunk_text"]),
                locator=str(row["locator"]), source_kind=str(row["source_kind"]),
                source_index=int(row["source_index"]) if row["source_index"] is not None else None,
                start_offset=int(row["start_offset"]), end_offset=int(row["end_offset"]),
                source_id=str(row["source_id"]), source_url=str(row["source_url"]),
                language=str(row["language"]),
                published_at=datetime.fromisoformat(row["published_at"]) if row["published_at"] else None,
                menu_work_ids=document.menu_work_ids, score=score,
            ), document))
        preferred = _preferred_credit_chunks(request, ((score, chunk) for score, chunk, _ in ranked))
        ranked.sort(key=lambda item: (
            item[1].chunk_id not in pinned,
            not _matches_dictionary_field(item[2], fields),
            item[1].chunk_id not in preferred,
            not episode_reference_matches(request.episode_targets, item[1].title, item[1].text),
            -item[0], item[2].published_at is None,
            -(item[2].published_at.timestamp()) if item[2].published_at else 0.0,
            item[2].document_id, item[1].locator,
        ))
        picked: list[EvidenceChunk] = []
        picked_docs: dict[str, EvidenceDocument] = {}
        cast_seen = set()
        for score, chunk, document in ranked:
            if score < request.min_retrieval_score:
                continue
            cast_key = subjects.cast_key(document)
            if cast_key is not None:
                if cast_key in cast_seen:
                    continue
                cast_seen.add(cast_key)
            picked.append(chunk)
            picked_docs.setdefault(document.document_id, document)
            if len(picked) >= request.top_k:
                break
        if not picked:
            lexical = await DatabaseEvidenceRetriever(self.candidates).retrieve(request)
            if lexical.chunks:
                return lexical
        return RetrievedEvidence(
            retrieval_id=f"ret_{uuid.uuid4().hex}", documents=tuple(picked_docs.values()),
            chunks=tuple(picked), query=request.query, content_type=request.content_type,
            menu_work_ids=request.menu_work_ids, seed_evidence_ids=request.seed_evidence_ids,
            retrieved_at=datetime.now(timezone.utc), backend="vector_cosine",
        )

    def _fetch_index_chunks(
        self, document_ids: tuple[str, ...], query_vector: tuple[float, ...],
    ) -> Sequence[sqlite3.Row | Mapping[str, object]]:
        with self._lock:
            if not isinstance(self.vector_index, sqlite3.Connection):
                return self.vector_index.fetch_chunks(document_ids, query_vector)
            self.vector_index.row_factory = sqlite3.Row
            query_only = self.vector_index.execute("PRAGMA query_only").fetchone()
            if query_only is None or int(query_only[0]) != 1:
                raise PermissionError("Vector sidecar connection must be opened read-only")
            metadata = dict(self.vector_index.execute("SELECT key,value FROM index_metadata"))
            if (metadata.get("model") != "text-embedding-3-small"
                    or metadata.get("dimensions") != "1024"
                    or metadata.get("completed_at") is None):
                raise RuntimeError("A completed 1024-dimension vector sidecar is required")
            rows: list[sqlite3.Row] = []
            for offset in range(0, len(document_ids), 400):
                batch = document_ids[offset:offset + 400]
                marks = ",".join("?" for _ in batch)
                rows.extend(self.vector_index.execute(
                    f"""SELECT c.chunk_id,c.document_id,c.content_type,c.title,c.source_url,
                               c.language,c.source_kind,c.source_index,c.locator,c.start_offset,
                               c.end_offset,c.chunk_text,c.source_id,c.published_at,v.vector_f32
                        FROM evidence_chunk c JOIN embedding_vector v ON v.text_hash=c.text_hash
                        WHERE c.document_id IN ({marks})""", batch,
                ).fetchall())
            return rows
