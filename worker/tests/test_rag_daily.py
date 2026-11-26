"""Mixed-producer quota tests: injected jobs only, no provider or service DB."""
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4
import unittest

import test_daily_count_runtime as count_fixture
from menu_worker.bounded_generation import StopRun
from menu_worker.bounded_resume import digest
from menu_worker.rag_daily import MixedDailyController, pin_scope, validate_scope


class RagStoreFixture:
    def __init__(self):
        self.rows, self.published = {}, []
        self.outcome = "ready_for_staging"
        self.incomplete = False

    def submit(self, request, receipt):
        for row in self.rows.values():
            if row["input_sha256"] == request.input_sha256:
                return deepcopy(row) | {"created": False}
        row = dict(job_id=str(uuid4()), generation_attempt_id=str(uuid4()), result_sha256="a" * 64,
                   input_sha256=request.input_sha256, admission_receipt=receipt,
                   execution_status="queued", outcome=self.outcome)
        self.rows[row["job_id"]] = row
        return deepcopy(row) | {"created": True}

    def get(self, key):
        return deepcopy(self.rows.get(key))

    def publish(self, key):
        self.published.append(key)
        self.rows[key]["execution_status"] = "succeeded"

    def result(self, job):
        return {"producer": "ani_rag", "usage_records": [{"estimated_cost_usd": "0.02"}],
                "cost_accounting": {"complete": not self.incomplete}}

    def export(self, job, directory):
        return {key: job[key] for key in ("job_id", "generation_attempt_id", "result_sha256")} | {
            "producer": "ani_rag", "bundle_path": str(directory / "native-rag.json")}


