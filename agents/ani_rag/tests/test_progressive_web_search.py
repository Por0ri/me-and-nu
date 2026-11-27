"""Exercise the real queue with scripted search results, without provider calls."""

import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ani_rag.models import RagContentType
from ani_rag.queues import QueueName, QueueStatus
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.web_discovery import (
    WebCandidateHit, WebDiscoveryRequest, WebDiscoveryWorker, WebSearchPage, enqueue_web_discovery,
)


class ProgressiveSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.db.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        self.db.executescript(schema.read_text(encoding="utf-8"))
        self.store = SQLiteRagStore(self.db)
        self.store.web_evidence_revision = AsyncMock(return_value="fixture-evidence")
        self.search = AsyncMock()
        self.adapter = SimpleNamespace(search_web_page=self.search)
        self.worker = WebDiscoveryWorker(self.store, self.adapter)
        self.request = WebDiscoveryRequest("first", RagContentType.PEOPLE, 7, ("second", "third"))
        self.hit = WebCandidateHit("Series director interview", "https://example.org/interviews/director")

    async def claim_candidate(self):
        item = await self.store.claim(
            QueueName.COLLECTION, now=datetime.now(timezone.utc), handler_kind="web_candidate_assessment",
        )
        self.assertIsNotNone(item)
        return item

    async def save_status(self, job, status):
        await self.store.save(job.transition(status, now=datetime.now(timezone.utc)))

    def queries(self):
        return [(call.args[0], call.kwargs["cursor"]) for call in self.search.await_args_list]

    async def test_wait_then_fallback_including_cursor_and_later_success(self):
        self.search.side_effect = [
            WebSearchPage((self.hit,), "page-two"), WebSearchPage(()),
            WebSearchPage((WebCandidateHit("Another interview", "https://example.org/interviews/second"),)),
        ]
        await enqueue_web_discovery(self.store, self.request)
        self.assertTrue(await self.worker.run_once())
        self.assertFalse(await self.worker.run_once())
        candidate = await self.claim_candidate()
        await self.save_status(candidate, QueueStatus.RETRY_WAIT)
        self.assertFalse(await self.worker.run_once())
        candidate = await self.claim_candidate()
        self.assertFalse(await self.worker.run_once())
        await self.save_status(candidate, QueueStatus.BLOCKED)
        # Another worker instance resumes the saved cursor before trying the second alias.
        worker = WebDiscoveryWorker(self.store, self.adapter)
        self.assertTrue(await worker.run_once())
        self.assertTrue(await worker.run_once())
        candidate = await self.claim_candidate()
        await self.save_status(candidate, QueueStatus.SUCCEEDED)
        self.assertTrue(await worker.run_once())
        self.assertFalse(await worker.run_once())
        self.assertEqual(self.queries(), [("first", None), ("first", "page-two"), ("second", None)])
        skipped = self.db.execute("""SELECT COUNT(*) FROM rag_queue_job
            WHERE json_extract(payload_json,'$.search_skipped_reason')='candidate_sent_to_generation'""").fetchone()
        self.assertEqual(skipped[0], 1)

    async def test_concurrent_occurrences_wait_on_the_same_inflight_url(self):
        self.search.return_value = WebSearchPage((self.hit, self.hit))
        for run_id in ("one", "two"):
            await enqueue_web_discovery(self.store, self.request, run_id=run_id)
            self.assertTrue(await self.worker.run_once())
        candidate = await self.claim_candidate()
        self.assertFalse(await self.worker.run_once())
        await self.save_status(candidate, QueueStatus.SUCCEEDED)
        self.assertTrue(await self.worker.run_once())
        self.assertTrue(await self.worker.run_once())
        self.assertFalse(await self.worker.run_once())
        self.assertEqual(self.queries(), [("first", None), ("first", None)])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rag_queue_job "
                                         "WHERE handler_kind='web_candidate_assessment'").fetchone()[0], 1)

    async def test_failed_page_commit_does_not_orphan_candidate_or_cursor(self):
        self.search.return_value = WebSearchPage((self.hit,))
        job = await enqueue_web_discovery(self.store, self.request)
        original = self.store._save_queue_item_in_transaction
        def fail_completion(item):
            if item.status is QueueStatus.SUCCEEDED:
                raise sqlite3.OperationalError("simulated disk failure")
            original(item)
        with patch.object(self.store, "_save_queue_item_in_transaction", side_effect=fail_completion):
            self.assertTrue(await self.worker.run_once())
        self.assertEqual((await self.store.get(job.job_id)).status, QueueStatus.RETRY_WAIT)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rag_queue_job").fetchone()[0], 1)
        self.assertTrue(await self.worker.run_once())
        self.assertFalse(await self.worker.run_once())
        self.assertIsNotNone(await self.claim_candidate())
