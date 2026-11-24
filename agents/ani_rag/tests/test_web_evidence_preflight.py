import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.queues import QueueItem, QueueName
from ani_rag.retrieval import DatabaseEvidenceRetriever, RetrievalRequest
from ani_rag.settings import load_source_registry
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.web_discovery import WebCandidateAssessmentWorker, WebDiscoveryRequest, WebDiscoveryWorker, enqueue_web_discovery


class WebEvidencePreflightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.connection.close)
        schema = Path(__file__).resolve().parents[1] / "src" / "ani_rag" / "storage" / "schema.sql"
        self.connection.executescript(schema.read_text(encoding="utf-8"))
        self.store = SQLiteRagStore(self.connection)
        self.registry = load_source_registry()
        self.now = datetime.now(timezone.utc)

    async def add_evidence(self, source_id, content_type, evidence_type, work, key, **metadata):
        source = next(s for s in self.registry.sources if s.source_id == source_id)
        await self.store.register_source(source)
        document = EvidenceDocument(
            document_id=key, title="Work interview", source_url=f"https://example.org/{key}",
            source_id=source_id, content_type=content_type, evidence_type=evidence_type,
            body="Work interview with theme composer", language="en", collection_method=CollectionMethod.MCP_READ,
            retrieved_at=self.now, work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=(work,), **metadata,
        )
        await self.store.upsert_and_schedule_generation(document, schedule_generation=False)
        return document

    async def test_web_search_skips_only_empty_lane_then_resumes_after_reference_collection(self):
        music, review = RagContentType.MUSIC, RagContentType.REVIEW
        await self.add_evidence("flyingdog_music", music, EvidenceType.OFFICIAL_FACT, 7, "music")
        await self.add_evidence("anime_corner", review, EvidenceType.EDITORIAL_REVIEW, 8, "other",
                                claims=("An attributed observation",), author_name="Lee", published_at=self.now)
        adapter = SimpleNamespace(search_web=AsyncMock(return_value=()))
        worker = WebDiscoveryWorker(self.store, adapter, registry=self.registry)
        for lane, query in [(review, "first review"), (music, "music interview")]:
            await enqueue_web_discovery(self.store, WebDiscoveryRequest(
                query=query, content_type=lane, menu_work_id=7,
            ))
            self.assertTrue(await worker.run_once())
        adapter.search_web.assert_awaited_once_with("music interview", limit=5)
        await self.add_evidence("anime_corner", review, EvidenceType.EDITORIAL_REVIEW, 7, "review",
                                claims=("An attributed observation",), author_name="Lee", published_at=self.now)
        await enqueue_web_discovery(self.store, WebDiscoveryRequest(query="next review", content_type=review, menu_work_id=7))
        self.assertTrue(await worker.run_once())
        self.assertEqual(adapter.search_web.await_args.args, ("next review",))

    async def test_stored_candidate_without_usable_evidence_never_triggers_a_paid_read(self):
        await self.add_evidence("anime_corner", RagContentType.REVIEW, EvidenceType.EDITORIAL_REVIEW, 7, "undated",
                                claims=("A claim",), author_name="Lee")
        await self.store.enqueue(QueueItem("old", QueueName.COLLECTION, handler_kind="web_candidate_assessment", payload={
            "content_type": "review", "menu_work_id": 7, "candidate": {"title": "Work interview", "url": "https://example.org/article"},
        }))
        adapter = SimpleNamespace(read_web_candidate=AsyncMock())
        worker = WebCandidateAssessmentWorker(self.store, adapter, None, None, None, registry=self.registry)
        self.assertTrue(await worker.run_once())
        adapter.read_web_candidate.assert_not_awaited()
        self.assertEqual((await self.store.get("old")).last_error, "candidate_no_work_evidence")

    async def test_reference_version_ignores_other_works_and_self_evidence_is_not_retrieved(self):
        lane = RagContentType.MUSIC
        sources = self.registry.reference_source_ids(lane)
        self.assertIn("flyingdog_music", sources)
        self.assertIn("wikidata", sources)
        self.assertNotIn("anime_corner", sources)
        self.assertIn("anilist", self.registry.reference_source_ids(RagContentType.CURATION))
        self.assertNotIn("frieren_official", self.registry.reference_source_ids(RagContentType.CHARACTER))
        page = await self.add_evidence("flyingdog_music", lane, EvidenceType.OFFICIAL_FACT, 7, "page")
        version = await self.store.web_evidence_revision(7, lane, sources)
        self.assertIsNotNone(version)
        await self.add_evidence("flyingdog_music", lane, EvidenceType.OFFICIAL_FACT, 8, "other")
        self.assertEqual(version, await self.store.web_evidence_revision(7, lane, sources))
        request = RetrievalRequest(query="theme composer", content_type=lane, menu_work_ids=(7,), source_ids=sources,
                                   exclude_source_urls=(page.source_url + "?utm_source=feed",))
        retriever = DatabaseEvidenceRetriever(self.store)
        self.assertFalse((await retriever.retrieve(request)).chunks)
        await self.add_evidence("flyingdog_music", lane, EvidenceType.OFFICIAL_FACT, 7, "second")
        self.assertNotEqual(version, await self.store.web_evidence_revision(7, lane, sources))
        self.assertEqual([doc.document_id for doc in (await retriever.retrieve(request)).documents], ["second"])

    async def test_other_work_and_ambiguous_seeds_leave_room_for_matched_evidence(self):
        lane = RagContentType.MUSIC
        source = "flyingdog_music"
        for work, key in [(8, "other"), (7, "unconfirmed"), (9, "right")]:
            await self.add_evidence(source, lane, EvidenceType.OFFICIAL_FACT, work, key)
        with self.connection:
            self.connection.execute(
                "UPDATE rag_work_link SET status='ambiguous',candidate_menu_work_id=menu_work_id,menu_work_id=NULL "
                "WHERE document_id='unconfirmed'"
            )
        request = RetrievalRequest(
            query="theme composer", content_type=lane, menu_work_ids=(7, 9), source_ids=(source,),
            seed_evidence_ids=("other", "unconfirmed"), top_k=1, candidate_limit=1,
        )
        self.assertEqual([d.document_id for d in await self.store.fetch_candidates(request)], ["right"])
        scoped_seed = replace(request, query="unrelated terms", seed_evidence_ids=("right",))
        self.assertEqual([d.document_id for d in await self.store.fetch_candidates(scoped_seed)], ["right"])
        unscoped_seed = replace(scoped_seed, menu_work_ids=(), seed_evidence_ids=("other",))
        self.assertEqual([d.document_id for d in await self.store.fetch_candidates(unscoped_seed)], ["other"])


if __name__ == "__main__":
    unittest.main()
