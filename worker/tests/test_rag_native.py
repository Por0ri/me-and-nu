"""Native callback composition uses isolated snapshots and injected services only."""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from menu_worker.rag_contract import RagContractError, RagRequest
from menu_worker.rag_native import RagNativeConfig, build_native_callbacks, _GuardedModel, _GuardedEmbeddings


class RagNativeTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[2] / "agents/ani_rag/tests/test_celery_bridge.py"
        spec = importlib.util.spec_from_file_location("_rag_bridge_fixtures", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.fixture = module.CeleryBridgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        root = self.fixture.root
        self.config = RagNativeConfig(str(root), str(root / "isolated.sqlite3"), str(root),
            str(root / "unused-key"), str(root / "unused-vector-dsn"), "menu_rag_vector_test", [1],
            enabled=True, confirm_isolated_copy=True, offline=True)
        request = RagRequest.parse({"namespace": "fixture-corpus", "work_id": "stable-source-topic",
            "menu_work_id": 1, "content_type": "guide", "query": "두 친구의 여행",
            "seed_evidence": [{"document_id": self.fixture.document.document_id,
                               "content_sha256": self.fixture.document.content_hash}],
            "allowed_source_ids": ["fixture_source"]})
        self.job = {"job_id": str(uuid4()), "generation_attempt_id": str(uuid4()),
                    "request": request.as_dict(), "input_sha256": request.input_sha256}
        self.factory_calls = 0

    @contextmanager
    def runtime(self, config, connection, pulse):
        from ani_rag.storage.sqlite_core import SQLiteRagStore
        from ani_rag.storage.sqlite_workflow import SQLiteImageReader
        self.factory_calls += 1
        yield {"retriever": self.fixture.retriever, "images": SQLiteImageReader(SQLiteRagStore(connection)),
               "generation_service": self.fixture.generator, "reviewer": self.fixture.reviewer}

    def callbacks(self, config=None):
        return build_native_callbacks(config or self.config, runtime_factory=self.runtime)

    def seed_with_two_excerpts(self):
        from ani_rag.models import EvidenceExcerpt
        document = replace(self.fixture.document, document_id="multi-excerpt",
            source_url="https://example.test/quoted-story", excerpts=(
                EvidenceExcerpt("길을 떠나는 두 친구.", "opening"),
                EvidenceExcerpt("여행의 마지막 약속.", "ending")))
        asyncio.run(self.fixture.store.upsert_and_schedule_generation(document, schedule_generation=False))
        request = RagRequest.parse({**self.job["request"], "seed_evidence": [
            {"document_id": document.document_id, "content_sha256": document.content_hash}]})
        return document, {**self.job, "request": request.as_dict(), "input_sha256": request.input_sha256}

    def test_two_excerpts_pass_preflight_and_export_in_source_order(self):
        document, job = self.seed_with_two_excerpts()
        rows = self.fixture.db.execute(
            "SELECT locator FROM rag_excerpt WHERE document_id=? ORDER BY excerpt_id", (document.document_id,))
        self.assertEqual([row[0] for row in rows], ["ending", "opening"])

        trace = asyncio.run(self.fixture.retriever.retrieve(self.fixture.request))
        trace = replace(trace, documents=(document,), seed_evidence_ids=(document.document_id,),
                        chunks=(replace(self.fixture.chunk, evidence_id=document.document_id,
                                        source_url=document.source_url),))
        preflight, run = self.callbacks()
        self.assertEqual(preflight(job)["provider_calls"], 0)
        with patch.object(self.fixture.retriever, "retrieve", return_value=trace):
            result = asyncio.run(run(job, lambda: None))
        self.assertEqual(result["outcome"], "ready_for_staging", result.get("reason"))
        exported = result["evidence"][document.document_id]["excerpts"]
        self.assertEqual([(item["locator"], item["text"]) for item in exported],
                         [(item.locator, item.text) for item in document.excerpts])

    def assert_preflight_rejects_changed_excerpt(self, mutation):
        document, job = self.seed_with_two_excerpts()
        self.fixture.db.execute(mutation + " WHERE document_id=? AND locator='opening'", (document.document_id,))
        self.fixture.db.commit()
        with self.assertRaisesRegex(RagContractError, "rag_evidence_content_changed"):
            self.callbacks()[0](job)
        self.assertEqual((self.factory_calls, self.fixture.generator.calls), (0, 0))

    def test_changed_excerpt_text_blocks_preflight(self):
        self.assert_preflight_rejects_changed_excerpt("UPDATE rag_excerpt SET excerpt_text='replaced'")

    def test_missing_excerpt_blocks_preflight(self):
        self.assert_preflight_rejects_changed_excerpt("DELETE FROM rag_excerpt")

    def test_unknown_excerpt_id_blocks_preflight(self):
        self.assert_preflight_rejects_changed_excerpt("UPDATE rag_excerpt SET excerpt_id='unknown'")

    def test_reordered_export_is_rejected_after_generation(self):
        document, job = self.seed_with_two_excerpts()
        exported = asdict(document)
        exported["excerpts"] = list(reversed(exported["excerpts"]))
        response = {"outcome": "ready_for_staging", "evidence": {document.document_id: exported}}
        with patch("ani_rag.celery_bridge.run_single_request", return_value=response):
            result = asyncio.run(self.callbacks()[1](job, lambda: None))
        self.assertEqual((result["outcome"], result["reason"]), ("failed", "rag_result_evidence_content_changed"))

    def test_preflight_has_no_db_writes_and_native_output_preserves_seed(self):
        before = hashlib.sha256(Path(self.config.database_path).read_bytes()).hexdigest()
        preflight, run = self.callbacks()
        report = preflight(self.job)
        self.assertEqual(report["provider_calls"], 0)
        self.assertEqual(report["database_writes"], 0)
        self.assertEqual(hashlib.sha256(Path(self.config.database_path).read_bytes()).hexdigest(), before)
        pulses = []
        result = asyncio.run(run(self.job, lambda: pulses.append(True)))
        self.assertEqual(result["outcome"], "ready_for_staging", result.get("reason"))
        self.assertTrue(result["simulated"])
        self.assertEqual(result["external_input_sha256"], self.job["input_sha256"])
        self.assertEqual(result["evidence"]["original-evidence"]["body"], self.fixture.document.body)
        self.assertGreaterEqual(len(pulses), 2)
        self.assertEqual(self.factory_calls, 1)

    def test_disabled_callbacks_do_not_open_database_or_read_keys(self):
        config = replace(self.config, enabled=False, database_path="does-not-exist")
        preflight, run = self.callbacks(config)
        with patch("menu_worker.rag_native.sqlite3.connect", side_effect=AssertionError("DB opened")), \
                patch("pathlib.Path.read_text", side_effect=AssertionError("key read")):
            with self.assertRaisesRegex(RagContractError, "rag_generation_disabled"):
                preflight(self.job)
            with self.assertRaisesRegex(RagContractError, "rag_generation_disabled"):
                asyncio.run(run(self.job, lambda: None))
        self.assertEqual(self.factory_calls, 0)

    def test_missing_paid_rates_are_blocked_before_opening_source_or_credentials(self):
        config = replace(self.config, offline=False)
        preflight, _ = build_native_callbacks(config)
        with patch("menu_worker.rag_native.sqlite3.connect", side_effect=AssertionError("DB opened")), \
                patch("pathlib.Path.read_text", side_effect=AssertionError("key read")):
            with self.assertRaisesRegex(RagContractError, "rag_model_pricing_required"):
                preflight(self.job)

    def test_explicit_rates_preflight_has_no_provider_calls(self):
        # Synthetic rates validate the contract; these are not production prices.
        rates = {"input": "0.1", "output": "0.2", "cached_input": "0.01"}
        config = replace(self.config, offline=False, pricing={
            self.config.model: rates, "text-embedding-3-small": {**rates, "output": "0"}})
        preflight, _ = build_native_callbacks(config)
        with patch("pathlib.Path.read_text", side_effect=AssertionError("key read")):
            self.assertEqual(preflight(self.job)["provider_calls"], 0)
        for invalid in ("NaN", "Infinity", "-1", "0"):
            broken = replace(config, pricing={**config.pricing, config.model: {**rates, "input": invalid}})
            with self.subTest(invalid=invalid), self.assertRaisesRegex(RagContractError, "invalid_rag_model_pricing"):
                build_native_callbacks(broken)[0](self.job)

    def test_unused_seed_is_preserved_without_forcing_it_into_exported_claims(self):
        extra = replace(self.fixture.document, document_id="unused-seed", source_url="https://example.test/unused")
        asyncio.run(self.fixture.store.upsert_and_schedule_generation(extra, schedule_generation=False))
        request = RagRequest.parse({**self.job["request"], "seed_evidence": [
            *self.job["request"]["seed_evidence"],
            {"document_id": extra.document_id, "content_sha256": extra.content_hash}]})
        job = {**self.job, "request": request.as_dict(), "input_sha256": request.input_sha256}
        result = asyncio.run(self.callbacks()[1](job, lambda: None))
        self.assertEqual(result["outcome"], "ready_for_staging", result.get("reason"))
        self.assertNotIn("unused-seed", result["evidence"])
        self.assertIn("unused-seed", result["request"]["seed_evidence_ids"])
        self.assertEqual(self.fixture.db.execute("SELECT content_hash FROM rag_document WHERE document_id='unused-seed'").fetchone()[0], extra.content_hash)

    def test_stored_hash_cannot_hide_modified_source_body(self):
        self.fixture.db.execute("UPDATE rag_document SET raw_text='changed body' WHERE document_id='original-evidence'")
        self.fixture.db.commit()
        preflight, run = self.callbacks()
        with self.assertRaisesRegex(RagContractError, "rag_evidence_content_changed"):
            preflight(self.job)
        with self.assertRaisesRegex(RagContractError, "rag_evidence_content_changed"):
            asyncio.run(run(self.job, lambda: None))
        self.assertEqual((self.factory_calls, self.fixture.generator.calls), (0, 0))

    def test_request_source_work_type_or_seed_hash_changes_are_blocked(self):
        for fields, expected in (
            ({"allowed_source_ids": ["other"]}, "rag_evidence_source_mismatch"),
            ({"menu_work_id": 2}, "rag_work_not_in_service_catalog"),
            ({"content_type": "people"}, "rag_evidence_type_mismatch"),
            ({"seed_evidence": [{"document_id": "original-evidence", "content_sha256": "0" * 64}]}, "rag_seed_hash_changed"),
        ):
            request = RagRequest.parse({**self.job["request"], **fields})
            job = {**self.job, "request": request.as_dict(), "input_sha256": request.input_sha256}
            with self.subTest(fields=fields), self.assertRaisesRegex(RagContractError, expected):
                self.callbacks()[0](job)

    def test_changed_native_evidence_is_failure_and_usage_survives(self):
        from ani_rag.celery_bridge import run_single_request
        async def changed(*args, **kwargs):
            result = await run_single_request(*args, **kwargs)
            result["evidence"]["original-evidence"]["body"] = "tampered"
            result["usage_records"] = [{"stage": "review", "estimated_cost_usd": None}]
            result["cost_accounting"]["complete"] = False
            return result
        with patch("ani_rag.celery_bridge.run_single_request", side_effect=changed):
            result = asyncio.run(self.callbacks()[1](self.job, lambda: None))
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["reason"], "rag_result_evidence_content_changed")
        self.assertIsNone(result["usage_records"][0]["estimated_cost_usd"])

    def test_lost_guard_after_completion_returns_usage_instead_of_discarding_it(self):
        calls = []
        def pulse():
            calls.append(True)
            if len(calls) > 1:
                raise RuntimeError("lease lost")
        result = asyncio.run(self.callbacks()[1](self.job, pulse))
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("usage_records", result)
        self.assertIn("cost_accounting", result)


class GuardedProviderTests(unittest.TestCase):
    def test_lost_guard_blocks_each_new_model_and_embedding_call(self):
        class Model:
            calls = 0
            async def generate_json(self, **kwargs):
                self.calls += 1
                return {}
            async def generate_json_with_images(self, **kwargs):
                self.calls += 1
                return {}
            async def embed_query(self, text):
                self.calls += 1
                return [0.0] * 1024
        model = Model()
        def lost():
            raise RuntimeError("lease lost")
        for call in (_GuardedModel(model, lost).generate_json(),
                     _GuardedModel(model, lost).generate_json_with_images(),
                     _GuardedEmbeddings(model, lost).embed_query("query")):
            with self.assertRaisesRegex(RuntimeError, "lease lost"):
                asyncio.run(call)
        self.assertEqual(model.calls, 0)


if __name__ == "__main__":
    unittest.main()
