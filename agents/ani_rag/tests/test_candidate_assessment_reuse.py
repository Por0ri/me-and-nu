import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ani_rag.models import RagContentType, WorkLinkStatus
from ani_rag.firecrawl_adapter import MCPToolCallError
from ani_rag.queues import QueueItem, QueueName, QueueStatus
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.workers import GenerationWorker
from ani_rag.web_discovery import (
    WebCandidateAssessmentWorker, WebCandidateHit, WebCandidatePage, WebDiscoveryRequest,
    WebDiscoveryWorker, _assessment_key_prefix, enqueue_web_discovery,
)


class CandidateAssessmentReuseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.reference_version = "original reference"
        self.body = "One Punch Man interview with the series director."
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.connection.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        self.connection.executescript(schema.read_text(encoding="utf-8"))
        self.store = SQLiteRagStore(self.connection)
        self.store.web_evidence_revision = AsyncMock(side_effect=lambda *_: self.reference_version)
        self.clock = patch("ani_rag.web_discovery._now", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.url = "https://example.org/interviews/saitama"
        self.hit = WebCandidateHit("One Punch Man director interview", self.url)
        self.adapter = SimpleNamespace(
            search_web=AsyncMock(return_value=(self.hit,)),
            read_web_candidate=AsyncMock(side_effect=self.read_page),
        )
        catalog = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(
            status=WorkLinkStatus.MATCHED, menu_work_ids=(7,),
        )))
        doc = SimpleNamespace(document_id="doc", title="Credits", content_hash="fixture-version")
        chunk = SimpleNamespace(score=1.0, evidence_id="doc", chunk_id="chunk",
                                source_id="ann_encyclopedia", source_url="https://example.org/credits",
                                text="Production credits.")
        retriever = SimpleNamespace(retrieve=AsyncMock(return_value=SimpleNamespace(
            chunks=(chunk,), documents=(doc,),
        )))
        self.verifier = SimpleNamespace(generate_json=AsyncMock(return_value={"claims": []}))
        self.search = WebDiscoveryWorker(self.store, self.adapter)
        self.assess = WebCandidateAssessmentWorker(
            self.store, self.adapter, catalog, retriever, self.verifier, page_cache=self.store,
        )

    def read_page(self, hit):
        return WebCandidatePage(hit, hit.title, self.url, self.body, "en", None, self.now)

    async def search_once(self):
        await enqueue_web_discovery(self.store, WebDiscoveryRequest(
            query="One Punch Man", menu_work_id=7, content_type=RagContentType.PEOPLE,
        ))
        self.assertTrue(await self.search.run_once())

    async def test_same_inputs_only_run_verifier_once_even_after_page_refresh(self):
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        self.verifier.generate_json.assert_awaited_once()
        self.adapter.search_web.return_value = (
            WebCandidateHit(self.hit.title, self.url + "?utm_source=another"),
        )
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        self.now += timedelta(days=31)
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        self.assertEqual(self.adapter.read_web_candidate.await_count, 2)
        self.verifier.generate_json.assert_awaited_once()
        outcomes = self.connection.execute(
            "SELECT last_error FROM rag_queue_job WHERE handler_kind='web_candidate_assessment' ORDER BY rowid",
        ).fetchall()
        self.assertTrue(outcomes[0][0].startswith("candidate_insufficient_rag_supported_claims"))
        self.assertEqual(outcomes[1][0], "candidate_assessment_unchanged")

    async def test_changed_evidence_reuses_cached_page_and_changed_page_rechecks_evidence(self):
        await self.search_once()
        await self.assess.run_once()
        self.reference_version = "new reference"
        await self.search_once()
        await self.assess.run_once()
        self.assertEqual(self.adapter.read_web_candidate.await_count, 1)
        self.assertEqual(self.verifier.generate_json.await_count, 2)
        self.now += timedelta(days=31)
        self.body += " New interview details added."
        await self.search_once()
        await self.assess.run_once()
        self.assertEqual(self.verifier.generate_json.await_count, 3)

    async def test_previous_policy_rechecks_holds_once_but_keeps_identical_successes(self):
        from ani_rag.source_evidence import WEB_ASSESSMENT_POLICY_VERSION
        from ani_rag.web_discovery import _reference_version
        def earlier_policy(*args):
            return _reference_version(*args).removesuffix(":" + WEB_ASSESSMENT_POLICY_VERSION)
        with patch("ani_rag.web_discovery._reference_version", side_effect=earlier_policy):
            await self.search_once()
            await self.assess.run_once()
        oldest = self.connection.execute(
            "SELECT job_id FROM rag_queue_job WHERE handler_kind='web_candidate_assessment'"
        ).fetchone()
        with self.connection:
            self.connection.execute("UPDATE rag_queue_job SET status='succeeded' WHERE job_id=?", (oldest["job_id"],))
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        with self.connection:
            self.connection.execute("UPDATE rag_queue_job SET status='blocked' WHERE job_id=?", (oldest["job_id"],))
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        self.assertEqual(self.adapter.read_web_candidate.await_count, 1)
        self.assertEqual(self.verifier.generate_json.await_count, 2)

    async def test_failed_provider_call_can_retry_the_same_inputs(self):
        self.verifier.generate_json.side_effect = [TimeoutError(), {"claims": []}]
        await self.search_once()
        await self.assess.run_once()
        await self.assess.run_once()
        self.assertEqual(self.verifier.generate_json.await_count, 2)
        self.adapter.read_web_candidate.assert_awaited_once()

    async def test_cached_unsupported_page_is_not_queued_again(self):
        failure = RuntimeError("page is unsupported")
        failure.unsupported_target = True
        failure.permanent_failure = True
        self.adapter.read_web_candidate.side_effect = failure
        await self.search_once()
        await self.assess.run_once()
        self.now += timedelta(days=1)
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        self.adapter.read_web_candidate.assert_awaited_once()
        self.verifier.generate_json.assert_not_awaited()

    async def test_provider_indexes_skip_new_and_already_queued_paid_reads(self):
        skipped = {
            "https://www.pinterest.com/ideas/one-piece-profile-picture/929291638559/": "image_ideas_index",
            "https://characterprofile.fandom.com/wiki/Category:One_Piece": "wiki_category_index",
            "https://onepiece.fandom.com/fr/wiki/Category%3ACharacters": "wiki_category_index",
        }
        self.adapter.search_web.return_value = tuple(WebCandidateHit(self.hit.title, url) for url in skipped)
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        for index, (url, reason) in enumerate(skipped.items()):
            job_id = f"already-queued-{index}"
            await self.store.enqueue(QueueItem(
                job_id, QueueName.COLLECTION, handler_kind="web_candidate_assessment",
                payload={"content_type": "people", "menu_work_id": 7,
                         "candidate": {"title": self.hit.title, "url": url}},
            ))
            self.assertTrue(await self.assess.run_once())
            self.assertEqual((await self.store.get(job_id)).last_error, reason)
        self.adapter.read_web_candidate.assert_not_awaited()
        self.verifier.generate_json.assert_not_awaited()

    async def test_site_refusal_skips_only_new_reads_on_the_same_host_until_expiry(self):
        cached_url = "https://example.org/cached"
        saved = await self.store.claim_web_candidate_page(cached_url, now=self.now)
        await self.store.complete_web_candidate_page(
            saved["claim_id"], source_url=cached_url, final_url=cached_url,
            page={"title": self.hit.title, "final_url": cached_url, "body": self.body,
                  "language": "en", "fetched_at": self.now.isoformat()}, fetched_at=self.now,
        )
        failure = MCPToolCallError("read", {"isError": True, "content": [{
            "type": "text", "text": "Firecrawl can't scrape this site because it is currently not supported.",
        }]})
        self.adapter.read_web_candidate.side_effect = failure
        await self.search_once()
        await self.assess.run_once()
        self.adapter.read_web_candidate.assert_awaited_once()
        future = self.url + "-other"
        self.adapter.search_web.return_value = (WebCandidateHit(self.hit.title, future),)
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        self.assertEqual(self.adapter.search_web.await_count, 2)
        # A job enqueued before another worker learned about the refusal does
        # not pin the pending queue. Its URL becomes eligible on rediscovery.
        job_id = "already-queued-during-host-cooldown"
        await self.store.enqueue(QueueItem(
            job_id, QueueName.COLLECTION, handler_kind="web_candidate_assessment",
            payload={"content_type":"people","menu_work_id":7,"candidate":{"title":self.hit.title, "url":future}},
        ))
        self.assertTrue(await self.assess.run_once())
        self.assertEqual((await self.store.get(job_id)).last_error, "candidate_site_read_cooldown")
        self.adapter.read_web_candidate.assert_awaited_once()
        self.adapter.search_web.return_value = (WebCandidateHit(self.hit.title, cached_url),)
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        self.verifier.generate_json.assert_awaited_once()
        self.adapter.read_web_candidate.assert_awaited_once()
        self.adapter.read_web_candidate.side_effect = lambda hit: WebCandidatePage(
            hit, hit.title, hit.url, self.body, "en", None, self.now,
        )
        for url in ("https://news.example.org/article", "https://example.org.net/article"):
            self.adapter.search_web.return_value = (WebCandidateHit(self.hit.title, url),)
            await self.search_once()
            self.assertTrue(await self.assess.run_once())
            self.assertEqual(self.adapter.read_web_candidate.await_args.args[0].url, url)
        self.now += timedelta(hours=6, seconds=5)
        for url in (self.url, future):
            self.adapter.search_web.return_value = (WebCandidateHit(self.hit.title, url),)
            await self.search_once()
            self.assertTrue(await self.assess.run_once())
            self.assertEqual(self.adapter.read_web_candidate.await_args.args[0].url, url)

    async def test_reference_only_claim_and_repeated_passage_cannot_fill_web_gate(self):
        self.body = "One Punch Man director interview. A special aired in October."
        self.verifier.generate_json.return_value = {"claims": [
            {"claim": value, "status": "supported", "evidence_refs": ["R1"]}
            for value in ("A special aired in October.", "A special aired in October.",
                          "The special retells the Marineford and Sabaody arcs.")
        ]}
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        self.assertIsNone(await self.store.claim(QueueName.GENERATION, now=self.now))
        reason, = self.connection.execute(
            "SELECT last_error FROM rag_queue_job WHERE handler_kind='web_candidate_assessment'",
        ).fetchone()
        self.assertTrue(reason.startswith("candidate_insufficient_rag_supported_claims:1;unsupported=2"))

    async def test_one_substantive_supported_passage_carries_the_original_story(self):
        passage = "The director altered the finale by animating it in a single long take."
        self.body = f"One Punch Man interview. {passage}"
        self.verifier.generate_json.return_value = {"claims": [
            {"claim": passage, "status": "supported", "role": "standalone_core", "evidence_refs": ["R1"]},
        ]}
        await self.search_once()
        await self.assess.run_once()
        job = await self.store.claim(QueueName.GENERATION, now=self.now)
        claims = job.payload["web_candidate"]["verified_claims"]
        self.assertEqual([item["claim"] for item in claims], [passage])
        self.assertEqual(claims[0]["kind"], "rag_core")

    async def test_interview_statement_uses_primary_citation_only_on_the_original_publisher(self):
        from ani_rag.source_evidence import web_quote_chunk_id
        anchor = "Kubota designed the characters for One Punch Man."
        statement = "I used a thicker outline for the final scene."
        label = f"Kubota: {statement}"
        self.body = f"{anchor}\n{label}"
        self.verifier.generate_json.return_value = {"claims": [
            {"claim": anchor, "status": "supported", "role": "context", "evidence_refs": ["R1"]},
            {"claim": statement, "status": "attributed_quote", "speaker": "Kubota", "attribution_excerpt": label,
             "role": "core", "evidence_refs": ["R1"]},
        ]}
        for host, expected in (("yattatachi.com.example.org", False), ("yattatachi.com", True)):
            self.url = f"https://{host}/one-punch-man-interview"
            self.hit = WebCandidateHit("One Punch Man interview", self.url)
            self.adapter.search_web.return_value = (self.hit,)
            await self.search_once()
            await self.assess.run_once()
            job = await self.store.claim(QueueName.GENERATION, now=self.now)
            self.assertEqual(job is not None, expected)
            if job:
                article = job.payload["web_candidate"]
                quote = article["verified_claims"][1]
                self.assertEqual(quote["speaker"], "Kubota")
                self.assertEqual(quote["chunk_ids"], [
                    web_quote_chunk_id(self.url, self.hit.title, article["original_text"], statement),
                ])
                self.assertEqual(article["corroborating_chunk_ids"], ["chunk"])

    async def test_background_details_cannot_replace_the_topic_or_cancel_a_contradiction(self):
        passages = ("The film has a director.", "The film has a writer.", "The film has a cast.")
        self.body = " ".join(passages)
        rows = [{"claim": value, "status": "supported", "role": "context", "evidence_refs": ["R1"]}
                for value in passages]
        self.verifier.generate_json.return_value = {"claims": rows}
        await self.search_once()
        await self.assess.run_once()
        self.assertIsNone(await self.store.claim(QueueName.GENERATION, now=self.now))
        self.reference_version = "changed"
        rows[0]["role"] = "standalone_core"
        rows[1]["status"] = "contradicted"
        await self.search_once()
        await self.assess.run_once()
        self.assertIsNone(await self.store.claim(QueueName.GENERATION, now=self.now))
        reason, = self.connection.execute(
            "SELECT last_error FROM rag_queue_job WHERE handler_kind='web_candidate_assessment' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        self.assertTrue(reason.startswith("candidate_has_rag_contradictions"))

    async def test_changed_verified_document_returns_to_assessment_once_without_another_page_read(self):
        passages = ("One Punch Man interview.", "This is the director.", "This is the writer.")
        self.body = " ".join(passages)
        self.verifier.generate_json.return_value = {"claims": [
            {"claim": value, "status": "supported", "evidence_refs": ["R1"]} for value in passages
        ]}
        await self.search_once()
        await self.assess.run_once()
        result = self.assess.retriever.retrieve.return_value
        result.documents[0].content_hash = "corrected-first"
        gen = GenerationWorker(self.store, self.store, self.assess.retriever, None, None, None)
        self.assertTrue(await gen.run_once())
        self.assertTrue(await self.assess.run_once())
        self.adapter.read_web_candidate.assert_awaited_once()
        self.assertEqual(self.verifier.generate_json.await_count, 2)
        result.documents[0].content_hash = "corrected-again"
        self.assertTrue(await gen.run_once())
        self.assertFalse(await self.assess.run_once())
        outcomes = self.connection.execute(
            "SELECT status,last_error FROM rag_queue_job WHERE queue_name='generation' ORDER BY rowid"
        ).fetchall()
        self.assertEqual([tuple(row) for row in outcomes],
                         [("blocked", "web_corroborating_chunks_need_reassessment")] * 2)

    async def test_unresolved_article_heading_requires_exact_edition_and_matching_ann_link(self):
        title = "Example Series Fan Letter"
        series = "Example Series"
        article = "https://www.animenewsnetwork.com/newtype/interview"
        ann = "https://www.animenewsnetwork.com/encyclopedia/anime.php?id=997"
        hit = WebCandidateHit(f"Interview with the creators behind {title}", article)
        catalog = SimpleNamespace(
            entries_for_work_id=lambda _: ({"titles": (title,)},),
            search_titles_for_work_id=lambda _: (series,),
            resolve_sync=lambda _: SimpleNamespace(menu_work_ids=(7,)),
        )
        self.assess.edition_catalog = catalog
        self.assess.page_cache = SimpleNamespace(linked_ann_work_ids=AsyncMock(return_value=(7,)))
        for url, heading, body, expected in (
            (article, hit.title, f"[{title}]({ann})", True),
            (article, hit.title, f"[{series}]({ann})", False),
            (article, "Another show", f"[{title}]({ann})", False),
            (article, hit.title, f"More about {title}", False),
            ("https://example.org/interview", hit.title, f"[{title}]({ann})", False),
            (article, f"{title} or Another Show?", f"[{title}]({ann}) and [Another Show]({ann})", False),
        ):
            with self.subTest(heading=heading, body=body, url=url):
                page = WebCandidatePage(WebCandidateHit(heading, url), heading, url, body, "en", None, self.now)
                self.assertEqual(await self.assess._linked_edition_matches(page, 7), expected)
        self.assess.page_cache.linked_ann_work_ids.return_value = (8,)
        self.assertFalse(await self.assess._linked_edition_matches(
            WebCandidatePage(hit, hit.title, article, f"[{title}]({ann})", "en", None, self.now), 7,
        ))

    async def test_old_cached_hash_upgrades_when_first_assessed(self):
        cached = await self.store.claim_web_candidate_page(self.url, now=self.now)
        await self.store.complete_web_candidate_page(
            cached["claim_id"], source_url=self.url, final_url=self.url, fetched_at=self.now,
            page={"title": self.hit.title, "final_url": self.url, "body": self.body, "language": "en",
                  "published_at": None, "fetched_at": self.now.isoformat()},
        )
        with self.connection:
            self.connection.execute("UPDATE rag_web_candidate_url_ledger SET last_content_hash='legacy'")
        await self.search_once()
        await self.assess.run_once()
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        self.adapter.read_web_candidate.assert_not_awaited()
        self.verifier.generate_json.assert_awaited_once()

    async def test_two_preexisting_jobs_cannot_own_the_same_evaluation(self):
        prefix = _assessment_key_prefix(7, RagContentType.PEOPLE, self.url)
        for name in ("unread", "read:old"):
            await self.store.enqueue(QueueItem(
                job_id=name, queue=QueueName.COLLECTION, handler_kind="web_candidate_assessment",
                dedupe_key=f"{prefix}:{name}",
            ))
        jobs = [await self.store.claim(QueueName.COLLECTION, now=self.now) for _ in range(2)]
        page = {"title": self.hit.title, "body": self.body, "final_url": self.url,
                "fetched_at": self.now.isoformat()}
        owners = await asyncio.gather(*(
            self.store.claim_web_assessment_inputs(job, source_url=self.url, key_prefix=prefix,
                                                  page=page, reference_version="ref") for job in jobs
        ))
        self.assertEqual(sum(owner is not None for owner in owners), 1)
        owner = next(owner for owner in owners if owner)
        self.assertEqual(owner.status, QueueStatus.RUNNING)
