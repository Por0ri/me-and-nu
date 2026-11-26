"""Shared retrieval behavior with a SQL-scored vector reader; no provider calls."""

import importlib.util
import math
import sqlite3
import struct
import sys
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.retrieval import RetrievalRequest, VectorEvidenceRetriever, chunk_document
from ani_rag.settings import load_source_registry
from ani_rag.storage.sqlite_core import SQLiteRagStore


class StoredQuery:
    def __init__(self):
        self.calls = 0
        self.vector = (1.0, 0.0) + (0.0,) * 1022

    async def embed_query(self, _text):
        self.calls += 1
        return self.vector


class ScoredIndex:
    """Stand-in for SQL 1 - cosine_distance, using existing stored vectors."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch_chunks(self, document_ids, query_vector):
        self.calls.append(document_ids)
        result = []
        norm = math.sqrt(sum(v * v for v in query_vector))
        for row in self.rows:
            if row["document_id"] not in document_ids:
                continue
            item = dict(row)
            values = struct.unpack("<1024f", item.pop("vector_f32"))
            other_norm = math.sqrt(sum(v * v for v in values))
            item["cosine_similarity"] = sum(a * b for a, b in zip(query_vector, values)) / (norm * other_norm)
            result.append(item)
        return result


class PgvectorRetrievalContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.db.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        self.store = SQLiteRagStore(self.db)
        sources = load_source_registry().approved_sources
        for source in sources:
            if source.source_id in {"anime_corner", "ann_encyclopedia"}:
                await self.store.register_source(source)
        self.docs = {}
        self.rows = []
        for key, work, source, cosine in (
            ("best", 1, "anime_corner", .9),
            ("pinned", 1, "anime_corner", .6),
            ("other-source", 1, "ann_encyclopedia", .95),
            ("other-work", 2, "anime_corner", .98),
            ("excluded", 1, "anime_corner", .99),
        ):
            document = EvidenceDocument(
                document_id=key, content_type=RagContentType.GUIDE, source_id=source,
                source_url=f"https://example.test/{key}", title=f"Studio profile {key}",
                body=f"Studio profile evidence for {key}.", retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                evidence_type=EvidenceType.REFERENCE_FACT, collection_method=CollectionMethod.MANUAL_IMPORT,
                language="en", work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=(work,),
            )
            await self.store.upsert_and_schedule_generation(document, schedule_generation=False)
            self.docs[key] = document
            chunk = chunk_document(document)[0]
            values = (cosine, math.sqrt(1 - cosine * cosine)) + (0.0,) * 1022
            self.rows.append({**vars(chunk), "document_id": key, "chunk_text": chunk.text,
                              "published_at": None, "content_type": chunk.content_type.value,
                              "vector_f32": struct.pack("<1024f", *values)})
        self.sqlite_index = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.sqlite_index.close)
        self.sqlite_index.executescript("""
            CREATE TABLE index_metadata(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO index_metadata VALUES('model','text-embedding-3-small'),('dimensions','1024'),('completed_at','2026-01-01');
            CREATE TABLE embedding_vector(text_hash TEXT PRIMARY KEY, vector_f32 BLOB);
            CREATE TABLE evidence_chunk(chunk_id TEXT,document_id TEXT,content_type TEXT,title TEXT,
                source_url TEXT,language TEXT,source_kind TEXT,source_index INTEGER,locator TEXT,
                start_offset INTEGER,end_offset INTEGER,chunk_text TEXT,source_id TEXT,published_at TEXT,text_hash TEXT);
        """)
        for row in self.rows:
            self.sqlite_index.execute("INSERT INTO embedding_vector VALUES(?,?)", (row["chunk_id"], row["vector_f32"]))
            fields = ("chunk_id", "document_id", "content_type", "title", "source_url", "language", "source_kind",
                      "source_index", "locator", "start_offset", "end_offset", "chunk_text", "source_id", "published_at")
            self.sqlite_index.execute("INSERT INTO evidence_chunk VALUES(" + ",".join("?" for _ in range(15)) + ")",
                                      tuple(row[field] for field in fields) + (row["chunk_id"],))
        self.sqlite_index.commit()
        self.sqlite_index.execute("PRAGMA query_only=ON")
        self.index = ScoredIndex(self.rows)
        self.query = StoredQuery()
        self.old = VectorEvidenceRetriever(self.store, self.sqlite_index, self.query)
        self.new = VectorEvidenceRetriever(self.store, self.index, self.query)
        self.request = RetrievalRequest(
            "studio profile", RagContentType.GUIDE, menu_work_ids=(1,), source_ids=("anime_corner",),
            exclude_source_urls=("https://example.test/excluded/?utm_source=candidate",), top_k=5, candidate_limit=10,
        )

    async def assert_parity(self, request):
        old, new = await self.old.retrieve(request), await self.new.retrieve(request)
        self.assertEqual([d.document_id for d in old.documents], [d.document_id for d in new.documents])
        self.assertEqual([replace(c, score=0) for c in old.chunks], [replace(c, score=0) for c in new.chunks])
        for left, right in zip(old.chunks, new.chunks, strict=True):
            self.assertAlmostEqual(left.score, right.score, places=7)
        self.assertEqual(old.backend, new.backend)
        return new

    async def test_order_filters_urls_and_ids_match_sqlite(self):
        result = await self.assert_parity(self.request)
        self.assertEqual([c.evidence_id for c in result.chunks], ["best", "pinned"])
        self.assertEqual(set(self.index.calls[-1]), {"best", "pinned"})
        for changed in (replace(self.request, source_ids=("ann_encyclopedia",)),
                        replace(self.request, menu_work_ids=(2,)),
                        replace(self.request, source_ids=(), menu_work_ids=()),
                        replace(self.request, min_retrieval_score=.8, top_k=1)):
            await self.assert_parity(changed)

    async def test_pinned_priority_and_stale_source_guard_match(self):
        pinned = next(r for r in self.rows if r["document_id"] == "pinned")["chunk_id"]
        request = replace(self.request, corroborating_chunks=(pinned,), top_k=1)
        self.assertEqual((await self.assert_parity(request)).chunks[0].evidence_id, "pinned")
        self.db.execute("UPDATE rag_document SET raw_text='changed source evidence' WHERE document_id='pinned'")
        self.assertEqual((await self.assert_parity(request)).chunks[0].evidence_id, "best")

    async def test_tied_scores_use_existing_document_order(self):
        def rows(document_ids, vector):
            output = ScoredIndex(self.rows).fetch_chunks(document_ids, vector)
            for item in output:
                item["cosine_similarity"] = .75
            return list(reversed(output))
        with patch.object(self.index, "fetch_chunks", side_effect=rows):
            result = await self.new.retrieve(self.request)
        self.assertEqual([c.evidence_id for c in result.chunks], ["best", "pinned"])

    async def test_zero_vector_sql_null_is_skipped_and_scores_keep_existing_bounds(self):
        rows = self.index.fetch_chunks(("best", "pinned"), self.query.vector)
        rows[0]["cosine_similarity"] = None
        rows[1]["cosine_similarity"] = 1.00000001
        with patch.object(self.index, "fetch_chunks", return_value=rows):
            result = await self.new.retrieve(self.request)
        self.assertEqual([c.evidence_id for c in result.chunks], ["pinned"])
        self.assertEqual(result.chunks[0].score, 1.0)

    async def test_empty_index_falls_back_without_changing_relational_filters(self):
        self.index.rows = []
        with patch.object(self.old, "_fetch_index_chunks", return_value=[]):
            result = await self.assert_parity(self.request)
        self.assertEqual(result.backend, "lexical")
        self.assertNotIn("excluded", [d.document_id for d in result.documents])

    async def test_no_candidates_avoids_embedding_and_index(self):
        result = await self.new.retrieve(replace(self.request, menu_work_ids=(999,)))
        self.assertFalse(result.chunks)
        self.assertEqual(self.query.calls, 0)
        self.assertFalse(self.index.calls)

    async def test_invalid_query_and_nonfinite_sql_score_are_rejected(self):
        for value in ((0.0,) * 1024, (float("nan"),) + (0.0,) * 1023, (1.0,)):
            self.query.vector = value
            with self.assertRaises(ValueError):
                await self.new.retrieve(self.request)
        self.query.vector = (1.0,) + (0.0,) * 1023
        rows = self.index.fetch_chunks(("best",), self.query.vector)
        rows[0]["cosine_similarity"] = float("nan")
        with patch.object(self.index, "fetch_chunks", return_value=rows), self.assertRaises(ValueError):
            await self.new.retrieve(self.request)

    async def test_runtime_accepts_external_index_without_opening_database(self):
        from ani_rag.runtime import _evidence_retriever
        retriever = _evidence_retriever(self.store, self.index, self.query)
        self.assertIsInstance(retriever, VectorEvidenceRetriever)
        self.assertFalse(self.index.calls)
        with self.assertRaises(ValueError):
            _evidence_retriever(self.store, self.index, None)


class PgvectorWorkerConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "scripts/run_worker.py"
        spec = importlib.util.spec_from_file_location("pgvector_worker_entrypoint_test", path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def parse(self, *args):
        return self.module._parser().parse_args(["--role", "generation", "--db", "unused.sqlite3", *args])

    def test_backends_are_exclusive_and_postgres_requires_expected_name(self):
        with self.assertRaises(SystemExit):
            self.parse("--vector-db", "old.sqlite3", "--vector-database-url-env", "RAG_TEST_DSN")
        for args in (("--vector-database-url-env", "RAG_TEST_DSN"),
                     ("--expected-vector-database", "menu_rag_vector_test")):
            with self.assertRaises(ValueError):
                self.module._validate_vector_options(self.parse(*args))
        options = self.parse("--vector-database-url-env", "RAG_TEST_DSN",
                             "--expected-vector-database", "menu_rag_vector_test")
        self.module._validate_vector_options(options)

    def test_connection_is_readonly_and_closed_after_setup_failure(self):
        connection, index = Mock(), object()
        connect, index_type = Mock(return_value=connection), Mock(return_value=index)
        module = SimpleNamespace(PostgresVectorIndex=index_type, connect_postgres=connect)
        options = self.parse("--vector-database-url-env", "RAG_TEST_DSN",
                             "--expected-vector-database", "menu_rag_vector_test")
        with patch.dict(sys.modules, {"ani_rag.storage.postgres_vector": module}), \
                patch.dict("os.environ", {"RAG_TEST_DSN": "dedicated-test-url"}):
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                with ExitStack() as stack:
                    self.assertIs(self.module._open_worker_vector(options, stack), index)
                    raise RuntimeError("setup failed")
        connect.assert_called_once_with("dedicated-test-url", expected_database="menu_rag_vector_test", read_only=True)
        connection.close.assert_called_once()

    def test_empty_environment_has_no_connection_side_effect(self):
        connect = Mock()
        module = SimpleNamespace(PostgresVectorIndex=Mock(), connect_postgres=connect)
        options = self.parse("--vector-database-url-env", "RAG_TEST_DSN",
                             "--expected-vector-database", "menu_rag_vector_test")
        with patch.dict(sys.modules, {"ani_rag.storage.postgres_vector": module}), patch.dict("os.environ", {}, clear=True):
            with ExitStack() as stack, self.assertRaisesRegex(RuntimeError, "environment variable is empty"):
                self.module._open_worker_vector(options, stack)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
