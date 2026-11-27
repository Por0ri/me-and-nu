"""Bounded RAG adapter tests: isolated SQLite, local images, no provider calls."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

from PIL import Image

from ani_rag.celery_bridge import (
    BridgeError, RecordedModel, RecordedQueryEmbeddings, RecordingOpenAIModel,
    UsageLedger, run_single_request,
)
from ani_rag.content import CardDraft, ContentDraft, ImageAsset, SupportedClaim
from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.queues import QueueItem, QueueName
from ani_rag.retrieval import EvidenceChunk, GenerationWorkRequest, RetrievedEvidence
from ani_rag.review import ReviewDecision, ReviewResult
from ani_rag.storage.sqlite_core import SQLiteRagStore
from ani_rag.storage.sqlite_workflow import SQLiteImageReader


class OfflineGenerator:
    def __init__(self):
        self.calls = 0
        self.remove_claims = False

    async def generate(self, retrieval, *, image_candidates=(), **kwargs):
        self.calls += 1
        chunk = retrieval.chunks[0]
        claim = SupportedClaim(chunk.text, (chunk.evidence_id,), (chunk.chunk_id,))
        cards = tuple(CardDraft(f"card-{i}", role, title, body,
            () if self.remove_claims else (claim,), asset.asset_id,
            image_credit="Fixture studio", source_image_url=asset.source_url)
            for i, (asset, role, title, body) in enumerate(zip(image_candidates[:3],
                ("cover", "body", "ending"), ("함께 걷는 여행", "동료의 약속", "새로운 출발"),
                ("낯선 길 위에서 두 친구가 만났어.", "어려운 순간에도 서로를 도왔어.", "함께한 기억이 다음 여행으로 이어져.")), 1))
        return ContentDraft("fixture-draft", RagContentType.GUIDE, "두 친구의 여행",
            cards, "두 친구가 함께 여행하며 서로를 돕는 이야기야.", (claim,), (chunk.evidence_id,),
            (1,), retrieval.retrieval_id, datetime.now(timezone.utc),
            "\n".join([retrieval.documents[0].source_url, *(item.source_url for item in image_candidates[:3])]),
            retrieved_chunk_ids=(chunk.chunk_id,))


class OfflineReviewer:
    def __init__(self, decision=ReviewDecision.PASS):
        self.calls, self.decision = 0, decision

    async def review(self, packet):
        self.calls += 1
        return ReviewResult(packet.draft.draft_id, self.decision, (),
            revision_instructions=("두 번째 문장을 더 자연스럽게 고쳐줘.",) if self.decision is ReviewDecision.REVISE else (),
            reviewer_id="offline-independent-reviewer", scorecard={"aggregate_score": 95})


class CeleryBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.db = sqlite3.connect(self.root / "isolated.sqlite3", check_same_thread=False)
        self.addCleanup(self.db.close)
        self.db.executescript((Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql").read_text(encoding="utf-8"))
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("""INSERT INTO rag_source(source_id,media_name,source_kind,collect_method,site_url,
            source_grade,copyright_status,license_basis,is_collecting,configuration_json,created_at,updated_at)
            VALUES('fixture_source','Fixture studio','official','manual','https://example.test',
            'primary','permitted','fixture',0,'{}','t','t')""")
        self.db.commit()
        self.store = SQLiteRagStore(self.db)
        self.images = SQLiteImageReader(self.store)
        self.document = EvidenceDocument("original-evidence", RagContentType.GUIDE, "두 친구의 여행",
            "https://example.test/story", "fixture_source", datetime.now(timezone.utc),
            EvidenceType.REFERENCE_FACT, CollectionMethod.MANUAL_IMPORT, "ko",
            body="두 친구가 함께 여행하며 서로를 돕는다.", work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=(1,))
        asyncio.run(self.store.upsert_and_schedule_generation(self.document, schedule_generation=False))
        for index, color in enumerate(((220, 90, 80), (70, 160, 100), (70, 100, 200)), 1):
            path = self.root / f"image-{index}.png"
            Image.new("RGB", (700, 900), color).save(path)
            png = path.read_bytes()
            asyncio.run(self.images.register_asset(ImageAsset(f"image-{index}",
                f"https://example.test/image-{index}.png", "fixture_source", "matched", (1,),
                "fixture", "https://example.test/license", str(path),
                content_sha256=hashlib.sha256(png).hexdigest(), media_type="image/png", byte_size=len(png), width=700, height=900)))
        body = self.document.body
        self.chunk = EvidenceChunk("original-chunk", self.document.document_id, self.document.content_type,
            self.document.title, body, f"body:0-{len(body)}", "body", None, 0, len(body),
            self.document.source_id, self.document.source_url, "ko", None, (1,), 0.9)
        document, chunk = self.document, self.chunk

        class Retriever:
            async def retrieve(self, request):
                return RetrievedEvidence("fixture-retrieval", (document,), (chunk,), request.query,
                    request.content_type, request.menu_work_ids, request.seed_evidence_ids,
                    datetime.now(timezone.utc))
        self.retriever = Retriever()
        self.request = GenerationWorkRequest("stable-source-topic", "두 친구의 여행", RagContentType.GUIDE,
                                             (self.document.document_id,), (1,))
        self.generator, self.reviewer = OfflineGenerator(), OfflineReviewer()
        self.kwargs = dict(request=self.request, namespace="fixture-corpus", job_id=str(uuid4()), attempt_id=str(uuid4()),
            retriever=self.retriever, images=self.images, source_ids=("fixture_source",),
            offline=True, generation_service=self.generator, reviewer=self.reviewer)

    def execute(self, **changes):
        return asyncio.run(run_single_request(self.db, **{**self.kwargs, **changes}))

    def test_exact_job_only_preserves_original_records_and_captures_native_pass(self):
        now = datetime.now(timezone.utc)
        for name, queue in (("unrelated-generation", QueueName.GENERATION), ("unrelated-review", QueueName.REVIEW)):
            asyncio.run(self.store.enqueue(QueueItem(name, queue, created_at=now, updated_at=now,
                                                    payload={"never": "claim this"})))
        original_queues = [tuple(row) for row in self.db.execute("SELECT * FROM rag_queue_job ORDER BY job_id")]
        original_docs = [tuple(row) for row in self.db.execute("SELECT * FROM rag_document")]
        result = self.execute()
        self.assertEqual(result["outcome"], "ready_for_staging", result)
        self.assertEqual(result["producer_status"], "ready_for_downstream")
        self.assertEqual(result["producer"], "ani_rag")
        self.assertTrue(result["simulated"])
        self.assertEqual(result["review_mode"], "offline_fixture")
        self.assertEqual(result["draft"]["draft_id"], "fixture-draft")
        self.assertEqual(result["review"]["decision"], "pass")
        self.assertEqual(list(result["evidence"]), ["original-evidence"])
        self.assertEqual(list(result["retrieved_chunks"]), ["original-chunk"])
        self.assertEqual((self.generator.calls, self.reviewer.calls), (1, 1))
        self.assertEqual([tuple(row) for row in self.db.execute("SELECT * FROM rag_document")], original_docs)
        self.assertEqual([tuple(row) for row in self.db.execute("SELECT * FROM rag_queue_job WHERE job_id LIKE 'unrelated-%' ORDER BY job_id")], original_queues)
        for row in result["rendered_cards"]:
            self.assertEqual(row["sha256"], hashlib.sha256(base64.b64decode(row["png_base64"])).hexdigest())
            self.assertEqual((row["width"], row["height"]), (1308, 1632))

    def test_duplicate_delivery_returns_same_result_and_other_attempt_cannot_regenerate(self):
        first = self.execute()
        self.assertEqual(self.execute(), first)
        self.assertEqual((self.generator.calls, self.reviewer.calls), (1, 1))
        with self.assertRaisesRegex(BridgeError, "generation_already_bound"):
            self.execute(job_id=str(uuid4()), attempt_id=str(uuid4()))
        self.assertEqual(self.db.execute("SELECT count(*) FROM rag_content_draft").fetchone()[0], 1)

    def test_review_revision_is_preserved_but_never_regenerated(self):
        self.reviewer.decision = ReviewDecision.REVISE
        result = self.execute()
        self.assertEqual((result["outcome"], result["producer_status"]), ("held", "revision_requested"), result)
        self.assertEqual(result["review"]["decision"], "revise")
        row = self.db.execute("SELECT status,payload_json FROM rag_queue_job WHERE dedupe_key LIKE 'revision:%'").fetchone()
        self.assertEqual(row["status"], "blocked")
        self.assertIn("더 자연스럽게", json.loads(row["payload_json"])["revision_instructions"][0])
        self.assertEqual((self.generator.calls, self.reviewer.calls), (1, 1))

    def test_deterministic_blocker_overrides_model_pass(self):
        self.generator.remove_claims = True
        result = self.execute()
        self.assertEqual(result["outcome"], "held", result)
        self.assertNotEqual(result["review"]["decision"], "pass")
        self.assertTrue(any(item["blocking"] for item in result["review"]["issues"]))

    def test_missing_images_holds_without_generation_or_review(self):
        class NoImages:
            async def candidates_for(self, _evidence):
                return ()
        result = self.execute(images=NoImages())
        self.assertEqual(result["outcome"], "held")
        self.assertEqual((self.generator.calls, self.reviewer.calls), (0, 0))
        self.assertEqual(result["reason"], "waiting_for_distinct_work_linked_image_assets")

    def test_empty_vector_candidates_records_explicit_zero_without_embedding(self):
        class EmptyRetriever:
            query_embeddings = object()
            async def retrieve(self, request):
                return RetrievedEvidence("empty-retrieval", (), (), request.query,
                    request.content_type, request.menu_work_ids, request.seed_evidence_ids,
                    datetime.now(timezone.utc), backend="vector")
        result = self.execute(retriever=EmptyRetriever())
        self.assertEqual(result["outcome"], "held")
        self.assertEqual(result["usage_summary"]["llm_calls"], 0)
        self.assertEqual(result["cost_accounting"]["total_estimated_cost_usd"], "0")

    def test_disabled_by_default_and_mismatched_source_never_start(self):
        with self.assertRaisesRegex(BridgeError, "paid_generation_disabled"):
            self.execute(offline=False)
        with self.assertRaisesRegex(BridgeError, "seed_source_scope_mismatch"):
            self.execute(source_ids=("other_source",))
        self.assertEqual((self.generator.calls, self.reviewer.calls), (0, 0))

    def test_interrupted_attempt_is_not_automatically_restarted(self):
        self.execute()
        self.db.execute("UPDATE rag_celery_attempt SET state='running',result_json=NULL")
        self.db.commit()
        with self.assertRaisesRegex(BridgeError, "generation_may_have_started"):
            self.execute()
        self.assertEqual(self.generator.calls, 1)

    def test_concurrent_duplicate_claim_does_not_generate_twice(self):
        async def run():
            return await asyncio.gather(run_single_request(self.db, **self.kwargs),
                                        run_single_request(self.db, **self.kwargs), return_exceptions=True)
        results = asyncio.run(run())
        self.assertEqual(sum(isinstance(row, dict) and row["outcome"] == "ready_for_staging" for row in results), 1)
        self.assertTrue(any(isinstance(row, BridgeError) for row in results))
        self.assertEqual((self.generator.calls, self.reviewer.calls), (1, 1))


class UsageTests(unittest.TestCase):
    def test_only_explicit_pricing_computes_cost_from_provider_tokens(self):
        ledger = UsageLedger(pricing={"priced-model": {"input": "1", "cached_input": "0.1", "output": "2"}})
        ledger.record("generation", model="priced-model",
                      tokens={"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 30})
        self.assertEqual(ledger.records[0]["estimated_cost_usd"], "0.000142")
        self.assertTrue(ledger.summary()["complete"])
        ledger.record("review", model="unpriced-snapshot", tokens={"input_tokens": 10, "output_tokens": 2})
        self.assertIsNone(ledger.summary()["total_estimated_cost_usd"])

    def test_unreported_or_failed_model_cost_remains_unknown(self):
        class Model:
            async def generate_json(self, **kwargs):
                if kwargs.get("fail"):
                    raise ValueError("bad response")
                return {"model_authored_input_tokens": 999}
        ledger = UsageLedger()
        wrapped = RecordedModel(Model(), ledger, "generation")
        asyncio.run(wrapped.generate_json())
        with self.assertRaises(ValueError):
            asyncio.run(wrapped.generate_json(fail=True))
        self.assertEqual(len(ledger.records), 2)
        self.assertFalse(ledger.summary()["complete"])
        self.assertIsNone(ledger.summary()["total_estimated_cost_usd"])
        self.assertIsNone(ledger.records[0]["tokens"])

    def test_embedding_tokens_recorded_before_invalid_vector_rejected(self):
        def create(**kwargs):
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=11),
                data=[SimpleNamespace(embedding=[0.0] * 3)])
        ledger = UsageLedger()
        embedding = RecordedQueryEmbeddings(SimpleNamespace(embeddings=SimpleNamespace(create=create)), ledger)
        with self.assertRaisesRegex(BridgeError, "invalid_query_embedding"):
            asyncio.run(embedding.embed_query("query"))
        self.assertEqual(ledger.records[0]["tokens"], {"input_tokens": 11, "output_tokens": 0})
        self.assertIsNone(ledger.records[0]["estimated_cost_usd"])

    def test_provider_tokens_survive_invalid_json(self):
        ledger = UsageLedger()
        model = RecordingOpenAIModel.__new__(RecordingOpenAIModel)
        model.ledger, model.stage, model.model = ledger, "review", "model-under-test"
        async def create(**kwargs):
            return SimpleNamespace(output_text="invalid json", model="model-snapshot",
                usage=SimpleNamespace(input_tokens=20, output_tokens=4, input_tokens_details=None))
        model.client = SimpleNamespace(responses=SimpleNamespace(create=create))
        wrapped = RecordedModel(model, ledger, "review")
        with self.assertRaises(json.JSONDecodeError):
            asyncio.run(wrapped.generate_json(system_prompt="review", user_payload={}, schema_name="test", schema={}))
        self.assertEqual(len(ledger.records), 1)
        self.assertEqual(ledger.records[0]["tokens"], {"input_tokens": 20, "output_tokens": 4})
        self.assertFalse(ledger.summary()["complete"])


if __name__ == "__main__":
    unittest.main()
