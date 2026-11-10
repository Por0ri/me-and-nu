"""SQLite adapters for draft persistence, review history, and registered images.

No image search, download, rights inference, database connection, or schema setup
is performed here. Image assets must be explicitly registered with provenance.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from ani_rag.content import (
    CardDraft, CardLayout, ContentDraft, ContentEntityLink, ContentTag,
    ImageAsset, ImageEntityLink, NormalizedBox, SupportedClaim, TEXT_ONLY_ASSET_ID,
)
from ani_rag.image_ingest import ImageCandidate
from ani_rag.models import EvidenceDocument, RagContentType
from ani_rag.retrieval import EvidenceChunk, RetrievedEvidence
from ani_rag.review import ReviewResult
from ani_rag.agents.content_generation.renderer import RenderedCard
from ani_rag.storage.sqlite_core import SQLiteRagStore


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _execute_with_stage(connection: sqlite3.Connection, stage: str, sql: str, parameters=()):
    try:
        return connection.execute(sql, parameters)
    except sqlite3.IntegrityError as error:
        raise sqlite3.IntegrityError(f"{stage}: {error}") from error


def _claim_id(draft_id: str, card_id: str | None, index: int) -> str:
    seed = f"{draft_id}:{card_id or 'detail'}:{index}"
    return f"cl_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:28]}"


def _claim_evidence_id(claim_id: str, document_id: str, chunk_id: str) -> str:
    return f"ce_{hashlib.sha256(f'{claim_id}:{document_id}:{chunk_id}'.encode()).hexdigest()[:28]}"


class SQLiteDraftRepository:
    """Draft/claim persistence over the same dedicated connection as SQLiteRagStore."""

    def __init__(self, store: SQLiteRagStore) -> None:
        self.store = store

    async def save(
        self, draft: ContentDraft, retrieval: RetrievedEvidence,
        rendered_cards: Sequence[RenderedCard],
    ) -> None:
        await asyncio.to_thread(self._save, draft, retrieval, rendered_cards)

    def _save(
        self, draft: ContentDraft, retrieval: RetrievedEvidence,
        rendered_cards: Sequence[RenderedCard],
    ) -> None:
        if draft.retrieval_id != retrieval.retrieval_id:
            raise ValueError("Draft retrieval ID does not match the supplied retrieval record")
        documents = {item.document_id: item for item in retrieval.documents}
        chunks = {item.chunk_id: item for item in retrieval.chunks}
        if not set(draft.evidence_ids) <= set(documents):
            raise ValueError("Draft evidence must be present in the supplied retrieval")
        if not set(draft.retrieved_chunk_ids) <= set(chunks):
            raise ValueError("Draft chunks must be present in the supplied retrieval")
        rendered_by_id = {item.card_id: item for item in rendered_cards}
        expected_card_ids = {card.card_id for card in draft.cards}
        if set(rendered_by_id) != expected_card_ids:
            raise ValueError("Every draft page must have exactly one rendered PNG plate")
        store = self.store
        with store._transaction():
            _execute_with_stage(store._connection, "retrieval",
                """INSERT INTO rag_retrieval(retrieval_id,query_text,content_type,work_ids_json,
                   seed_evidence_ids_json,retrieved_at,backend) VALUES(?,?,?,?,?,?,?)""",
                (retrieval.retrieval_id, retrieval.query, retrieval.content_type.value,
                 _json(list(retrieval.menu_work_ids)), _json(list(retrieval.seed_evidence_ids)),
                 retrieval.retrieved_at.isoformat(), retrieval.backend),
            )
            for chunk in retrieval.chunks:
                if chunk.evidence_id not in documents:
                    raise ValueError(f"Chunk {chunk.chunk_id!r} has no retrieval document")
                _execute_with_stage(store._connection, "chunk",
                    """INSERT OR IGNORE INTO rag_chunk(chunk_id,document_id,locator,source_kind,
                       source_index,chunk_text,start_offset,end_offset,language,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (chunk.chunk_id, chunk.evidence_id, chunk.locator, chunk.source_kind,
                     chunk.source_index, chunk.text, chunk.start_offset, chunk.end_offset,
                     chunk.language, retrieval.retrieved_at.isoformat()),
                )
            for rank, chunk in enumerate(retrieval.chunks, start=1):
                _execute_with_stage(store._connection, "retrieval_hit",
                    "INSERT INTO rag_retrieval_hit(retrieval_id,chunk_id,rank,score) VALUES(?,?,?,?)",
                    (retrieval.retrieval_id, chunk.chunk_id, rank, chunk.score),
                )
            _execute_with_stage(store._connection, "content_draft",
                """INSERT INTO rag_content_draft(draft_id,content_type,topic_id,topic_label,title,body,summary,
                   keyword_json,thumbnail_url,is_sponsored,is_changed_notice,ai_judgment_basis_json,
                   event_detail_id,published_at,valid_until,embedding_json,source_footer,ai_disclosure,menu_work_ids_json,
                   retrieved_chunk_ids_json,retrieval_id,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (draft.draft_id, draft.content_type.value, draft.topic_id, draft.topic_label,
                 draft.title, draft.detail_body,
                 draft.summary, _json(list(draft.keywords)), draft.thumbnail_url, int(draft.is_sponsored),
                 int(draft.is_changed_notice), _json(draft.ai_judgment_basis), draft.event_detail_id,
                 draft.published_at.isoformat() if draft.published_at else None,
                 draft.valid_until.isoformat() if draft.valid_until else None,
                 _json(list(draft.embedding)) if draft.embedding is not None else None,
                 draft.source_footer, draft.ai_disclosure, _json(list(draft.menu_work_ids)),
                 _json(list(draft.retrieved_chunk_ids)), retrieval.retrieval_id, "pending_review",
                 draft.created_at.isoformat(), datetime.now(timezone.utc).isoformat()),
            )
            for evidence_id in draft.evidence_ids:
                _execute_with_stage(store._connection, "draft_evidence",
                    "INSERT INTO rag_draft_evidence(draft_id,document_id,relation) VALUES(?,?,?)",
                    (draft.draft_id, evidence_id, "support"),
                )
                source_row = store._connection.execute(
                    """SELECT d.raw_document_id,d.url,d.published_at,d.metadata_json,s.source_site_id,s.media_name
                       FROM rag_document d JOIN rag_source s ON s.source_id=d.source_id WHERE d.document_id=?""",
                    (evidence_id,),
                ).fetchone()
                if source_row is not None:
                    metadata = json.loads(source_row["metadata_json"])
                    _execute_with_stage(store._connection, "content_source",
                        """INSERT INTO rag_content_source(draft_id,source_url,raw_document_id,source_site_id,publisher,published_at,is_primary)
                           VALUES(?,?,?,?,?,?,?)""",
                        (draft.draft_id, source_row["url"], source_row["raw_document_id"], source_row["source_site_id"],
                         metadata.get("publisher") or source_row["media_name"], source_row["published_at"],
                         int(evidence_id == draft.evidence_ids[0])),
                    )
            for link in draft.entity_links:
                _execute_with_stage(store._connection, "content_entity_link",
                    """INSERT INTO rag_content_entity_link(draft_id,entity_id,link_method,created_at,sort_order)
                       VALUES(?,?,?,?,?)""",
                    (draft.draft_id, link.entity_id, link.link_method, draft.created_at.isoformat(), link.sort_order),
                )
            for tag in draft.tags:
                _execute_with_stage(store._connection, "content_tag",
                    "INSERT INTO rag_content_tag(draft_id,subtopic_id,tagged_by,tagged_at) VALUES(?,?,?,?)",
                    (draft.draft_id, tag.subtopic_id, tag.tagged_by, draft.created_at.isoformat()),
                )
            for position, card in enumerate(draft.cards, start=1):
                _execute_with_stage(store._connection, "content_card",
                    """INSERT INTO rag_content_card(card_id,draft_id,page_no,role,title,body,card_image_url,
                       source_image_url,source_page_url,image_rights_status,image_credit)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (card.card_id, draft.draft_id, position, card.role, card.headline, card.body,
                     card.card_image_url, card.source_image_url, card.source_page_url,
                     card.image_rights_status, card.image_credit),
                )
                _execute_with_stage(store._connection, "card_image",
                    "INSERT INTO rag_card_image(card_id,asset_id) VALUES(?,?)",
                    (card.card_id, None if card.image_asset_id == TEXT_ONLY_ASSET_ID else card.image_asset_id),
                )
                rendered = rendered_by_id[card.card_id]
                _execute_with_stage(store._connection, "rendered_card",
                    """INSERT INTO rag_rendered_card(
                       draft_id,card_id,image_png,width,height,frontend_copy_json,
                       frontend_layout_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                    (draft.draft_id, card.card_id, rendered.image_png, rendered.width, rendered.height,
                     _json(rendered.frontend_copy), _json(rendered.frontend_layout),
                     draft.created_at.isoformat()),
                )
                self._store_claims(draft, card.claims, card.card_id, chunks, position * 1000)
            self._store_claims(draft, draft.detail_claims, None, chunks, 0)

    def _store_claims(
        self, draft: ContentDraft, claims: Sequence[SupportedClaim], card_id: str | None,
        chunks: dict[str, EvidenceChunk], offset: int,
    ) -> None:
        connection = self.store._connection
        for index, claim in enumerate(claims):
            identifier = _claim_id(draft.draft_id, card_id, offset + index)
            _execute_with_stage(connection, "claim",
                "INSERT INTO rag_claim(claim_id,draft_id,card_id,sequence_no,claim_text,attribution) VALUES(?,?,?,?,?,?)",
                (identifier, draft.draft_id, card_id, index + 1, claim.text, claim.attribution),
            )
            for chunk_id in claim.evidence_chunk_ids:
                chunk = chunks.get(chunk_id)
                if chunk is None or chunk.evidence_id not in claim.evidence_ids:
                    continue
                excerpt_id = None
                if chunk.source_kind == "excerpt" and chunk.source_index is not None:
                    excerpt_id = self.store._excerpt_id(chunk.evidence_id, chunk.source_index)
                _execute_with_stage(connection, "claim_evidence",
                    """INSERT INTO rag_claim_evidence(claim_evidence_id,claim_id,document_id,excerpt_id,chunk_id)
                       VALUES(?,?,?,?,?)""",
                    (_claim_evidence_id(identifier, chunk.evidence_id, chunk_id), identifier,
                     chunk.evidence_id, excerpt_id, chunk_id),
                )

    async def load_for_review(self, draft_id: str):
        return await asyncio.to_thread(self._load_for_review, draft_id)

    def _load_for_review(self, draft_id: str):
        from ani_rag.workers import DraftReviewContext

        store = self.store
        with store._lock:
            row = store._connection.execute("SELECT * FROM rag_content_draft WHERE draft_id=?", (draft_id,)).fetchone()
            if row is None:
                raise KeyError(draft_id)
            evidence_ids = tuple(item["document_id"] for item in store._connection.execute(
                "SELECT document_id FROM rag_draft_evidence WHERE draft_id=? ORDER BY document_id", (draft_id,)
            ).fetchall())
            evidence = {identifier: store._load_document(identifier) for identifier in evidence_ids}
            card_rows = store._connection.execute(
                """SELECT c.*,COALESCE(i.asset_id,?) AS asset_id FROM rag_content_card c
                   LEFT JOIN rag_card_image i ON i.card_id=c.card_id
                   WHERE c.draft_id=? ORDER BY c.page_no""", (TEXT_ONLY_ASSET_ID, draft_id)
            ).fetchall()
            layout_rows = store._connection.execute(
                "SELECT card_id,frontend_layout_json FROM rag_rendered_card WHERE draft_id=?",
                (draft_id,),
            ).fetchall()
            layout_by_id = {str(item["card_id"]): json.loads(item["frontend_layout_json"]) for item in layout_rows}

            def _card_layout(card_id: str) -> CardLayout:
                value = layout_by_id[card_id]
                return CardLayout(
                    image_box=NormalizedBox(**value["image_box"]),
                    text_overlay_box=NormalizedBox(**value["text_overlay_box"]),
                    background_style=str(value.get("background_style", "blur")),
                    template=str(value.get("template", "image_top_copy_bottom")),
                    image_emphasis=str(value.get("image_emphasis", "dominant")),
                )

            cards = tuple(CardDraft(
                card_id=str(card["card_id"]), role=str(card["role"]), headline=str(card["title"]),
                body=str(card["body"]), claims=self._load_claims(str(card["card_id"])),
                image_asset_id=str(card["asset_id"]),
                card_image_url=card["card_image_url"], source_image_url=card["source_image_url"],
                source_page_url=card["source_page_url"], image_rights_status=card["image_rights_status"],
                image_credit=card["image_credit"],
                layout=_card_layout(str(card["card_id"])),
            ) for card in card_rows)
            detail_claims = self._load_claims(draft_id, detail=True)
            entity_links = tuple(ContentEntityLink(
                entity_id=int(item["entity_id"]), link_method=str(item["link_method"]),
                sort_order=int(item["sort_order"]),
            ) for item in store._connection.execute(
                "SELECT entity_id,link_method,sort_order FROM rag_content_entity_link WHERE draft_id=? ORDER BY sort_order",
                (draft_id,),
            ).fetchall())
            tags = tuple(ContentTag(
                subtopic_id=int(item["subtopic_id"]), tagged_by=str(item["tagged_by"]),
            ) for item in store._connection.execute(
                "SELECT subtopic_id,tagged_by FROM rag_content_tag WHERE draft_id=? ORDER BY subtopic_id",
                (draft_id,),
            ).fetchall())
            chunk_rows = store._connection.execute(
                """SELECT c.*,h.score,d.content_type,d.title,d.source_id,d.url,
                          d.original_language,d.published_at
                   FROM rag_retrieval_hit h JOIN rag_chunk c ON c.chunk_id=h.chunk_id
                   JOIN rag_document d ON d.document_id=c.document_id
                   WHERE h.retrieval_id=? ORDER BY h.rank""", (row["retrieval_id"],)
            ).fetchall()
            chunks: dict[str, EvidenceChunk] = {}
            for item in chunk_rows:
                doc = evidence.get(str(item["document_id"])) or store._load_document(str(item["document_id"]))
                chunks[str(item["chunk_id"])] = EvidenceChunk(
                    chunk_id=str(item["chunk_id"]), evidence_id=str(item["document_id"]),
                    content_type=RagContentType(item["content_type"]), title=str(item["title"]),
                    text=str(item["chunk_text"]), locator=str(item["locator"]), source_kind=str(item["source_kind"]),
                    source_index=item["source_index"], start_offset=int(item["start_offset"]),
                    end_offset=int(item["end_offset"]), source_id=str(item["source_id"]),
                    source_url=str(item["url"]), language=str(item["original_language"]),
                    published_at=datetime.fromisoformat(item["published_at"]) if item["published_at"] else None,
                    menu_work_ids=doc.menu_work_ids, score=float(item["score"]),
                )
            image_ids = tuple(dict.fromkeys(
                card.image_asset_id for card in cards if card.image_asset_id != TEXT_ONLY_ASSET_ID
            ))
            images = SQLiteImageReader(store)._get_many(image_ids)
            rendered_cards = {
                str(item["card_id"]): bytes(item["image_png"])
                for item in store._connection.execute(
                    "SELECT card_id,image_png FROM rag_rendered_card WHERE draft_id=? ORDER BY card_id",
                    (draft_id,),
                ).fetchall()
            }
            draft = ContentDraft(
                draft_id=str(row["draft_id"]), content_type=RagContentType(row["content_type"]),
                title=str(row["title"]), cards=cards, detail_body=str(row["body"]),
                detail_claims=detail_claims, evidence_ids=evidence_ids,
                menu_work_ids=tuple(json.loads(row["menu_work_ids_json"])),
                retrieval_id=str(row["retrieval_id"]), created_at=datetime.fromisoformat(row["created_at"]),
                source_footer=str(row["source_footer"]), ai_disclosure=str(row["ai_disclosure"]),
                retrieved_chunk_ids=tuple(json.loads(row["retrieved_chunk_ids_json"])),
                topic_id=row["topic_id"], topic_label=row["topic_label"], summary=row["summary"],
                keywords=tuple(json.loads(row["keyword_json"])), thumbnail_url=row["thumbnail_url"],
                is_sponsored=bool(row["is_sponsored"]), is_changed_notice=bool(row["is_changed_notice"]),
                ai_judgment_basis=json.loads(row["ai_judgment_basis_json"]),
                event_detail_id=row["event_detail_id"],
                published_at=datetime.fromisoformat(row["published_at"]) if row["published_at"] else None,
                valid_until=datetime.fromisoformat(row["valid_until"]) if row["valid_until"] else None,
                embedding=tuple(json.loads(row["embedding_json"])) if row["embedding_json"] else None,
                entity_links=entity_links, tags=tags,
            )
            return DraftReviewContext(draft, evidence, images, chunks, draft.retrieval_id, rendered_cards)

    def _load_claims(self, owner_id: str, *, detail: bool = False) -> tuple[SupportedClaim, ...]:
        if detail:
            rows = self.store._connection.execute(
                "SELECT * FROM rag_claim WHERE draft_id=? AND card_id IS NULL ORDER BY sequence_no", (owner_id,)
            ).fetchall()
        else:
            rows = self.store._connection.execute(
                "SELECT * FROM rag_claim WHERE card_id=? ORDER BY sequence_no", (owner_id,)
            ).fetchall()
        claims: list[SupportedClaim] = []
        for row in rows:
            links = self.store._connection.execute(
                "SELECT document_id,chunk_id FROM rag_claim_evidence WHERE claim_id=? ORDER BY document_id,chunk_id",
                (row["claim_id"],),
            ).fetchall()
            claims.append(SupportedClaim(
                text=str(row["claim_text"]),
                evidence_ids=tuple(dict.fromkeys(str(link["document_id"]) for link in links)),
                evidence_chunk_ids=tuple(dict.fromkeys(str(link["chunk_id"]) for link in links)),
                attribution=row["attribution"],
            ))
        return tuple(claims)

    async def set_status(self, draft_id: str, status: str) -> None:
        await asyncio.to_thread(self._set_status, draft_id, status)

    def _set_status(self, draft_id: str, status: str) -> None:
        if not status.strip():
            raise ValueError("Draft status cannot be empty")
        with self.store._transaction():
            cursor = self.store._connection.execute(
                "UPDATE rag_content_draft SET status=?,updated_at=? WHERE draft_id=?",
                (status, datetime.now(timezone.utc).isoformat(), draft_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(draft_id)


class SQLiteReviewHistoryRepository:
    def __init__(self, store: SQLiteRagStore) -> None:
        self.store = store

    async def save_result(self, result: ReviewResult, revision_count: int, reviewed_at: datetime) -> None:
        if revision_count < 0 or reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
            raise ValueError("Review history needs a non-negative revision count and aware timestamp")
        payload = {
            "draft_id": result.draft_id,
            "decision": result.decision.value,
            "issues": [asdict(issue) for issue in result.issues],
            "revision_instructions": list(result.revision_instructions),
            "evidence_requests": list(result.evidence_requests),
            "reviewer_id": result.reviewer_id,
            "scorecard": result.scorecard,
        }
        review_id = f"rv_{uuid.uuid4().hex}"
        await asyncio.to_thread(self._save, review_id, result, revision_count, reviewed_at, _json(payload))

    def _save(self, review_id: str, result: ReviewResult, revision_count: int, reviewed_at: datetime, payload: str) -> None:
        with self.store._transaction():
            self.store._connection.execute(
                """INSERT INTO rag_review_history(review_id,draft_id,reviewer_id,decision,revision_count,result_json,reviewed_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (review_id, result.draft_id, result.reviewer_id, result.decision.value,
                 revision_count, payload, reviewed_at.isoformat()),
            )
            self.store._connection.execute(
                """INSERT INTO rag_judgment_log(draft_id,agent_code,stage,attempt_no,input_summary,
                   judgment_result_json,verdict,judged_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (result.draft_id, result.reviewer_id, "review", revision_count, None,
                 payload, result.decision.value, reviewed_at.isoformat()),
            )


class SQLiteImageReader:
    """Reads explicitly registered image metadata; it never searches or fetches files."""

    def __init__(self, store: SQLiteRagStore) -> None:
        self.store = store

    async def get_existing_for_candidate(self, candidate: ImageCandidate) -> ImageAsset | None:
        """Reuse a previously downloaded exact URL and link it to the confirmed work."""
        return await asyncio.to_thread(self._get_existing_for_candidate, candidate)

    def _get_existing_for_candidate(self, candidate: ImageCandidate) -> ImageAsset | None:
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT * FROM rag_image_asset WHERE source_id=? AND source_url=?",
                (candidate.source_id, candidate.source_url),
            ).fetchone()
            if row is None or not row["local_path"] or not Path(str(row["local_path"])).is_file():
                return None
            with self.store._transaction():
                self.store._connection.execute(
                    """INSERT OR IGNORE INTO rag_image_work_link(image_work_link_id,asset_id,menu_work_id,
                       candidate_menu_work_id,status) VALUES(?,?,?,NULL,'matched')""",
                    (f"iw_{row['asset_id']}_{candidate.menu_work_id}", row["asset_id"], candidate.menu_work_id),
                )
                for link in candidate.entity_links:
                    self.store._connection.execute(
                        """INSERT OR IGNORE INTO rag_image_entity_link(
                             asset_id,entity_id,relation_type,source_document_id,source_chunk_id,status,created_at)
                           VALUES(?,?,?,?,?,?,?)""",
                        (row["asset_id"], link.entity_id, link.relation_type, link.source_document_id,
                         link.source_chunk_id, link.status, datetime.now(timezone.utc).isoformat()),
                    )
            asset = self._asset(row)
            work_ids = tuple(sorted(set((*asset.menu_work_ids, candidate.menu_work_id))))
            entity_links = {(link.entity_id, link.relation_type): link for link in asset.entity_links}
            entity_links.update({(link.entity_id, link.relation_type): link for link in candidate.entity_links})
            return replace(asset, menu_work_ids=work_ids, entity_links=tuple(entity_links.values()))

    async def register_asset(self, asset: ImageAsset) -> None:
        source = urlsplit(asset.source_url)
        if (not asset.asset_id.strip() or not asset.source_id.strip() or not asset.source_url.strip()
                or not asset.local_path.strip() or asset.work_link_status != "matched" or not asset.menu_work_ids):
            raise ValueError("Image registration requires a source URL, local file, and matched work")
        if source.scheme not in {"http", "https"} or not source.hostname or source.username or source.password:
            raise ValueError("Image source must use a credential-free absolute HTTP(S) URL")
        if any(work_id <= 0 for work_id in asset.menu_work_ids) or len(set(asset.menu_work_ids)) != len(asset.menu_work_ids):
            raise ValueError("Image work links must be unique positive snapshot IDs")
        if any(link.status != "matched" for link in asset.entity_links):
            raise ValueError("Only evidence-backed matched entities may be linked to usable images")
        if len({(link.entity_id, link.relation_type) for link in asset.entity_links}) != len(asset.entity_links):
            raise ValueError("Image/entity links must be unique by entity and relation")
        await asyncio.to_thread(self._register_asset, asset)

    def _register_asset(self, asset: ImageAsset) -> None:
        with self.store._transaction():
            self.store._connection.execute(
                """INSERT OR IGNORE INTO rag_image_asset(asset_id,source_id,source_url,source_page_url,source_native_id,
                   image_role,alt_text,local_path,content_sha256,media_type,byte_size,width,height,rights_basis,
                   rights_evidence_url,work_link_status,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (asset.asset_id, asset.source_id, asset.source_url, asset.source_page_url, asset.source_native_id,
                 asset.image_role, asset.alt_text, asset.local_path, asset.content_sha256, asset.media_type, asset.byte_size,
                 asset.width, asset.height, asset.rights_basis, asset.rights_evidence_url,
                 asset.work_link_status, datetime.now(timezone.utc).isoformat()),
            )
            for index, work_id in enumerate(asset.menu_work_ids):
                self.store._connection.execute(
                    """INSERT OR IGNORE INTO rag_image_work_link(image_work_link_id,asset_id,menu_work_id,
                       candidate_menu_work_id,status) VALUES(?,?,?,NULL,'matched')""",
                    (f"iw_{asset.asset_id}_{index}", asset.asset_id, work_id),
                )
            for link in asset.entity_links:
                self.store._connection.execute(
                    """INSERT OR IGNORE INTO rag_image_entity_link(
                         asset_id,entity_id,relation_type,source_document_id,source_chunk_id,status,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (asset.asset_id, link.entity_id, link.relation_type, link.source_document_id,
                     link.source_chunk_id, link.status, datetime.now(timezone.utc).isoformat()),
                )

    async def get_many(self, asset_ids: Sequence[str]) -> dict[str, ImageAsset]:
        return await asyncio.to_thread(self._get_many, asset_ids)

    def _get_many(self, asset_ids: Sequence[str]) -> dict[str, ImageAsset]:
        if not asset_ids:
            return {}
        marks = ",".join("?" for _ in asset_ids)
        with self.store._lock:
            rows = self.store._connection.execute(
                f"SELECT * FROM rag_image_asset WHERE asset_id IN ({marks})", tuple(asset_ids)
            ).fetchall()
            return {str(row["asset_id"]): self._asset(row) for row in rows}

    async def candidates_for(self, evidence: Sequence[EvidenceDocument]) -> Sequence[ImageAsset]:
        work_ids = tuple(sorted({work_id for item in evidence for work_id in item.menu_work_ids}))
        if not work_ids:
            return ()
        return await asyncio.to_thread(self._candidates_for, work_ids)

    def _candidates_for(self, work_ids: Sequence[int]) -> Sequence[ImageAsset]:
        marks = ",".join("?" for _ in work_ids)
        with self.store._lock:
            rows = self.store._connection.execute(
                f"""SELECT DISTINCT a.* FROM rag_image_asset a JOIN rag_image_work_link w ON w.asset_id=a.asset_id
                    WHERE w.status='matched' AND w.menu_work_id IN ({marks}) ORDER BY a.asset_id""",
                tuple(work_ids),
            ).fetchall()
            assets = tuple(self._asset(row) for row in rows)

        # Keep every downloaded Kitsu size on disk, but expose only the best
        # size for each underlying cover/character image to the copy selector.
        variant_rank = {"original": 0, "large": 1, "medium": 2, "small": 3, "tiny": 4}
        selected: dict[tuple[str, ...], tuple[tuple[int, int], ImageAsset]] = {}
        for asset in assets:
            parts = asset.image_role.rsplit(":", 1)
            if asset.source_id == "kitsu_anime_catalog" and len(parts) == 2 and parts[-1] in variant_rank:
                base_role, variant = parts
                key = (asset.source_id, asset.source_native_id or asset.asset_id, base_role)
                rank = (variant_rank[variant], -(asset.width or 0) * (asset.height or 0))
            else:
                key = (asset.asset_id,)
                rank = (0, 0)
            previous = selected.get(key)
            if previous is None or rank < previous[0]:
                selected[key] = (rank, asset)
        return tuple(sorted(
            (value[1] for value in selected.values()),
            key=lambda item: (item.source_id, item.image_role, item.alt_text or "", item.asset_id),
        ))

    def _asset(self, row: sqlite3.Row) -> ImageAsset:
        links = self.store._connection.execute(
            "SELECT menu_work_id FROM rag_image_work_link WHERE asset_id=? AND status='matched' ORDER BY menu_work_id",
            (row["asset_id"],),
        ).fetchall()
        entity_rows = self.store._connection.execute(
            """SELECT l.entity_id,l.relation_type,l.source_document_id,l.source_chunk_id,l.status,e.name
               FROM rag_image_entity_link l JOIN rag_entity e ON e.entity_id=l.entity_id
               WHERE l.asset_id=? AND l.status='matched' ORDER BY l.entity_id,l.relation_type""",
            (row["asset_id"],),
        ).fetchall()
        return ImageAsset(
            asset_id=str(row["asset_id"]), source_url=str(row["source_url"]), source_id=str(row["source_id"]),
            work_link_status=str(row["work_link_status"]),
            menu_work_ids=tuple(int(item["menu_work_id"]) for item in links if item["menu_work_id"] is not None),
            rights_basis=row["rights_basis"], rights_evidence_url=row["rights_evidence_url"],
            local_path=str(row["local_path"]),
            source_page_url=row["source_page_url"], source_native_id=row["source_native_id"],
            image_role=str(row["image_role"]), alt_text=row["alt_text"], content_sha256=row["content_sha256"],
            media_type=row["media_type"], byte_size=row["byte_size"], width=row["width"], height=row["height"],
            entity_links=tuple(ImageEntityLink(
                entity_id=int(item["entity_id"]), relation_type=str(item["relation_type"]),
                source_document_id=str(item["source_document_id"]), source_chunk_id=str(item["source_chunk_id"]),
                status=str(item["status"]), entity_name=str(item["name"]),
            ) for item in entity_rows),
        )
