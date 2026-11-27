import asyncio
from datetime import datetime, timedelta, timezone
import json
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
    WebDiscoveryWorker, _assessment_key_prefix, _reference_version, enqueue_web_discovery,
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

    async def test_unverified_final_address_records_the_reason_without_evaluating_the_page(self):
        from ani_rag.adapter import PageReadProvenanceError

        self.adapter.read_web_candidate.side_effect = PageReadProvenanceError("redirect_missing_final_url")
        await self.search_once()
        self.assertTrue(await self.assess.run_once())

        cache = self.connection.execute(
            "SELECT status, last_error_code, page_json FROM rag_web_candidate_page_cache WHERE canonical_url=?",
            (self.url,),
        ).fetchone()
        self.assertEqual(tuple(cache), ("failed", "redirect_missing_final_url", None))
        self.verifier.generate_json.assert_not_awaited()

    async def test_player_and_comment_form_stop_before_model_calls_and_reuse_the_read(self):
        self.body = ("Close\n[Visit](https://example.org/#)\nVideo Player is loading.\n"
                     "Current TimeÂ 0:00\nAdvertisement\nGameRant\n"
                     "Submit a Thread of your own on GameRant under {{listingPage}}.\n"
                     "### Submit a Thread\nTopic\nLink (Optional)\nCancelSubmit")
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        error = self.connection.execute(
            "SELECT last_error FROM rag_queue_job WHERE handler_kind='web_candidate_assessment'"
        ).fetchone()[0]
        self.assertEqual(error, "candidate_article_body_missing")
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        self.adapter.read_web_candidate.assert_awaited_once()
        self.assess.work_catalog.resolve.assert_not_awaited()
        self.assess.retriever.retrieve.assert_not_awaited()
        self.verifier.generate_json.assert_not_awaited()

    async def test_one_credit_line_between_player_controls_is_still_assessed(self):
        self.body = "Video Player is loading.\n\nDirector | Chikara Sakurai\n\nAdvertisement"
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        self.assess.work_catalog.resolve.assert_awaited_once()
        self.verifier.generate_json.assert_awaited_once()

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

    async def test_new_policy_reassesses_success_without_duplicating_generation(self):
        self.body = "One Punch Man interview. It features director Chikara Sakurai. This is the writer."
        self.verifier.generate_json.return_value = {"claims": [
            {"claim": text, "status": "supported", "evidence_refs": ["R1"]}
            for text in ("One Punch Man interview.", "It features director Chikara Sakurai.", "This is the writer.")
        ]}
        with patch("ani_rag.web_discovery.WEB_ASSESSMENT_POLICY_VERSION", "earlier-policy"):
            await self.search_once()
            self.assertTrue(await self.assess.run_once())
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        rows = self.connection.execute(
            "SELECT count(*) FROM rag_queue_job WHERE queue_name='generation' AND status='pending'"
        ).fetchone()
        self.assertEqual(rows[0], 1)
        self.adapter.read_web_candidate.assert_awaited_once()
        self.assertEqual(self.verifier.generate_json.await_count, 2)

    async def test_video_with_only_player_and_title_is_held_once_before_identity_or_paid_assessment(self):
        self.url = "https://www.youtube.com/watch?v=saitama"
        self.hit = WebCandidateHit("One Punch Man interview", self.url)
        self.adapter.search_web.return_value = (self.hit,)
        self.body = "# One Punch Man interview\nShow transcript\n## Comments\nLook at that!"
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        row = self.connection.execute(
            "SELECT last_error FROM rag_queue_job WHERE handler_kind='web_candidate_assessment'"
        ).fetchone()
        self.assertEqual(row[0], "candidate_video_evidence_missing")
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        self.assess.work_catalog.resolve.assert_not_awaited()
        self.assess.retriever.retrieve.assert_not_awaited()
        self.verifier.generate_json.assert_not_awaited()
        self.adapter.read_web_candidate.assert_awaited_once()

    async def test_video_policy_rechecks_cached_source_once_and_preserves_generation_identity(self):
        self.url = "https://www.youtube.com/watch?v=saitama"
        self.hit = WebCandidateHit("One Punch Man interview", self.url, "GTA recommendations")
        self.adapter.search_web.return_value = (self.hit,)
        excerpt = "One Punch Man interview. It features director Chikara Sakurai. This is the writer."
        self.body = "# One Punch Man interview\n## Description\n" + excerpt + "\n## Comments\nGTA recommendations"
        self.verifier.generate_json.return_value = {"claims": [
            {"claim": text, "status": "supported", "evidence_refs": ["R1"]}
            for text in ("One Punch Man interview.", "It features director Chikara Sakurai.", "This is the writer.")
        ]}
        with patch("ani_rag.web_discovery._reference_version", return_value="old-policy"):
            await self.search_once()
            self.assertTrue(await self.assess.run_once())
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        self.assertEqual(self.assess.work_catalog.resolve.await_args.args[0].source_text, excerpt)
        self.assertNotIn("GTA", self.assess.retriever.retrieve.await_args.args[0].query)
        self.assertEqual(self.verifier.generate_json.await_args.kwargs["user_payload"]["candidate"]["body"], excerpt)
        generations = self.connection.execute(
            "SELECT payload_json FROM rag_queue_job WHERE queue_name='generation'"
        ).fetchall()
        self.assertEqual(len(generations), 1)
        self.assertEqual(json.loads(generations[0][0])["web_candidate"]["original_text"], excerpt)
        self.adapter.read_web_candidate.assert_awaited_once()

    async def test_two_workers_can_retry_a_v2_identity_hold_only_once(self):
        from ani_rag.source_evidence import WEB_ASSESSMENT_POLICY_VERSION
        with patch("ani_rag.web_discovery.WEB_ASSESSMENT_POLICY_VERSION", "work-and-quoted-sources-v2"):
            self.assess.work_catalog.resolve.return_value = SimpleNamespace(status=WorkLinkStatus.UNRESOLVED)
            await self.search_once()
            await self.assess.run_once()
        await self.search_once()
        prefix = _assessment_key_prefix(7, RagContentType.PEOPLE, self.url)
        original = self.connection.execute(
            "SELECT payload_json FROM rag_queue_job WHERE handler_kind='web_candidate_assessment' AND status='pending'"
        ).fetchone()
        await self.store.enqueue(QueueItem(
            job_id="overlap", queue=QueueName.COLLECTION, handler_kind="web_candidate_assessment",
            dedupe_key=f"{prefix}:overlap", payload=json.loads(original[0]),
        ))
        self.assess.work_catalog.resolve.return_value = SimpleNamespace(status=WorkLinkStatus.MATCHED, menu_work_ids=(7,))
        self.assertEqual(await asyncio.gather(self.assess.run_once(), self.assess.run_once()), [True, True])
        self.adapter.read_web_candidate.assert_awaited_once()
        self.verifier.generate_json.assert_awaited_once()
        stored = self.connection.execute(
            "SELECT json_extract(payload_json,'$.assessment_inputs') FROM rag_queue_job "
            "WHERE handler_kind='web_candidate_assessment' AND last_error LIKE 'candidate_insufficient_%'"
        ).fetchall()
        self.assertEqual(len(stored), 1)
        self.assertTrue(stored[0][0].endswith(WEB_ASSESSMENT_POLICY_VERSION))
        await self.search_once()
        self.assertFalse(await self.assess.run_once())

    async def test_failed_provider_call_can_retry_the_same_inputs(self):
        self.verifier.generate_json.side_effect = [TimeoutError(), {"claims": []}]
        await self.search_once()
        await self.assess.run_once()
        await self.assess.run_once()
        self.assertEqual(self.verifier.generate_json.await_count, 2)
        self.adapter.read_web_candidate.assert_awaited_once()

    async def test_changed_policy_rechecks_legacy_holds_only_with_retained_page(self):
        from ani_rag.web_discovery import _reference_version
        with patch("ani_rag.web_discovery.WEB_ASSESSMENT_POLICY_VERSION", "work-and-quoted-sources-v3"):
            await self.search_once()
            self.assertTrue(await self.assess.run_once())
        job = self.connection.execute("SELECT * FROM rag_queue_job WHERE handler_kind='web_candidate_assessment'").fetchone()
        payload = json.loads(job["payload_json"])
        prefix = _assessment_key_prefix(7, RagContentType.PEOPLE, self.url)
        async def can_reassess():
            rev, _ = await self.store.web_candidate_assessment_plan(
                self.url, now=self.now, key_prefix=prefix, reference_version=_reference_version(self.reference_version, payload),
            )
            return rev is not None
        for reason in ("candidate_work_identity_unconfirmed", "candidate_insufficient_rag_supported_claims:0",
                       "candidate_has_rag_contradictions:1", "candidate_rag_retrieval_empty:best=0"):
            with self.subTest(reason=reason), self.connection:
                self.connection.execute("UPDATE rag_queue_job SET last_error=? WHERE job_id=?", (reason, job["job_id"]))
                self.assertTrue(await can_reassess())
        with self.connection:
            self.connection.execute("DELETE FROM rag_web_candidate_page_cache")
        self.assertFalse(await can_reassess())
        self.adapter.read_web_candidate.assert_awaited_once()

    async def test_operational_hold_with_saved_inputs_does_not_own_editorial_decision(self):
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        with self.connection:
            self.connection.execute(
                "UPDATE rag_queue_job SET last_error='candidate_page_cache_backoff' WHERE handler_kind='web_candidate_assessment'"
            )
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        self.assertEqual(self.verifier.generate_json.await_count, 2)
        self.adapter.read_web_candidate.assert_awaited_once()

    async def test_unavailable_kitsu_lookup_can_recover_without_another_page_read(self):
        self.assess.work_catalog.resolve.side_effect = [
            SimpleNamespace(status=WorkLinkStatus.UNRESOLVED, matched_on="kitsu_lookup_unavailable"),
            SimpleNamespace(status=WorkLinkStatus.MATCHED, menu_work_ids=(7,)),
        ]
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        state = self.connection.execute(
            "SELECT status,last_error FROM rag_queue_job WHERE handler_kind='web_candidate_assessment'"
        ).fetchone()
        self.assertEqual(tuple(state), ("retry_wait", "kitsu_lookup_unavailable"))
        self.assertTrue(await self.assess.run_once())
        self.adapter.read_web_candidate.assert_awaited_once()
        self.verifier.generate_json.assert_awaited_once()

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
            "https://es.pinterest.com/pin/909867930968751692/": "image_pin",
            "https://in.pinterest.com/pin/denji-icon--528328600052009251/": "image_pin",
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
        passage = "In the finale, director Chikara Sakurai used a single long take."
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

    async def test_plot_and_title_do_not_advance_a_people_job(self):
        self.body = "Attack on Titan The Final Season (TV). Marley is invading Paradis."
        self.verifier.generate_json.return_value = {"claims": [
            {"claim": "Attack on Titan The Final Season (TV)", "status": "supported", "role": "context",
             "speaker": "", "attribution_excerpt": "", "evidence_refs": ["R1"]},
            {"claim": "invading Paradis", "status": "supported", "role": "core",
             "speaker": "", "attribution_excerpt": "", "evidence_refs": ["R1"]},
        ]}
        await self.search_once()
        await self.assess.run_once()
        self.assertIsNone(await self.store.claim(QueueName.GENERATION, now=self.now))
        failure = self.connection.execute(
            "SELECT last_error FROM rag_queue_job WHERE handler_kind='web_candidate_assessment'"
        ).fetchone()[0]
        self.assertTrue(failure.startswith("candidate_people_evidence_missing:2"))
        await self.search_once()
        self.assertFalse(await self.assess.run_once())

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

    async def test_haikyu_report_needs_the_panelists_choice_and_reason(self):
        from ani_rag.content import web_claims_can_generate, web_claims_for_generation

        self.url = "https://www.nerdsandbeyond.com/2024/08/26/interview-the-haikyu-creative-team/"
        self.hit = WebCandidateHit("Interview: The HAIKYU!! Creative Team Reflects on Their Favorite Characters at Anime NYC", self.url)
        self.adapter.search_web.return_value = (self.hit,)
        credit = "Producer Shoichiro Taguchi attended the panel."
        # Paraphrased article and independent-reference fixtures, not an assertion about the live corpus.
        report = "Shoichiro Taguchi chose Tanaka. The player's monologue about being ordinary encouraged him."
        self.body = f"{credit}\n\n{report}"
        reference = self.assess.retriever.retrieve.return_value.chunks[0]
        reference.text = credit
        anchor = {"claim": credit, "role": "standalone_core", "speaker": "Shoichiro Taguchi",
                  "attribution_excerpt": "Producer", "status": "supported", "evidence_refs": ["R1"]}
        focus = {"speaker": "Shoichiro Taguchi", "subject": "Tanaka", "detail": "being ordinary encouraged him"}
        self.verifier.generate_json.return_value = {"claims": [anchor, {
            "claim": report, "status": "supported", "role": "standalone_core", "evidence_refs": ["R1"],
            "interview_answer": {**focus, "reference_excerpt": credit},
        }]}
        await self.search_once()
        await self.assess.run_once()
        self.assertIsNone(await self.store.claim(QueueName.GENERATION, now=self.now))
        reason, = self.connection.execute("SELECT last_error FROM rag_queue_job WHERE handler_kind='web_candidate_assessment'").fetchone()
        self.assertTrue(reason.startswith("candidate_interview_answer_missing"))
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        self.verifier.generate_json.assert_awaited_once()
        candidate = self.verifier.generate_json.await_args.kwargs["user_payload"]["candidate"]
        self.assertFalse(candidate["firsthand_interview_publisher"])
        self.assertEqual(candidate["interview_subject"], "preference")
        self.assertIn(report, self.assess.retriever.retrieve.await_args.args[0].query)

        self.reference_version = "new independent account"
        corroboration = "Taguchi's favorite was Tanaka because his words about being ordinary gave Taguchi encouragement."
        reference.text = corroboration
        self.verifier.generate_json.return_value["claims"][1]["interview_answer"]["reference_excerpt"] = corroboration
        await self.search_once()
        await self.assess.run_once()
        job = await self.store.claim(QueueName.GENERATION, now=self.now)
        source = job.payload["web_candidate"]
        current = web_claims_for_generation(source, {"chunk": "C1"})
        self.assertEqual(current[1]["interview_answer"], focus)
        self.assertTrue(web_claims_can_generate(current, RagContentType.PEOPLE, source=source))
        self.assertFalse(web_claims_can_generate(current[:1], RagContentType.PEOPLE, source=source))
        self.adapter.read_web_candidate.assert_awaited_once()
        self.assertEqual(self.verifier.generate_json.await_count, 2)

    async def test_changed_verified_document_returns_to_assessment_once_without_another_page_read(self):
        passages = ("One Punch Man interview.", "It features director Chikara Sakurai.", "This is the writer.")
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

    async def test_ann_label_may_use_the_final_season_only_with_the_matching_catalog_entry_and_link(self):
        from ani_rag.catalog import LocalWorkCatalog
        self.assess.edition_catalog = LocalWorkCatalog()
        self.assess.page_cache = SimpleNamespace(linked_ann_work_ids=AsyncMock(return_value=(1,)))
        url = "https://www.animenewsnetwork.com/interest/interview"
        ann = "https://www.animenewsnetwork.com/encyclopedia/anime.php?id=22277"
        label = "Attack on Titan The Final Season"
        heading = f"Interview With {label} Director, CG Producer Streamed"
        page = WebCandidatePage(WebCandidateHit(heading, url), heading, url, f"[{label}]({ann})", "en", None, self.now)
        from dataclasses import replace
        self.assertTrue(await self.assess._article_work_matches(page, 1))
        self.assertFalse(await self.assess._article_work_matches(page, 7))
        self.assertFalse(await self.assess._article_work_matches(
            replace(page, body=f"[Attack on Titan Season 2]({ann})"), 1,
        ))
        self.assertFalse(await self.assess._article_work_matches(
            replace(page, title=f"Interview With {label} Part 2 Director"), 1,
        ))
        for suffix in (": The Final Chapters Director Interview", " — THE FINAL CHAPTERS Special 2"):
            with self.subTest(suffix=suffix):
                self.assertFalse(await self.assess._article_work_matches(replace(page, title=label + suffix), 1))
        self.assertTrue(await self.assess._article_work_matches(
            replace(page, title=f"{label}: Interview With the Director"), 1,
        ))
        self.assess.page_cache.linked_ann_work_ids.return_value = (7,)
        self.assertFalse(await self.assess._article_work_matches(page, 1))

    async def test_fetched_topic_and_lead_must_identify_the_same_exact_anime(self):
        from ani_rag.catalog import LocalWorkCatalog
        from dataclasses import replace
        self.assess.edition_catalog = LocalWorkCatalog()
        url = "https://en.wikipedia.org/wiki/Music_of_Attack_on_Titan"
        heading = "Music of Attack on Titan - Wikipedia"
        lead = ('This article lists the soundtrack albums attributed to the [anime](https://example.org/anime "Anime") '
                'series _**[Attack on Titan](https://example.org/Attack_on_Titan_(TV_series) "Attack on Titan (TV series)")**_.')
        page = WebCandidatePage(WebCandidateHit("A misleading search snippet", url), heading, url, lead, "en", None, self.now)
        self.assertTrue(await self.assess._article_work_matches(page, 1))
        for invalid in (
            replace(page, title="Music of Attack on Titan: Junior High - Wikipedia"),
            replace(page, body="We focus on the anime series Attack on Titan: Junior High."),
            replace(page, body="This article discusses a different composer and their work.\n\n" + lead),
            replace(page, body="# Related articles\n\n| " + lead + " |"),
        ):
            self.assertFalse(await self.assess._article_work_matches(invalid, 1))
        self.assertFalse(await self.assess._article_work_matches(page, 7))

    async def test_episode_list_identifies_the_anime_in_its_article_lead(self):
        from dataclasses import replace
        from ani_rag.catalog import LocalWorkCatalog
        self.assess.edition_catalog = LocalWorkCatalog()
        url = "https://en.wikipedia.org/wiki/List_of_Assassination_Classroom_episodes"
        heading = "List of Assassination Classroom episodes - Wikipedia"
        lead = "Assassination Classroom is an anime series adapted from the manga of the same name."
        chrome = "Help our readers today.\n\nFrom Wikipedia, the free encyclopedia\n\n[![](cover.jpg)](cover) DVD cover.\n\n"
        page = WebCandidatePage(WebCandidateHit(heading, url), heading, url, chrome + lead, "en", None, self.now)
        self.assertTrue(await self.assess._article_work_matches(page, 17))
        self.assertFalse(await self.assess._article_work_matches(page, 16))
        for body in (
            chrome + lead.replace("Classroom is", "Classroom: Junior High is"),
            chrome + "My " + lead,
            chrome + "This article is an index of a separate live-action series.\n\n" + lead,
            chrome + "## Related shows\n\n" + lead,
            chrome + lead.replace("anime series", "American live-action series"),
        ):
            with self.subTest(body=body):
                self.assertFalse(await self.assess._article_work_matches(replace(page, body=body), 17))

        url = "https://yakusokunoneverland.fandom.com/wiki/List_of_Episodes"
        heading = "List of Episodes | The Promised Neverland Wiki | Fandom"
        lead = "The Promised Neverland is a CloverWorks anime adaptation of the manga of the same name."
        page = WebCandidatePage(WebCandidateHit(heading, url), heading, url, "# List of Episodes\n\n" + lead, "en", None, self.now)
        self.assertTrue(await self.assess._article_work_matches(page, 16))
        self.assertFalse(await self.assess._article_work_matches(replace(page, body=lead), 16))
        self.assertFalse(await self.assess._article_work_matches(replace(page, body=page.body.replace("Neverland is", "Neverland: A Side Story is")), 16))
        self.assertFalse(await self.assess._article_work_matches(replace(page, title=heading.replace("Neverland", "Neverland: A Side Story")), 16))

    async def test_named_episode_lists_match_the_fetched_anime_article_link_and_its_lead(self):
        from dataclasses import replace
        from ani_rag.catalog import LocalWorkCatalog
        self.assess.edition_catalog = LocalWorkCatalog()
        subject = "Attack on Titan"
        heading = f"List of {subject} episodes"
        for domain, marker, prefix, statement, suffix in (
            ("en.wikipedia.org", "TV_series", "From Wikipedia, the free encyclopedia",
             "{} is a Japanese [dark fantasy](https://en.wikipedia.org/wiki/Dark_fantasy) "
             "[anime](https://en.wikipedia.org/wiki/Anime) television series based on the manga.", " - Wikipedia"),
            ("attackontitan.fandom.com", "Anime", "# " + heading, "This is a list of all the episodes from the "
             "{} anime's first season.", " | Attack on Titan Wiki | Fandom"),
        ):
            with self.subTest(domain=domain):
                url = f"https://{domain}/wiki/List_of_Attack_on_Titan_episodes"
                target = f"https://{domain}/wiki/Attack_on_Titan_({marker})"
                label = f'_[{subject}]({target} "{subject} ({marker})")_'
                lead = statement.format(label)
                navigation = f"[Season 2](https://{domain}/wiki/Season_2)"
                body = f"Join the quest! Share your opinion.\n\n{prefix}\n\n{navigation}\n\n{lead}"
                page = WebCandidatePage(WebCandidateHit("search snippet only", url), heading + suffix,
                                        url, body, "en", None, self.now)
                self.assertTrue(await self.assess._article_work_matches(page, 1))
                self.assertFalse(await self.assess._article_work_matches(page, 7))
                if domain.endswith(".wikipedia.org"):
                    self.assertTrue(await self.assess._article_work_matches(
                        replace(page, body=body.replace("dark fantasy", "science fiction")), 1))
                    self.assertFalse(await self.assess._article_work_matches(
                        replace(page, body=body.replace("dark fantasy", "manga adaptation of an")), 1))
                for changed in (
                    body.replace(target, target.replace(domain, "another.example")),
                    body.replace(target, target.replace("Attack_on_Titan_", "Attack_on_Titan:_Junior_High_")),
                    body.replace(target, target.replace(f"_({marker})", "_(manga)")),
                    body.replace(lead, "An unrelated program is the topic of this story.\n\n" + lead),
                    body.replace(lead, "## Related pages\n\n" + lead),
                    body.replace(lead, navigation),
                ):
                    self.assertFalse(await self.assess._article_work_matches(replace(page, body=changed), 1))
                self.assertFalse(await self.assess._article_work_matches(
                    replace(page, title=page.title.replace(subject, "Attack on Titan: Junior High")), 1))
                if domain.endswith(".fandom.com"):
                    self.assertFalse(await self.assess._article_work_matches(
                        replace(page, body=body.replace(prefix, "# Other work")), 1))

    def test_only_named_wiki_episode_list_urls_require_a_new_assessment_version(self):
        previous = _reference_version("evidence", {})
        for host in ("en.wikipedia.org", "attackontitan.fandom.com"):
            url = f"https://{host}/wiki/List_of_Attack_on_Titan_episodes"
            self.assertNotEqual(_reference_version("evidence", {"candidate": {"url": url}}), previous)
            self.assertEqual(_reference_version("evidence", {"candidate": {"url": url + "/Season_2"}}), previous)
        url = "https://www.imdb.com/title/tt2560140/episodes/"
        self.assertEqual(_reference_version("evidence", {"candidate": {"url": url}}), previous)

    def test_only_fandom_music_and_episode_guides_refresh_their_cached_evaluation(self):
        ordinary = _reference_version("refs", {})
        def version(content_type, url, title):
            return _reference_version("refs", {
                "content_type": content_type, "candidate": {"url": url, "title": title},
            })
        song = "https://ansatsukyoshitsu.fandom.com/wiki/Seishun_Satsubatsuron"
        guide = "https://namu.wiki/w/example"
        self.assertNotEqual(version("music", song, "Seishun Satsubatsuron"), ordinary)
        self.assertNotEqual(version("guide", guide, "암살교실/애니메이션/에피소드 가이드"), ordinary)
        self.assertEqual(version("character", song, "Seishun Satsubatsuron"), ordinary)
        self.assertEqual(version("music", "https://example.org/wiki/song", "song"), ordinary)
        self.assertEqual(version("guide", guide, "암살교실 성우 소개"), ordinary)

    async def test_missing_episode_evidence_waits_for_new_reference_before_reusing_cached_page(self):
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        with self.connection:
            self.connection.execute(
                "UPDATE rag_queue_job SET last_error='candidate_episode_reference_missing:0;unsupported=8' "
                "WHERE handler_kind='web_candidate_assessment'"
            )
        await self.search_once()
        self.assertFalse(await self.assess.run_once())
        self.reference_version = "reference with newly indexed episodes"
        await self.search_once()
        self.assertTrue(await self.assess.run_once())
        self.adapter.read_web_candidate.assert_awaited_once()

    async def test_ambiguous_resolution_cannot_be_replaced_by_the_search_seed(self):
        self.assess.edition_catalog = SimpleNamespace()
        self.assess.work_catalog.resolve.return_value = SimpleNamespace(status=WorkLinkStatus.AMBIGUOUS)
        with patch.object(self.assess, "_article_work_matches", new_callable=AsyncMock) as recover:
            await self.search_once()
            await self.assess.run_once()
            recover.assert_not_awaited()
        self.verifier.generate_json.assert_not_awaited()

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
