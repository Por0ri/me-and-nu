import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from scripts.hold_relinked_drafts import audit_and_hold
from ani_rag.content import AI_DISCLOSURE
from ani_rag.queues import QueueItem, QueueName
from ani_rag.review import ReviewDecision, ReviewResult
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.storage.sqlite_workflow import SQLiteDraftRepository, SQLiteReviewHistoryRepository
from ani_rag.workers import ReviewWorker


class HoldRelinkedDraftTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.original = Path(temp.name) / "original.sqlite3"
        self.corrected = Path(temp.name) / "corrected.sqlite3"
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        with sqlite3.connect(self.original) as db:
            db.executescript(schema.read_text(encoding="utf-8"))
            db.execute("PRAGMA foreign_keys=OFF")
            for draft_id, state, metadata in (
                ("a", "ready_for_downstream", {"evidence_ids": ["d"]}),
                ("b", "pending_review", {"web_discovery_candidate": {"corroborating_document_ids": ["d"]}}),
                ("c", "ready_for_downstream", {"retrieved_chunk_ids": ["d"]}),
                ("r", "rejected_archived", {"evidence_ids": ["d"]}),
            ):
                db.execute("""INSERT INTO rag_content_draft(draft_id,content_type,title,body,
                    source_footer,ai_disclosure,menu_work_ids_json,status,ai_judgment_basis_json,
                    created_at,updated_at) VALUES(?,'guide','title','body','','','[1]',?,?,'old','old')""",
                           (draft_id, state, json.dumps(metadata)))
            db.execute("INSERT INTO rag_work_link(link_id,document_id,menu_work_id,status) VALUES('l','d',1,'matched')")
        db.close()
        shutil.copyfile(self.original, self.corrected)
        with sqlite3.connect(self.corrected) as db:
            db.execute("UPDATE rag_work_link SET menu_work_id=NULL,candidate_menu_work_id=1,status='ambiguous'")
        db.close()

    def test_citations_and_gate_inputs_held_once_with_history(self):
        plan = audit_and_hold(self.original, self.corrected)
        self.assertEqual([item["draft_id"] for item in plan], ["a", "b"])
        self.assertEqual(plan, audit_and_hold(self.original, self.corrected, apply=True))
        self.assertEqual([], audit_and_hold(self.original, self.corrected, apply=True))
        with sqlite3.connect(self.corrected) as db:
            self.assertEqual(dict(db.execute("SELECT draft_id,status FROM rag_content_draft")), {
                "a": "evidence_requested", "b": "evidence_requested",
                "c": "ready_for_downstream", "r": "rejected_archived",
            })
            for name in ("rag_queue_job", "rag_job_event", "rag_review_history", "rag_judgment_log"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0], 2)
            self.assertEqual(set(db.execute("SELECT queue_name,status FROM rag_queue_job")), {("collection", "blocked")})
        db.close()

    def test_active_review_rolls_back_all_changes(self):
        with sqlite3.connect(self.corrected) as db:
            db.execute("""INSERT INTO rag_queue_job(job_id,queue_name,payload_json,status,created_at,
                updated_at) VALUES('j','review','{"draft_id":"b"}','running','old','old')""")
        db.close()
        with self.assertRaisesRegex(RuntimeError, "active job"):
            audit_and_hold(self.original, self.corrected, apply=True)
        with sqlite3.connect(self.corrected) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rag_review_history").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT status FROM rag_content_draft WHERE draft_id='a'").fetchone()[0],
                             "ready_for_downstream")
        db.close()

    def test_live_review_reads_work_links_of_web_gate_documents_outside_the_draft(self):
        db = sqlite3.connect(self.corrected, check_same_thread=False)
        self.addCleanup(db.close)
        now = datetime.now(timezone.utc).isoformat()
        with db:
            db.execute("""UPDATE rag_content_draft SET created_at=?,updated_at=?,ai_disclosure=?,
                retrieved_chunk_ids_json='["draft-chunk"]' WHERE draft_id='b'""", (now, now, AI_DISCLOSURE))
            db.execute("""INSERT INTO rag_document(document_id,source_id,source_site_id,content_type,
                evidence_type,collection_method,title,raw_text,original_language,url,canonical_url,
                fetched_at,author_attribution,metadata_json,content_hash,created_at)
                VALUES('in-draft','sample',1,'guide','reference_fact','mcp_read','title','text','en',
                'https://example.org/work','https://example.org/work',?,'unknown','{}','hash',?)""", (now, now))
            db.execute("INSERT INTO rag_draft_evidence VALUES('b','in-draft','cited')")
            db.execute("""INSERT INTO rag_work_link(link_id,document_id,menu_work_id,status)
                VALUES('matched-draft','in-draft',1,'matched')""")
        store = SQLiteRagStore(db)
        repository = SQLiteDraftRepository(store)
        context = asyncio.run(repository.load_for_review("b"))
        self.assertNotIn("d", context.evidence)
        self.assertEqual(context.web_gate_work_ids, {"d": ()})
        asyncio.run(store.enqueue(QueueItem("review-b", QueueName.REVIEW, payload={"draft_id": "b"})))
        reviewer = AsyncMock()
        reviewer.review.return_value = ReviewResult("b", ReviewDecision.PASS, (), reviewer_id="fixture")
        worker = ReviewWorker(store, repository, SQLiteReviewHistoryRepository(store), reviewer)
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertEqual(db.execute("SELECT status FROM rag_content_draft WHERE draft_id='b'").fetchone()[0],
                         "evidence_requested")
        job = db.execute("SELECT status,payload_json FROM rag_queue_job WHERE queue_name='collection'").fetchone()
        self.assertEqual(job["status"], "blocked")
        self.assertTrue(any("web corroborator d" in message for message in json.loads(job["payload_json"])["evidence_requests"]))
