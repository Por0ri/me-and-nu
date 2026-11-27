from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import unittest

from ani_rag.models import (
    CollectionMethod,
    EvidenceDocument,
    EvidenceType,
    RagContentType,
    WorkLinkStatus,
)
from ani_rag.retrieval import DatabaseEvidenceRetriever, RetrievalRequest


class CandidateSelfCorroborationTests(unittest.TestCase):
    def test_candidate_url_is_excluded_but_other_same_host_evidence_remains(self):
        now = datetime.now(timezone.utc)

        def document(document_id: str, url: str) -> EvidenceDocument:
            return EvidenceDocument(
                document_id=document_id,
                content_type=RagContentType.GUIDE,
                title="Attack on Titan profile",
                source_url=url,
                source_id="ann_encyclopedia",
                retrieved_at=now,
                evidence_type=EvidenceType.REFERENCE_FACT,
                collection_method=CollectionMethod.MANUAL_IMPORT,
                language="en",
                body="Attack on Titan profile, episode list, director and studio details.",
                work_link_status=WorkLinkStatus.MATCHED,
                menu_work_ids=(1,),
            )

        class Candidates:
            def __init__(self, original, independent):
                self.original = original
                self.independent = independent

            async def fetch_candidates(self, _request):
                return (
                    document("same-page", self.original),
                    document("other-page", self.independent),
                )

        wiki = "https://en.wikipedia.org/wiki/A_Silent_Voice_(film)"
        encoded = wiki.replace("(", "%28").replace(")", "%29")
        ann = "https://www.animenewsnetwork.com/encyclopedia/anime.php?id=16661"
        for original, excluded, independent in (
            ("https://example.test/encyclopedia/work/", "https://example.test/encyclopedia/work?utm_source=search",
             "https://example.test/encyclopedia/cast"),
            (encoded, wiki, ann), (wiki, encoded, ann),
        ):
            with self.subTest(original=original):
                request = RetrievalRequest(
                    query="Attack on Titan episode director studio", content_type=RagContentType.GUIDE,
                    menu_work_ids=(1,), exclude_source_urls=(excluded,), top_k=8, candidate_limit=8,
                )
                result = asyncio.run(DatabaseEvidenceRetriever(Candidates(original, independent)).retrieve(request))
                self.assertEqual({chunk.evidence_id for chunk in result.chunks}, {"other-page"})
                self.assertEqual({chunk.source_url for chunk in result.chunks}, {independent})


if __name__ == "__main__":
    unittest.main()
