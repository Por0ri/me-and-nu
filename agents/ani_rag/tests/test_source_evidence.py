from dataclasses import replace
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock

from ani_rag.adapter import ReadResult, SearchHit
from ani_rag.collector import CollectionRequest, collect_from_source
from ani_rag.interfaces import SourceReadClaim, StoredEvidenceReference
from ani_rag.models import AuthorAttribution, EvidenceType, RagContentType
from ani_rag.settings import load_source_registry
from ani_rag.source_evidence import interview_speaker_label, poll_context, review_passages


class SourceEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_interview_turn_cannot_be_assigned_to_another_or_ambiguous_speaker(self):
        quote = "We rewrote the ending together."
        context = f"**Natsume-san:** {quote}"
        body = f"Interview with Shingo Natsume and Chikashi Kubota.\n{context}"
        self.assertEqual(interview_speaker_label(body, "Shingo Natsume", context, quote), "Natsume-san")
        self.assertIsNone(interview_speaker_label(body, "Chikashi Kubota", context, quote))
        self.assertIsNone(interview_speaker_label(body + "\nAkira Natsume also attended.",
                                                  "Shingo Natsume", context, quote))
        self.assertIsNone(interview_speaker_label(body, "Shingo Natsume", context, "We dropped the ending."))

    async def collect(self, page, content_type, evidence_type):
        repo = AsyncMock()
        repo.claim_source_read.return_value = SourceReadClaim("claim", True)
        repo.upsert_and_schedule_generation.return_value = StoredEvidenceReference("doc", "doc")
        adapter = AsyncMock()
        adapter.list_tools.return_value = {"firecrawl_search", "firecrawl_scrape"}
        adapter.search.return_value = [SearchHit(page.source_id, page.title, page.url, "firecrawl", "firecrawl_search")]
        adapter.read.return_value = page
        await collect_from_source(CollectionRequest(page.source_id, content_type, evidence_type, page.title, "en"),
                                  registry=load_source_registry(), adapter=adapter, repository=repo)
        return repo

    async def test_attributed_review_passage_flows_from_source_to_storage(self):
        quote = "I found the quiet conversation more striking than the final battle."
        page = ReadResult("anime_corner", "Episode Review", "https://animecorner.me/episode-review/",
                          "By [A. Reviewer](https://animecorner.me/author/example/)\n\n" + quote,
                          "firecrawl", "firecrawl_scrape", published_at=datetime.now(timezone.utc),
                          redirects_verified=True)
        repo = await self.collect(page, RagContentType.REVIEW, EvidenceType.EDITORIAL_REVIEW)
        doc = repo.upsert_and_schedule_generation.await_args.args[0]
        self.assertEqual((doc.author_name, doc.author_attribution, doc.claims),
                         ("A. Reviewer", AuthorAttribution.ATTRIBUTED, (quote,)))
        self.assertEqual(doc.excerpts[-1].supports_claim_index, 0)
        self.assertIsNone(review_passages(replace(page, body="By Someone\n\n## Comments\n\n" + quote)))
        unsigned = await self.collect(replace(page, body=quote), RagContentType.REVIEW, EvidenceType.EDITORIAL_REVIEW)
        unsigned.upsert_and_schedule_generation.assert_not_awaited()
        unsigned.release_source_read.assert_awaited_once()

    async def test_incomplete_chart_stays_evidence_only_and_votes_are_not_respondents(self):
        page = ReadResult("anime_trending_polls", "Weekly Anime Chart", "https://www.anitrendz.com/charts/top-anime",
                          "Voting period: 2026-11-01 to 2026-11-07\nVotes: 1,200\nRanking method: weighted vote",
                          "firecrawl", "firecrawl_scrape", published_at=datetime.now(timezone.utc),
                          redirects_verified=True)
        repo = await self.collect(page, RagContentType.CURATION, EvidenceType.CURATION_RESULT)
        call = repo.upsert_and_schedule_generation.await_args
        context = call.args[0].curation_context
        self.assertEqual(context.window_start.isoformat(), "2026-11-01")
        self.assertEqual(context.aggregation_method, "weighted vote")
        self.assertIsNone(context.sample_size)
        self.assertFalse(call.kwargs["schedule_generation"])
        self.assertEqual(poll_context(replace(page, body=page.body + "\nRespondents: 970")).sample_size, 970)
