import hashlib
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ani_rag.adapter import canonical_source_url, canonical_web_candidate_url
from ani_rag.models import RagContentType
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.web_discovery import WebCandidateHit, _assessment_key_prefix, _candidate_page_from_cache


class ArticleKeyTests(unittest.TestCase):
    def test_only_wikipedia_article_path_parentheses_are_folded(self):
        url = "https://en.wikipedia.org/wiki/A_Silent_Voice_%28film%29"
        literal = url.replace("%28", "(").replace("%29", ")")
        self.assertEqual(canonical_web_candidate_url(url), canonical_web_candidate_url(literal))
        self.assertEqual(canonical_source_url(url), url)
        for other in (url.replace("wikipedia.org", "wikipedia.org.example"), url.replace("/wiki/", "/w/")):
            self.assertNotEqual(canonical_web_candidate_url(other), canonical_web_candidate_url(other.replace("%28", "(")))
        self.assertEqual(canonical_web_candidate_url(url + "?oldid=21"), literal + "?oldid=21")
        self.assertNotEqual(canonical_web_candidate_url(url + "?oldid=21"), canonical_web_candidate_url(literal + "?oldid=22"))
        self.assertTrue(canonical_web_candidate_url(url.replace("_%28", "%2F%28")).endswith("%2F(film)"))
        self.assertTrue(canonical_web_candidate_url(url.replace("%28", "%2528")).endswith("%2528film)"))

    def test_article_aliases_leave_the_original_read_url_and_functional_parameters_intact(self):
        base = "https://www.crunchyroll.com/news/interviews/example"
        self.assertEqual(canonical_web_candidate_url(base + "?a=1&SrsLtid=two&b=2"), base + "?a=1&b=2")
        self.assertEqual(canonical_source_url(base + "?srsltid=one"), base + "?srsltid=one")
        mobile = "https://m.blog.naver.com/nos05009/224356839955"
        desktop = "https://blog.naver.com/nos05009/224356839955"
        viewer = "https://blog.naver.com/PostView.naver?logNo=224356839955&blogId=nos05009"
        self.assertEqual(canonical_web_candidate_url(mobile), desktop)
        self.assertEqual(canonical_web_candidate_url(desktop + "?fromRss=true&trackingCode=rss"), desktop)
        self.assertEqual(canonical_web_candidate_url(viewer + "&language=en"), desktop + "?language=en")
        for different in (viewer.replace("nos05009", "other"), viewer.replace("224356839955", "999"),
                          viewer + "&blogId=other", viewer.replace(".com/", ".com.other/"),
                          mobile + "?lang=ko"):
            self.assertNotEqual(canonical_web_candidate_url(different), desktop)


class HistoricalCandidateURLTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.connection.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        self.connection.executescript(schema.read_text(encoding="utf-8"))

    def ledger(self, url, error=None):
        now = self.now.isoformat()
        self.connection.execute(
            """INSERT INTO rag_web_candidate_url_ledger(canonical_url,first_seen_at,last_seen_at,
               last_fetched_at,next_check_at,last_error_code,updated_at) VALUES(?,?,?,?,?,?,?)""",
            (url, now, now, None if error else now, (self.now + timedelta(days=1)).isoformat(), error, now),
        )
        self.connection.commit()

    async def test_prior_wiki_candidate_skips_the_other_spelling_in_both_directions(self):
        for work, old_title, new_title in ((3, "%28film%29", "(film)"), (4, "(film)", "%28film%29")):
            base = f"https://en.wikipedia.org/wiki/Work_{work}_"
            old, new = base + old_title, base + new_title
            self.ledger(old)
            old_prefix = f"web_candidate:{work}:guide:" + hashlib.sha256(old.encode()).hexdigest()[:24]
            now = self.now.isoformat()
            job_id = f"old-{work}"
            self.connection.execute(
                """INSERT INTO rag_queue_job(job_id,queue_name,handler_kind,dedupe_key,payload_json,status,
                   created_at,updated_at) VALUES(?,'collection','web_candidate_assessment',?,?,'pending',?,?)""",
                (job_id, old_prefix + ":unread:v", json.dumps({"candidate":{"url":old}}), now, now),
            )
            self.connection.commit()
            store = SQLiteRagStore(self.connection)
            prefix = _assessment_key_prefix(work, RagContentType.GUIDE, new)
            self.assertEqual(await store.web_candidate_assessment_plan(
                new, now=self.now, key_prefix=prefix, reference_version="v"), (None, job_id))
            self.connection.execute("UPDATE rag_queue_job SET status='blocked' WHERE job_id=?", (job_id,))
            self.connection.commit()
            self.assertEqual(await store.web_candidate_assessment_plan(
                new, now=self.now, key_prefix=prefix, reference_version="v"), (None, None))

    async def test_legacy_tracking_failure_survives_restart_and_expires(self):
        old = "https://example.org/news/story?srsltid=old"
        new = "https://example.org/news/story?srsltid=new"
        self.ledger(old, "redirect_unverified_chain")
        self.connection.execute(
            """INSERT INTO rag_web_candidate_page_cache(cache_id,canonical_url,status,retry_after,
               last_error_code,updated_at) VALUES(?,?,'failed',?,?,?)""",
            ("old-id", old, (self.now + timedelta(days=1)).isoformat(), "redirect_unverified_chain",
             self.now.isoformat()),
        )
        self.connection.commit()
        store = SQLiteRagStore(self.connection)
        prefix = _assessment_key_prefix(2, RagContentType.PEOPLE, new)
        self.assertEqual(await store.web_candidate_assessment_plan(
            new, now=self.now, key_prefix=prefix, reference_version="v"), (None, None))
        self.assertEqual((await store.claim_web_candidate_page(new, now=self.now))["status"], "retry_later")
        later = self.now + timedelta(days=2)
        revision, _ = await store.web_candidate_assessment_plan(new, now=later, key_prefix=prefix, reference_version="v")
        self.assertTrue(revision.startswith("due:"))
        acquired = await store.claim_web_candidate_page(new, now=later)
        self.assertEqual((acquired["status"], acquired["claim_id"]), ("acquired", "old-id"))
        self.assertEqual((await store.claim_web_candidate_page(old, now=later))["status"], "busy")
        await store.complete_web_candidate_page("old-id", source_url=new, final_url=new,
                                               page={"body": "B", "title": "T", "final_url": new},
                                               fetched_at=later)
        self.assertEqual((await store.claim_web_candidate_page(old, now=later))["status"], "cached")

    async def test_prior_mobile_article_and_old_queue_have_one_identity(self):
        old = "https://blog.naver.com/nos05009/224356839955?fromRss=true&trackingCode=rss"
        new = "https://m.blog.naver.com/nos05009/224356839955"
        self.ledger(old)
        now = self.now.isoformat()
        old_prefix = "web_candidate:2:people:" + hashlib.sha256(old.encode()).hexdigest()[:24]
        self.connection.execute(
            """INSERT INTO rag_queue_job(job_id,queue_name,handler_kind,dedupe_key,payload_json,status,
               created_at,updated_at) VALUES('old-job','collection','web_candidate_assessment',?,?,'pending',?,?)""",
            (old_prefix + ":unread:v", json.dumps({"candidate": {"url": old}}), now, now),
        )
        self.connection.commit()
        store = SQLiteRagStore(self.connection)
        prefix = _assessment_key_prefix(2, RagContentType.PEOPLE, new)
        self.assertEqual(await store.web_candidate_assessment_plan(
            new, now=self.now, key_prefix=prefix, reference_version="v"), (None, "old-job"))
        self.connection.execute("UPDATE rag_queue_job SET status='blocked' WHERE job_id='old-job'")
        self.connection.commit()
        self.assertEqual(await store.web_candidate_assessment_plan(
            new, now=self.now, key_prefix=prefix, reference_version="v"), (None, None))
        hit = WebCandidateHit("Post", new)
        cached = {"title": "Post", "body": "Text", "final_url": old, "fetched_at": now}
        page = _candidate_page_from_cache(hit, cached)
        self.assertEqual(page.final_url, old)
        self.assertEqual(page.hit.host, "blog.naver.com")
        with self.assertRaises(ValueError):
            _candidate_page_from_cache(hit, {**cached, "final_url": "https://unrelated.org/post"})
