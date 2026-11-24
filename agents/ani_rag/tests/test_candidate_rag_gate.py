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
            async def fetch_candidates(self, _request):
                return (
                    document("same-page", "https://example.test/encyclopedia/work/"),
                    document("other-page", "https://example.test/encyclopedia/cast"),
                )

        request = RetrievalRequest(
            query="Attack on Titan episode director studio",
            content_type=RagContentType.GUIDE,
            menu_work_ids=(1,),
            exclude_source_urls=("https://example.test/encyclopedia/work?utm_source=search",),
            top_k=8,
            candidate_limit=8,
        )

        result = asyncio.run(DatabaseEvidenceRetriever(Candidates()).retrieve(request))

        self.assertTrue(result.chunks)
        self.assertEqual({chunk.evidence_id for chunk in result.chunks}, {"other-page"})
        self.assertTrue(all(chunk.source_url.endswith("/cast") for chunk in result.chunks))


if __name__ == "__main__":
    unittest.main()
