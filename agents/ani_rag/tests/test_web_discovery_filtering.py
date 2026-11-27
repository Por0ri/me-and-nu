import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from ani_rag.firecrawl_adapter import MCPToolCallError
from ani_rag.models import RagContentType
from ani_rag.queues import QueueItem, QueueName, QueueStatus
from ani_rag.web_discovery import (
    WebCandidateHit, WebCandidatePage, _candidate_assessment_body,
    _candidate_assessment_trace, _candidate_mentions_work, _candidate_pre_read_rejection_reason,
    _candidate_rag_query, _failure,
)


class WebDiscoveryFilteringTests(unittest.TestCase):
    def test_provider_indexes_do_not_block_editorial_routes(self):
        for url in (
            "https://onepiece.fandom.com/wiki/Episodes_and_Volumes",
            "https://onepiece.fandom.com/wiki/Category_theory",
            "https://newsroom.pinterest.com/news/anime-interview/",
            "https://example.org/pin/9212345/",
            "https://example.org/ideas/an-interview",
            "https://www.reddit.com/r/OnePiece/comments/123/a_discussion",
        ):
            with self.subTest(url=url):
                hit = WebCandidateHit("One Piece interview", url)
                self.assertIsNone(_candidate_pre_read_rejection_reason(hit, RagContentType.PEOPLE))

    def test_only_search_hits_mentioning_the_seeded_work_are_queued(self):
        title = "Fullmetal Alchemist: Brotherhood"
        relevant = WebCandidateHit(
            title="Fullmetal Alchemist Brotherhood review",
            url="https://example.com/review", snippet="A look at Fullmetal Alchemist.",
        )
        unrelated = WebCandidateHit(
            title="Best Anime of Fall 2024 Tier List",
            url="https://example.com/ranking", snippet="Our season ranking.",
        )

        self.assertTrue(_candidate_mentions_work(relevant, (title, "鋼の錬金術師")))
        self.assertFalse(_candidate_mentions_work(unrelated, (title, "鋼の錬金術師")))

    def test_unsupported_scrape_target_is_permanent_and_not_retried(self):
        error = MCPToolCallError("read", {
            "isError": True,
            "content": [{
                "type": "text",
                "text": "Firecrawl can't scrape this site because it is currently not supported.",
            }],
        })
        job = QueueItem(
            job_id="job-test", queue=QueueName.COLLECTION,
            status=QueueStatus.RUNNING, created_at=datetime.now(timezone.utc),
        )

        result = _failure(job, "web_candidate_error", error)

        self.assertTrue(error.unsupported_target)
        self.assertTrue(error.unsupported_site)
        self.assertEqual(result.status, QueueStatus.BLOCKED)
        self.assertIn("unsupported_target", result.last_error)

    def test_only_explicit_provider_site_refusals_start_a_read_cooldown(self):
        refusal = "Firecrawl cannot scrape this site because it is currently not supported."
        for op, message, metadata in (
            ("read", "This URL is currently not supported.", {}),
            ("read", "This page is not supported.", {}),
            ("web_discovery_search", refusal, {}),
            ("read", refusal, {"status":401}), ("read", refusal, {"status":429}),
            ("read", refusal, {"status":503}), ("read", refusal, {"status":302}),
            ("read", refusal + " Redirect to a different domain.", {}),
            ("read", refusal + " Authentication required.", {}),
            ("read", refusal + " Request timed out.", {}),
            ("read", refusal, {"metadata":{"redirectChain":["https://a.org/x","https://b.org/y"]}}),
            ("read", refusal, {"metadata":{"url":"https://b.org/y"}}),
        ):
            with self.subTest(op=op, message=message, metadata=metadata):
                error = MCPToolCallError(op, {"isError": True, "structuredContent": {**metadata,"message":message}},
                                         requested_url="https://a.org/x")
                self.assertFalse(error.unsupported_site)

    def test_fandom_chrome_and_contents_are_removed_but_article_tables_remain(self):
        now = datetime.now(timezone.utc)
        hit = WebCandidateHit(
            title="Episodes and Volumes", url="https://example.com/wiki/Episodes",
        )
        page = WebCandidatePage(
            hit=hit, title="Episodes and Volumes | Kimetsu no Yaiba Wiki | Fandom",
            final_url=hit.url,
            body=(
                "Fandom is on a quest for your opinions\n\n## Contents\n\n- [Season 1](#season-1)"
                "\n\n# Episodes and Volumes\n\n[Sign In to Save](/register)"
                "\n\n## Contents\n\n- [Season 1](#season-1)\n- [Season 2](#season-2)"
                "\n\n## Season 1\n\n| # | Release Date |\n|---|---|\n| 1 | July 31, 2019 |"
                "\n\n## Season 2\n\nEpisode list for the Entertainment District Arc."
            ),
            language="en", published_at=None, fetched_at=now,
        )

        extracted = _candidate_assessment_body(page)
        query = _candidate_rag_query(page, hit)

        self.assertNotIn("Fandom is on a quest", extracted)
        self.assertNotIn("## Contents", extracted)
        self.assertIn("| # | Release Date |", extracted)
        self.assertIn("## Season 2", extracted)
        self.assertNotIn("Fandom is on a quest", query)
        self.assertIn("Season 2", query)

    def test_blocked_claim_trace_keeps_status_and_reference_identity(self):
        chunk = SimpleNamespace(
            chunk_id="chunk-7", evidence_id="doc-3", source_id="ann_encyclopedia",
            source_url="https://example.com/ref",
        )
        document = SimpleNamespace(title="Demon Slayer: Kimetsu no Yaiba")
        trace = _candidate_assessment_trace(
            [
                {"claim": "The arc has seven episodes", "status": "insufficient", "evidence_refs": ["R1"]},
                {"claim": "The anime is based on the manga", "status": "supported", "evidence_refs": ["R1"]},
            ],
            {"R1": chunk}, {"doc-3": document},
        )

        self.assertEqual(trace["counts"], {"supported": 1, "insufficient": 1, "contradicted": 0, "invalid": 0})
        self.assertEqual(trace["claims"][0]["evidence"][0]["chunk_id"], "chunk-7")
        self.assertEqual(trace["claims"][0]["evidence"][0]["document_title"], document.title)


if __name__ == "__main__":
    unittest.main()