class MixedDailyTests(unittest.TestCase):
    def setUp(self):
        self.base = count_fixture.DailyCountRuntimeTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.f = self.base.f
        self.rag = RagStoreFixture()
        self.scope = {"schema_version": 1, "purpose": "rag_daily_generation", "routes": [
            {"namespace": "dictionary", "menu_work_id": work, "content_type": kind,
             "allowed_source_ids": ["kitsu_anime_catalog"]}
            for work in (8, 2) for kind in ("character", "guide")]}
        pin_scope(self.f.controller(), self.scope, reviewed_sha256=digest(self.scope))

    def discover(self, route, limit, *, excluded_document_ids):
        return [{**route, "work_id": f"work:{route['menu_work_id']}:{route['content_type']}:{i}",
                 "query": f"Evidence topic {i}", "seed_evidence": [
                     {"document_id": f"doc:{route['menu_work_id']}:{route['content_type']}:{i}",
                      "content_sha256": f"{i:064x}"}]}
                for i in range(limit)
                if f"doc:{route['menu_work_id']}:{route['content_type']}:{i}" not in excluded_document_ids]

    def controller(self):
        old = self.f.controller()
        return MixedDailyController(config=old.config, checkpoint=old.path, store=old.store,
            discover=old.discover, validate=old.validate, publish=old.publish, read_result=old.read_result,
            export=old.export, now=old.now, sleep=old.sleep, lock_check=old.lock_check,
            reservations=old.reservations, rag_store=self.rag, rag_discover=self.discover,
            rag_publish=self.rag.publish, rag_read_result=self.rag.result, rag_export=self.rag.export,
            rag_scope_sha256=digest(self.scope))

    def test_mixed_successes_stop_at_twenty_and_preserve_old_history(self):
        controller = self.controller()
        history = deepcopy(controller.state["records"])
        report = controller.run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (20, 20))
        self.assertGreater(len(self.rag.published), 0)
        self.assertGreater(len(self.f.published), 0)
        self.assertEqual(len(self.rag.published) + len(self.f.published), 20)
        self.assertEqual(report["daily_deliveries"], 20)
        self.assertEqual(sum(row["successes"] for row in report["rag_route_allocation"]), len(self.rag.published))
        self.assertEqual(controller.state["records"][:len(history)], history)
        self.assertFalse(report["automatic_service_publication"])
        self.assertEqual(self.f.f.source.read_bytes(), self.f.f.original)

    def test_mixed_holds_and_skips_share_forty_attempts(self):
        self.f.f.store.outcomes = ["held"] * 80
        self.rag.outcome = "held"
        report = self.controller().run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (40, 0))
        self.assertEqual(len(self.rag.published) + len(self.f.published), 40)
        ordinals = [row["admission_receipt"]["attempt_ordinal"] for row in self.rag.rows.values()]
        self.assertTrue(all(1 <= value <= 40 for value in ordinals))
        self.assertEqual(len(ordinals), len(set(ordinals)))

    def test_restart_does_not_republish_or_reset_counts(self):
        first = self.controller().run()
        before = list(self.rag.published), list(self.f.published)
        second = self.controller().run()
        self.assertEqual(first["daily_attempts"], second["daily_attempts"])
        self.assertEqual((self.rag.published, self.f.published), before)

    def test_unknown_usage_stops_and_keeps_reserved_attempt(self):
        self.rag.incomplete = True
        controller = self.controller()
        with self.assertRaisesRegex(StopRun, "rag_usage_requires_reconciliation"):
            controller.run()
        row = controller.state["records"][-1]
        self.assertTrue(row["counts_slot"])
        self.assertTrue(row["rag_cost_incomplete"])
        self.assertIsNone(row["estimated_cost_usd"])
        self.assertNotIn("rag_delivery", row)
        before = list(self.rag.published)
        self.controller().run()
        self.assertEqual(self.rag.published, before)

    def test_period_only_controller_cannot_consume_mixed_checkpoint(self):
        with self.assertRaisesRegex(StopRun, "mixed_checkpoint_requires_rag_controller"):
            self.f.controller().run()

    def test_failed_job_preserves_known_paid_usage_and_attempt(self):
        controller = self.controller()
        record = controller.reserve_rag(controller.collect_rag()[0])
        def fail(job_id):
            self.rag.published.append(job_id)
            self.rag.rows[job_id].update(execution_status="failed", outcome=None,
                cost_complete=True, usage_records=[{"estimated_cost_usd": "0.03"}])
        controller.rag_publish = fail
        with self.assertRaisesRegex(StopRun, "rag_generation_requires_attention"):
            controller.handle(record)
        self.assertEqual(Decimal(record["estimated_cost_usd"]), Decimal("0.03"))
        self.assertEqual(record["rag_usage_records"], [{"estimated_cost_usd": "0.03"}])
        self.assertTrue(record["counts_slot"])
        self.assertNotIn("rag_delivery", record)
        self.assertNotIn(record["job_id"], controller.unknown_cost_jobs())

    def test_failed_unknown_usage_preserves_partial_cost_without_republish(self):
        controller = self.controller()
        baseline = controller.known_spent()
        record = controller.reserve_rag(controller.collect_rag()[0])
        usage = [{"estimated_cost_usd": "0.03"}, {"estimated_cost_usd": None}]
        def fail(job_id):
            self.rag.published.append(job_id)
            self.rag.rows[job_id].update(execution_status="needs_attention", outcome=None,
                cost_complete=False, usage_records=deepcopy(usage))
        controller.rag_publish = fail
        with self.assertRaisesRegex(StopRun, "rag_usage_requires_reconciliation"):
            controller.handle(record)
        self.assertEqual(record["rag_usage_records"], usage)
        self.assertEqual(controller.known_spent(), baseline + Decimal("0.03"))
        self.assertIn(record["job_id"], controller.unknown_cost_jobs())
        self.assertIsNone(controller.summary()["estimated_total_cost_usd"])
        self.controller().run()
        self.assertEqual(self.rag.published, [record["job_id"]])

    def test_preflight_failure_records_explicit_zero_without_delivery(self):
        controller = self.controller()
        record = controller.reserve_rag(controller.collect_rag()[0])
        def fail(job_id):
            self.rag.rows[job_id].update(execution_status="failed", outcome=None,
                cost_complete=True, usage_records=[])
        controller.rag_publish = fail
        with self.assertRaisesRegex(StopRun, "rag_generation_requires_attention"):
            controller.handle(record)
        self.assertEqual(Decimal(record["estimated_cost_usd"]), Decimal(0))
        self.assertNotIn("rag_delivery", record)

    def test_damaged_result_seal_keeps_ledger_usage_and_unknown_total(self):
        controller = self.controller()
        baseline = controller.known_spent()
        record = controller.reserve_rag(controller.collect_rag()[0])
        publish = controller.rag_publish
        def with_usage(job_id):
            publish(job_id)
            self.rag.rows[job_id].update(cost_complete=True,
                                       usage_records=[{"estimated_cost_usd": "0.05"}])
        def damaged(job):
            raise ValueError("rag_artifact_changed")
        controller.rag_publish, controller.rag_read_result = with_usage, damaged
        with self.assertRaisesRegex(StopRun, "rag_usage_requires_reconciliation"):
            controller.handle(record)
        self.assertTrue(record["rag_result_verification_failed"])
        self.assertEqual(controller.known_spent(), baseline + Decimal("0.05"))
        self.assertIn(record["job_id"], controller.unknown_cost_jobs())
        self.assertIsNone(controller.summary()["estimated_total_cost_usd"])
        self.assertNotIn("rag_delivery", record)

    def test_reservation_rejects_candidates_outside_reviewed_scope(self):
        controller = self.controller()
        fields = controller.collect_rag()[0]
        fields["rag_request"]["allowed_source_ids"] = ["unreviewed_source"]
        with self.assertRaisesRegex(StopRun, "rag_discovery_scope_mismatch"):
            controller.reserve_rag(fields)

    def test_calendar_boundary_prevents_both_producers(self):
        controller = self.controller()
        self.f.f.now = controller.deadline
        with self.assertRaisesRegex(StopRun, "deadline_reached"):
            controller.run()
        self.assertEqual((self.rag.published, self.f.published), ([], []))

    def test_changed_scope_cannot_be_silently_adopted(self):
        self.scope["routes"][0]["menu_work_id"] = 99
        with self.assertRaisesRegex(StopRun, "rag_daily_scope_changed"):
            self.controller()

    def test_new_scope_rejects_unregistered_service_work(self):
        scope = deepcopy(self.scope)
        scope["routes"][0]["menu_work_id"] = 9223372036854775807
        with self.assertRaisesRegex(StopRun, "rag_work_not_in_service_catalog"):
            validate_scope(scope, current_catalog=True)
