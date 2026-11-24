from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import unittest

from ani_rag.queues import QueueItem, QueueName, QueueStatus
from ani_rag.review import HeadlineEdit
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.storage.sqlite_workflow import SQLiteDraftRepository
from ani_rag.workers import GenerationWorker


class HeadlineRevisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.conn.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        self.conn.executescript(schema.read_text(encoding="utf-8"))
        self.store = SQLiteRagStore(self.conn)
        self.drafts = SQLiteDraftRepository(self.store)
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """INSERT INTO rag_content_draft(draft_id,content_type,title,body,source_footer,
                   ai_disclosure,status,created_at,updated_at) VALUES('draft','guide','이름 셋','상세글',
                   '출처','고지','revision_requested',?,?)""", (now, now),
            )
            for page, title in enumerate(("이름 셋!", "엔딩 크레딧"), 1):
                card_id = f"card-{page}"
                self.conn.execute(
                    """INSERT INTO rag_content_card(card_id,draft_id,page_no,role,title,body)
                       VALUES(?,'draft',?,'detail',?,'본문')""", (card_id, page, title),
                )
                self.conn.execute(
                    """INSERT INTO rag_rendered_card(draft_id,card_id,image_png,width,height,
                       frontend_copy_json,frontend_layout_json,created_at) VALUES('draft',?,X'AB',100,100,?,'{}',?)""",
                    (card_id, json.dumps({"headline": title, "body": "본문"}), now),
                )
        self.edits = (HeadlineEdit(0, "이름 셋", "이름 넷"), HeadlineEdit(1, "이름 셋!", "이름 넷!"))
        self.review = QueueItem("review", QueueName.REVIEW, dedupe_key="review:draft:1",
                                payload={"draft_id": "draft", "revision_count": 1})

    async def test_generation_updates_only_headlines_and_queues_one_independent_review(self):
        await self.store.enqueue(QueueItem("rev", QueueName.GENERATION, dedupe_key="revision:draft:1", payload={
            "draft_id": "draft", "revision_count": 1, "headline_edits": [asdict(edit) for edit in self.edits],
        }))
        worker = GenerationWorker(self.store, None, None, None, self.drafts, None)
        self.assertTrue(await worker.run_once())
        self.assertEqual((await self.store.get("rev")).status, QueueStatus.SUCCEEDED)
        # A replay after commit must not overwrite a later title or dispatch a second review.
        await self.drafts.apply_headline_edits("draft", self.edits, self.review)
        queued = await self.store.claim(QueueName.REVIEW, now=datetime.now(timezone.utc))
        self.assertEqual(queued.payload, {"draft_id": "draft", "revision_count": 1})
        self.assertIsNone(await self.store.claim(QueueName.REVIEW, now=datetime.now(timezone.utc)))
        draft = self.conn.execute("SELECT status,title,body FROM rag_content_draft").fetchone()
        self.assertEqual(tuple(draft), ("pending_review", "이름 넷", "상세글"))
        cards = self.conn.execute("""SELECT title,json_extract(frontend_copy_json,'$.headline'),
             json_extract(frontend_copy_json,'$.body'),image_png,frontend_layout_json
             FROM rag_content_card JOIN rag_rendered_card USING(card_id) ORDER BY page_no""").fetchall()
        self.assertEqual([tuple(row) for row in cards], [
            ("이름 넷!", "이름 넷!", "본문", b"\xab", "{}"),
            ("엔딩 크레딧", "엔딩 크레딧", "본문", b"\xab", "{}"),
        ])

    async def test_stale_card_edit_rolls_back_article_title_and_review_job(self):
        stale = (self.edits[0], HeadlineEdit(1, "이전 제목", "교정 제목"))
        with self.assertRaises(ValueError):
            await self.drafts.apply_headline_edits("draft", stale, self.review)
        row = self.conn.execute("SELECT status,title FROM rag_content_draft").fetchone()
        self.assertEqual(tuple(row), ("revision_requested", "이름 셋"))
        self.assertIsNone(await self.store.get("review"))
