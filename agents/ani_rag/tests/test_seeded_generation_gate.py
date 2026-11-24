from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from ani_rag.collector import seed_work_is_confirmed
from ani_rag.catalog import LLMWorkCatalogResolver, LocalWorkCatalog
from ani_rag.interfaces import WorkIdentity, WorkResolution
from ani_rag.models import WorkLinkStatus, RagContentType
from ani_rag.rag_gate import compare_page_to_rag
from ani_rag.retrieval import EvidenceChunk


class SeededGenerationGateTests(unittest.TestCase):
    def test_only_the_requested_work_id_confirms_a_seed(self):
        self.assertTrue(seed_work_is_confirmed(
            42, WorkResolution(WorkLinkStatus.MATCHED, menu_work_ids=(42,), matched_on="exact_title"),
        ))
        self.assertFalse(seed_work_is_confirmed(
            42, WorkResolution(WorkLinkStatus.MATCHED, menu_work_ids=(43,), matched_on="exact_title"),
        ))
        self.assertFalse(seed_work_is_confirmed(42, WorkResolution(WorkLinkStatus.UNRESOLVED)))

    def test_exact_catalog_title_wins_over_seed_and_never_calls_llm(self):
        catalog = LocalWorkCatalog()
        seed = catalog.work_search_seeds()[0]
        exact = catalog.resolve_sync(WorkIdentity(titles=(seed.titles[0],), external_ids={}))
        other_id = next(item.menu_work_id for item in catalog.work_search_seeds()
                        if item.menu_work_id != seed.menu_work_id)

        class NeverCallModel:
            async def generate_json(self, **_kwargs):
                raise AssertionError("Exact catalog identity should not be sent to the LLM")

        resolver = LLMWorkCatalogResolver(catalog, NeverCallModel())
        matching = asyncio.run(resolver.resolve(WorkIdentity(
            titles=(seed.titles[0],), external_ids={}, source_text=seed.titles[0],
            seed_menu_work_ids=(seed.menu_work_id,),
        )))
        conflicting = asyncio.run(resolver.resolve(WorkIdentity(
            titles=(seed.titles[0],), external_ids={}, source_text=seed.titles[0],
            seed_menu_work_ids=(other_id,),
        )))

        self.assertEqual(matching.menu_work_ids, exact.menu_work_ids)
        self.assertEqual(conflicting.menu_work_ids, exact.menu_work_ids)
        self.assertFalse(seed_work_is_confirmed(other_id, conflicting))

    def test_rag_gate_requires_three_supported_claims_and_blocks_contradictions(self):
        now = datetime.now(timezone.utc)
        document = SimpleNamespace(document_id="reference-1", title="Reference", source_id="ann_encyclopedia",
                                   source_url="https://example.test/reference")
        chunk = EvidenceChunk(
            chunk_id="chunk-1", evidence_id="reference-1", content_type=RagContentType.MUSIC,
            title="Reference", text="A cited reference", locator="body", source_kind="body",
            source_index=None, start_offset=0, end_offset=19, source_id="ann_encyclopedia",
            source_url="https://example.test/reference", language="en", published_at=None,
            menu_work_ids=(42,), score=0.9,
        )

        class Retriever:
            async def retrieve(self, _request):
                return SimpleNamespace(documents=(document,), chunks=(chunk,))

        class Model:
            claims = [
                {"claim": f"Supported fact {i}", "status": "supported", "evidence_refs": ["R1"]}
                for i in range(3)
            ]

            async def generate_json(self, **_kwargs):
                return {"claims": list(self.claims)}

        async def run():
            model = Model()
            passed = await compare_page_to_rag(
                title="Candidate", url="https://example.test/candidate", body="Candidate facts",
                content_type=RagContentType.MUSIC, menu_work_id=42,
                retriever=Retriever(), model=model,
            )
            model.claims = [*model.claims, {
                "claim": "Contradicted fact", "status": "contradicted", "evidence_refs": ["R1"],
            }]
            rejected = await compare_page_to_rag(
                title="Candidate", url="https://example.test/candidate", body="Candidate facts",
                content_type=RagContentType.MUSIC, menu_work_id=42,
                retriever=Retriever(), model=model,
            )
            return passed, rejected

        passed, rejected = asyncio.run(run())
        self.assertTrue(passed.passed)
        self.assertEqual(len(passed.supported_claims), 3)
        self.assertFalse(rejected.passed)
        self.assertIn("contradictions", rejected.reason)


if __name__ == "__main__":
    unittest.main()
