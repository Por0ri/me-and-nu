from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ani_rag.agents.mcp_crawler.agent import MCPCrawlerAgent, RecurringCollectionPlan
from ani_rag.collector import CollectionRequest, CollectionResult
from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.queues import QueueItem, QueueName, QueueStatus, WebPipelineBacklog
from ani_rag.settings import SourceStatus, load_source_registry
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.structured_mcp import StructuredCollectionRequest
from ani_rag.web_discovery import (
    WebCandidateAssessmentWorker, WebCandidatePage, WebDiscoveryWorker, WebSearchPage,
)
from ani_rag.workers import CollectionWorker


class WebBackpressureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.connection.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        self.connection.executescript(schema.read_text(encoding="utf-8"))
        self.store = SQLiteRagStore(self.connection)
        self.now = datetime.now(timezone.utc)
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ANI_RAG_WEB_DISCOVERY_BACKLOG_LIMIT", None)

    async def add(self, job_id, queue, *, handler=None, key=None, payload=None, **times):
        return await self.store.enqueue(QueueItem(
            job_id, queue, handler_kind=handler, dedupe_key=key, payload=payload, **times,
        ))

    async def add_character_reference(self):
        source = next(s for s in load_source_registry().approved_sources if s.source_id == "ann_encyclopedia")
        await self.store.register_source(source)
        await self.store.upsert_and_schedule_generation(EvidenceDocument(
            document_id="profile", title="One Punch Man", source_id=source.source_id,
            source_url="https://example.org/reference", content_type=RagContentType.CHARACTER,
            language="en", body="Saitama is a character in One Punch Man.", retrieved_at=self.now,
            collection_method=CollectionMethod.MCP_READ, evidence_type=EvidenceType.REFERENCE_FACT,
            work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=(7,),
        ), schedule_generation=False)

    async def cancel(self, *job_ids):
        for job_id in job_ids:
            job = await self.store.get(job_id)
            await self.store.save(job.transition(QueueStatus.CANCELLED, now=self.now))

    async def test_search_stops_at_four_stays_paused_at_three_and_restarts_at_two(self):
        await self.add_character_reference()
        search = SimpleNamespace(search_web=AsyncMock(return_value=()))
        worker = WebDiscoveryWorker(self.store, search)
        await self.add("search", QueueName.COLLECTION, handler="web_discovery", payload={
            "query": "One Punch Man", "content_type": "character", "menu_work_id": 7,
        })
        await self.add("candidate", QueueName.COLLECTION, handler="web_candidate_assessment")
        await self.add("generation", QueueName.GENERATION)
        await self.add("review", QueueName.REVIEW)
        await self.add("review2", QueueName.REVIEW)
        self.assertFalse(await worker.run_once())
        await self.cancel("review2")
        self.assertFalse(await worker.run_once())
        restarted = WebDiscoveryWorker(self.store, search)
        self.assertFalse(await restarted.run_once())
        self.assertEqual((await self.store.get("search")).status, QueueStatus.PENDING)
        search.search_web.assert_not_awaited()
        await self.cancel("review")
        self.assertTrue(await restarted.run_once())
        search.search_web.assert_awaited_once()
        await self.add("review3", QueueName.REVIEW)
        self.assertFalse(await restarted.search_gate.search_is_paused())

    async def test_candidate_drain_continues_until_two_active_revisions(self):
        await self.add_character_reference()
        async def read(hit):
            return WebCandidatePage(hit, hit.title, hit.url, "A profile of Saitama", "en", None, self.now)

        adapter = SimpleNamespace(read_web_candidate=AsyncMock(side_effect=read))
        catalog = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(status=WorkLinkStatus.UNRESOLVED)))
        worker = WebCandidateAssessmentWorker(self.store, adapter, catalog, None, None)
        await self.add("candidate", QueueName.COLLECTION, handler="web_candidate_assessment", payload={
            "candidate": {"title": "One Punch Man profile", "url": "https://example.org/features/saitama"},
            "content_type": "character", "menu_work_id": 7,
        })
        await self.add("rev1", QueueName.GENERATION, key="revision:old:1")
        await self.add("rev2", QueueName.GENERATION, payload={"revision_count": 1})
        await self.add("review", QueueName.REVIEW)
        self.assertFalse(await worker.run_once())
        adapter.read_web_candidate.assert_not_awaited()
        self.assertEqual((await self.store.get("candidate")).status, QueueStatus.PENDING)
        await self.cancel("rev2")
        await self.add("new", QueueName.GENERATION)
        self.assertEqual(await self.store.web_pipeline_backlog(), WebPipelineBacklog(4, 1))
        self.assertTrue(await worker.run_once())
        adapter.read_web_candidate.assert_awaited_once()

    async def test_old_draft_status_does_not_keep_search_paused(self):
        with self.connection:
            self.connection.execute(
                """INSERT INTO rag_content_draft(draft_id,content_type,title,body,source_footer,
                   ai_disclosure,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                ("old", "guide", "title", "body", "source", "AI", "revision_requested",
                 self.now.isoformat(), self.now.isoformat()),
            )
        await self.add("old-job", QueueName.GENERATION, key="revision:old:1", status=QueueStatus.SUCCEEDED)
        self.assertEqual(await self.store.web_pipeline_backlog(), WebPipelineBacklog(0, 0))

    async def test_claim_prioritises_due_revisions_and_preserves_fifo_within_priority(self):
        await self.add("new", QueueName.GENERATION, created_at=self.now - timedelta(minutes=5))
        await self.add("retry-not-due", QueueName.GENERATION, key="revision:future:1",
                       status=QueueStatus.RETRY_WAIT, updated_at=self.now + timedelta(hours=1))
        await self.add("rev-old", QueueName.GENERATION, key="revision:a:1", created_at=self.now - timedelta(minutes=2))
        await self.add("rev-new", QueueName.GENERATION, payload={"revision_count": 1}, created_at=self.now)
        claimed = [await self.store.claim(QueueName.GENERATION, now=self.now) for _ in range(4)]
        self.assertEqual([j.job_id if j else None for j in claimed], ["rev-old", "rev-new", "new", None])

    async def test_pre_boot_running_jobs_do_not_count_but_old_pending_and_live_running_do(self):
        self.store._local_boot_at = (self.now - timedelta(days=1)).isoformat()
        old = self.now - timedelta(days=3)
        await self.add("old-running", QueueName.GENERATION, key="revision:lost:1", created_at=old)
        await self.store.claim(QueueName.GENERATION, now=old)
        await self.add("old-pending", QueueName.GENERATION, created_at=old)
        await self.add("live-running", QueueName.GENERATION, key="revision:live:1")
        await self.store.claim(QueueName.GENERATION, now=self.now)
        self.assertEqual(await self.store.web_pipeline_backlog(), WebPipelineBacklog(2, 1))
        self.assertEqual((await self.store.get("old-running")).status, QueueStatus.RUNNING)

    async def test_pause_keeps_queued_and_due_discovery_but_runs_evidence_and_structured_workers(self):
        source = SimpleNamespace(
            source_id="facts", status=SourceStatus.APPROVED, is_collecting=True,
            content_types=(RagContentType.CHARACTER,), allowed_tools=frozenset({"search", "read"}),
        )
        url_worker = CollectionWorker(self.store, SimpleNamespace(sources=(source,)), None, None)
        search = WebDiscoveryWorker(self.store, SimpleNamespace(search_web_page=AsyncMock(return_value=WebSearchPage(()))))
        structured = SimpleNamespace(run_once=AsyncMock(return_value=False))
        request = CollectionRequest("facts", RagContentType.CHARACTER, EvidenceType.REFERENCE_FACT, "Saitama", "en")
        structured_request = StructuredCollectionRequest(
            "facts", RagContentType.CHARACTER, EvidenceType.REFERENCE_FACT, "Saitama", "en", ("P1441",),
            seed_menu_work_id=7,
        )
        agent = MCPCrawlerAgent(
            url_worker=url_worker, web_discovery_worker=search, structured_worker=structured,
            recurring_plans=(
                RecurringCollectionPlan("due-facts", request, 86400),
                RecurringCollectionPlan("due-structured", structured_request, 86400),
            ),
        )
        payload = dict(source_id="facts", query="Saitama", language="en",
                       content_type="character", evidence_type="reference_fact")
        await self.add("recurring", QueueName.COLLECTION, handler="url", key="recurring_collection:old:1",
                       payload=payload, created_at=self.now - timedelta(hours=1))
        await self.add("targeted", QueueName.COLLECTION, handler="url", payload=payload)
        await self.add("rev1", QueueName.GENERATION, key="revision:a:1")
        await self.add("rev2", QueueName.GENERATION, key="revision:b:1")
        with patch("ani_rag.workers.collect_from_source", new=AsyncMock(return_value=CollectionResult("facts", 0, ()))) as collect:
            self.assertTrue(await agent.run_once())
            collect.assert_awaited_once()
        search.adapter.search_web_page.assert_not_awaited()
        structured.run_once.assert_awaited_once()
        self.assertEqual((await self.store.get("targeted")).status, QueueStatus.SUCCEEDED)
        self.assertEqual((await self.store.get("recurring")).status, QueueStatus.PENDING)
        self.assertEqual(await self.store.count_active(QueueName.COLLECTION, handler_kind="url"), 1)
        self.assertEqual(await self.store.count_active(QueueName.COLLECTION, handler_kind="structured"), 1)
