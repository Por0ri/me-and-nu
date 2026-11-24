from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import unittest

from ani_rag.content import ContentDraft
from ani_rag.models import RagContentType
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.storage.sqlite_workflow import SQLiteDraftRepository


class RevisionTopicHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.conn.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        self.conn.executescript(schema.read_text(encoding="utf-8"))
        self.drafts = SQLiteDraftRepository(SQLiteRagStore(self.conn))

    def add_draft(self, draft_id, title, parent=None, status="revision_requested",
                  works=(7,), kind="people"):
        self.conn.execute(
            """INSERT INTO rag_content_draft(draft_id,title,content_type,menu_work_ids_json,
               ai_judgment_basis_json,status,body,source_footer,ai_disclosure,created_at,updated_at)
               VALUES(?,?,?,?,?,?,'','','','2026-11-23','2026-11-23')""",
            (draft_id, title, kind, json.dumps(works), json.dumps({"revision_of_draft_id": parent}), status),
        )

    async def test_prior_versions_ignored_but_identical_unrelated_article_is_a_duplicate(self):
        title = "감독이 원펀맨 만화를 읽고 애니로 만들고 싶었던 이유"
        self.add_draft("first", title)
        self.add_draft("second", "다듬는 중인 인터뷰", "first")
        self.add_draft("third", "콘티 준비 이야기", "second")
        self.add_draft("independent", "성우의 녹음실 인사")
        topics = await self.drafts.recent_topics((7,), revision_of="third")
        self.assertEqual([topic["title"] for topic in topics], ["성우의 녹음실 인사"])
        draft = ContentDraft("fourth", RagContentType.PEOPLE, title, (), "인터뷰 본문", (), ("source",), (7,),
                             "retrieval", datetime.now(timezone.utc), "출처", retrieved_chunk_ids=("quote",),
                             ai_judgment_basis={"revision_of_draft_id": "third"})
        self.assertIsNone(await self.drafts.find_duplicate_topic(draft))
        self.assertEqual(await self.drafts.find_duplicate_topic(replace(draft, ai_judgment_basis={})), title)
        self.add_draft("independent-copy", title, status="ready_for_downstream")
        self.assertEqual(await self.drafts.find_duplicate_topic(draft), title)

    async def test_invalid_parent_links_do_not_exempt_other_works_types_or_approved_drafts(self):
        self.add_draft("other-work", "별도의 합작 기사", works=(7, 8))
        self.add_draft("other-kind", "캐릭터 인터뷰", kind="character")
        self.add_draft("approved", "이미 게시 대기 중인 글", status="ready_for_downstream")
        for parent in ("other-work", "other-kind", "approved"):
            revision = "revision-of-" + parent
            self.add_draft(revision, "수정본 " + parent, parent)
            topics = await self.drafts.recent_topics((7,), revision_of=revision)
            titles = {topic["title"] for topic in topics}
            self.assertTrue({"별도의 합작 기사", "캐릭터 인터뷰", "이미 게시 대기 중인 글"} <= titles)
            self.assertNotIn("수정본 " + parent, titles)

    async def test_cycle_ends_and_keeps_unrelated_topics(self):
        self.add_draft("A", "첫 번째", "B")
        self.add_draft("B", "두 번째", "A")
        self.add_draft("C", "독립 기사")
        self.assertEqual([topic["title"] for topic in await self.drafts.recent_topics((7,), revision_of="A")],
                         ["독립 기사"])

    async def test_revision_copy_includes_distinct_blocking_feedback_from_only_its_own_reviews(self):
        self.add_draft("one", "첫 원고")
        self.add_draft("two", "첫 수정본", "one")
        self.add_draft("other", "같은 작품의 다른 원고")
        quantity = {"code": "SOURCE", "message": "권수는 확인되지 않음", "blocking": True}
        action = {"code": "SOURCE", "message": "감독은 만들고 싶었다고 말함", "blocking": True}
        for draft_id, count, issues in (
            ("one", 0, [quantity, {**quantity, "message": "사진을 키워도 좋음", "blocking": False}]),
            ("two", 1, [action, quantity]),
            ("other", 0, [{**quantity, "message": "다른 기사 검수 내용"}]),
        ):
            self.conn.execute(
                "INSERT INTO rag_review_history VALUES(?,?,?,'revise',?,?,?)",
                ("rv-" + draft_id, draft_id, "reviewer", count, json.dumps({"issues": issues}), "2026-11-23"),
            )
        previous = await self.drafts.revision_copy("two", (7,))
        self.assertEqual(previous["title"], "첫 수정본")
        self.assertEqual(previous["prior_blocking_issues"], [
            {"code": "SOURCE", "message": "감독은 만들고 싶었다고 말함"},
            {"code": "SOURCE", "message": "권수는 확인되지 않음"},
        ])
