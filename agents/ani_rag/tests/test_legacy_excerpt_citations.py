"""Historical vector references must still cite the same stored passage after reindexing."""

import hashlib
import sqlite3
import struct
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from ani_rag.content import ContentDraft, SupportedClaim
from ani_rag.excerpt_citations import cited_excerpt_index
from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceExcerpt, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.quality import validate_draft
from ani_rag.retrieval import RetrievalRequest, VectorEvidenceRetriever, chunk_document
from ani_rag.settings import load_source_registry
from ani_rag.storage.excerpt_links import stored_excerpt_id
from ani_rag.storage.sqlite_core import SQLiteRagStore


DOC = "ev_d8db666bd279293851c0aceef381b119"  # lexicographically, excerpt 1 comes before 0


def vector_row(chunk):
    return {**vars(chunk), "document_id": chunk.evidence_id, "chunk_text": chunk.text,
            "content_type": chunk.content_type.value, "text_hash": hashlib.sha256(chunk.text.encode()).hexdigest()}


class LegacyExcerptCitationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.db.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        self.store = SQLiteRagStore(self.db)
        source = next(s for s in load_source_registry().approved_sources if s.source_id == "ann_encyclopedia")
        await self.store.register_source(source)
        self.document = EvidenceDocument(
            document_id=DOC, title="Studio booklet", source_url="https://example.test/booklet", source_id=source.source_id,
            content_type=RagContentType.GUIDE, evidence_type=EvidenceType.REFERENCE_FACT,
            collection_method=CollectionMethod.MCP_READ, language="en", retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=(11,),
            excerpts=(EvidenceExcerpt("The first passage.", "page:1"), EvidenceExcerpt("An unlabelled passage.")),
        )
        await self.store.upsert_and_schedule_generation(self.document, schedule_generation=False)
        self.current = chunk_document(self.document)
        self.old = chunk_document(replace(self.document, excerpts=self.document.excerpts[::-1]))
        self.request = RetrievalRequest("Studio booklet", RagContentType.GUIDE, menu_work_ids=(11,), source_ids=(source.source_id,))
        self.index = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.index.close)
        self.index.executescript("""
            CREATE TABLE index_metadata(key PRIMARY KEY, value);
            INSERT INTO index_metadata VALUES ('model','text-embedding-3-small'),('dimensions','1024'),('completed_at','fixture');
            CREATE TABLE embedding_vector(text_hash PRIMARY KEY, vector_f32);
            CREATE TABLE evidence_chunk(chunk_id PRIMARY KEY, document_id, content_type, title, source_url,
                language, source_kind, source_index, locator, start_offset, end_offset, chunk_text,
                source_id, published_at, text_hash);
        """)
        embedder = AsyncMock()
        embedder.embed_query.return_value = (1.0,) + (0.0,) * 1023
        self.retriever = VectorEvidenceRetriever(self.store, self.index, embedder)

    def load_sidecar(self, rows):
        self.index.execute("PRAGMA query_only=OFF")
        with self.index:
            self.index.execute("DELETE FROM evidence_chunk")
            vector = struct.pack("<1024f", 1.0, *([0.0] * 1023))
            for row in rows:
                self.index.execute("INSERT OR IGNORE INTO embedding_vector VALUES(?,?)", (row["text_hash"], vector))
                self.index.execute("""INSERT INTO evidence_chunk VALUES(
                    :chunk_id,:document_id,:content_type,:title,:source_url,:language,:source_kind,:source_index,
                    :locator,:start_offset,:end_offset,:chunk_text,:source_id,:published_at,:text_hash)""", row)
        self.index.execute("PRAGMA query_only=ON")

    async def test_pinned_citation_survives_old_rebuilt_mixed_and_empty_indexes(self):
        for previous, current in zip(self.old, reversed(self.current), strict=True):
            self.assertEqual(previous.text, current.text)
            self.assertNotEqual(previous.chunk_id, current.chunk_id)
            for index_age, rows in (("old", self.old), ("rebuilt", self.current),
                                    ("mixed", (*self.old, *self.current)), ("empty", ())):
                with self.subTest(passage=current.locator, index=index_age):
                    self.load_sidecar([vector_row(c) for c in rows])
                    result = await self.retriever.retrieve(replace(self.request, corroborating_chunks=(previous.chunk_id,)))
                    self.assertEqual(result.backend, "vector_cosine" if rows else "lexical")
                    self.assertEqual([chunk.text for chunk in result.chunks].count(previous.text), 1)
                    self.assertEqual(len(result.chunks), 2)
                    self.assertEqual(replace(result.chunks[0], score=0.0), previous)

    async def test_current_pin_also_resolves_against_the_old_index(self):
        self.load_sidecar([vector_row(c) for c in self.old])
        current = self.current[0]
        result = await self.retriever.retrieve(replace(self.request, corroborating_chunks=(current.chunk_id,)))
        self.assertEqual(replace(result.chunks[0], score=0.0), current)

    async def test_changed_row_cannot_steal_pin_or_be_returned_as_vector_evidence(self):
        cited, other = self.old
        legitimate = vector_row(other)
        for tamper in ({"chunk_text": "Not from the source."}, {"source_url": "https://unrelated.test/"},
                       {"source_index": 14}, {"locator": "page:7:0-22"}, {"chunk_id": "wrong-hash"}):
            with self.subTest(tamper=tamper):
                self.load_sidecar([legitimate, {**vector_row(cited), **tamper}])
                result = await self.retriever.retrieve(replace(self.request, corroborating_chunks=(cited.chunk_id,)))
                self.assertEqual(result.backend, "vector_cosine")
                self.assertEqual([c.chunk_id for c in result.chunks], [other.chunk_id])

    def test_both_formats_link_to_the_same_persisted_excerpt_and_pass_review_origin_check(self):
        for chunks in (self.old, self.current):
            for chunk in chunks:
                original = next(i for i, e in enumerate(self.document.excerpts) if e.text == chunk.text)
                fields = (chunk.source_index, chunk.locator, chunk.start_offset, chunk.end_offset, chunk.text)
                self.assertEqual(cited_excerpt_index(DOC, self.document.excerpts, *fields), original)
                expected_id = "ex_" + hashlib.sha256(f"{DOC}:{original}".encode()).hexdigest()[:28]
                self.assertEqual(stored_excerpt_id(self.db, DOC, *fields), expected_id)
                draft = ContentDraft(
                    draft_id="review", content_type=RagContentType.GUIDE, title="Studio booklet", cards=(),
                    detail_body="Article", detail_claims=(SupportedClaim(chunk.text, (DOC,), (chunk.chunk_id,)),),
                    evidence_ids=(DOC,), menu_work_ids=(11,), retrieval_id="lookup", created_at=self.document.retrieved_at,
                    source_footer=self.document.source_url, retrieved_chunk_ids=(chunk.chunk_id,),
                )
                for changed, should_fail in ((chunk, False), (replace(chunk, locator="bad locator"), True)):
                    issues = validate_draft(draft, {DOC: self.document}, {}, {chunk.chunk_id: changed})
                    self.assertEqual("RETRIEVED_CHUNK_ORIGIN_MISMATCH" in {i.code for i in issues}, should_fail)

    def test_indistinguishable_passages_have_no_citation_target(self):
        repeated = EvidenceExcerpt("same", "same-locator")
        self.assertIsNone(cited_excerpt_index(DOC, (repeated, repeated), 0, "same-locator:0-4", 0, 4, "same"))
        self.db.execute("UPDATE rag_excerpt SET locator=?, excerpt_text=?", (repeated.locator, repeated.text))
        self.assertIsNone(stored_excerpt_id(self.db, DOC, 0, "same-locator:0-4", 0, 4, "same"))
