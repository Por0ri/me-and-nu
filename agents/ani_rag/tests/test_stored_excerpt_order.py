"""Source order survives a SQLite round trip and either historical ID scheme."""

import hashlib
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceExcerpt, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.retrieval import RetrievalRequest
from ani_rag.settings import load_source_registry
from ani_rag.storage.excerpt_order import ExcerptOrderError, ordered_excerpt_rows
from ani_rag.storage.sqlite_core import SQLiteRagStore


DOC = "ev_d8db666bd279293851c0aceef381b119"


def stored_id(prefix, index, document_id=DOC):
    return prefix + "_" + hashlib.sha256(f"{document_id}:{index}".encode()).hexdigest()[:28]


class ExcerptOrderTests(unittest.TestCase):
    def test_restores_ordinals_with_either_id_scheme(self):
        self.assertEqual(ordered_excerpt_rows(DOC, []), [])
        for prefix in ("ex", "mde"):
            original = [{"excerpt_id": stored_id(prefix, i)} for i in range(3)]
            with self.subTest(prefix=prefix):
                self.assertEqual(ordered_excerpt_rows(DOC, original[:1]), original[:1])
                self.assertEqual(ordered_excerpt_rows(DOC, reversed(original)), original)

    def test_rejects_gaps_duplicates_foreign_or_unknown_ids(self):
        first, second, third = (stored_id("ex", i) for i in range(3))
        for ids in ((first, third), (first, first), (second,), (first, stored_id("mde", 1)),
                    (first, stored_id("ex", 1, "different-document")), ("unknown-id",)):
            with self.subTest(ids=ids), self.assertRaises(ExcerptOrderError):
                ordered_excerpt_rows(DOC, ({"excerpt_id": value} for value in ids))


class SQLiteExcerptOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_hash_and_claim_indices_survive_round_trip(self):
        db = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(db.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        db.executescript(schema.read_text(encoding="utf-8"))
        store = SQLiteRagStore(db)
        source = next(s for s in load_source_registry().approved_sources if s.source_id == "ann_encyclopedia")
        await store.register_source(source)
        document = EvidenceDocument(
            document_id=DOC, content_type=RagContentType.GUIDE, source_id=source.source_id,
            source_url="https://example.test/evidence", title="Work credits", body="Preserved source.",
            retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc), language="en",
            evidence_type=EvidenceType.REFERENCE_FACT, collection_method=CollectionMethod.MCP_READ,
            work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=(11,), claims=("A", "B"),
            excerpts=(EvidenceExcerpt("First excerpt", "first", 0), EvidenceExcerpt("Second excerpt", "second", 1)),
        )
        await store.upsert_and_schedule_generation(document, schedule_generation=False)
        lexical = [r[0] for r in db.execute("SELECT supports_claim_index FROM rag_excerpt ORDER BY excerpt_id")]
        self.assertEqual(lexical, [1, 0])
        db.execute("PRAGMA reverse_unordered_selects=ON")
        request = RetrievalRequest("Work credits", RagContentType.GUIDE, menu_work_ids=(11,),
                                   source_ids=(source.source_id,), seed_evidence_ids=(DOC,))
        stored_hash = db.execute("SELECT content_hash FROM rag_document").fetchone()[0]
        for prefix in ("ex", "mde"):
            if prefix == "mde":
                db.execute("UPDATE rag_excerpt SET excerpt_id='mde_' || substr(excerpt_id,4)")
            with self.subTest(prefix=prefix):
                loaded, = await store.fetch_candidates(request)
                self.assertEqual(loaded.excerpts, document.excerpts)
                self.assertEqual(loaded.content_hash, stored_hash)
        db.execute("DELETE FROM rag_excerpt WHERE supports_claim_index=0")
        with self.assertRaises(ExcerptOrderError):
            await store.fetch_candidates(request)
