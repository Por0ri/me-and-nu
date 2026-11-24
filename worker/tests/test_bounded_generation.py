import copy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from menu_worker.bounded_generation import APPROVED_RESERVATION, Controller, StopRun, audit_existing_jobs, result_cost, validate_reservations, write_json


class FakeStore:
    def __init__(self):
        self.jobs = {}
        self.published = []
        self.submitted = []
        self.duplicate = None
        self.outcomes = []
        self.defer_completion = False

    def submit(self, request, job_kind):
        self.submitted.append(copy.deepcopy(request))
        if self.duplicate:
            return {**self.duplicate, "created": False}
        job_id = str(len(self.jobs) + 1)
        outcome = self.outcomes.pop(0) if self.outcomes else "ready_for_staging"
        job = {"job_id": job_id, "execution_status": "queued", "last_task_id": None,
               "outcome": outcome, "producer_status": "ready_for_feed", "result_sha256": "a" * 64,
               "generation_attempt_id": "attempt-" + job_id, "generation_started_at": None, "attempts": []}
        self.jobs[job_id] = job
        return {**job, "created": True}

    def publish_pending(self, job_id, publish, expected_kind):
        self.published.append(job_id)
        assert expected_kind == "anime_generate"
        self.jobs[job_id]["last_task_id"] = publish(job_id)
        if not self.defer_completion:
            self.jobs[job_id]["execution_status"] = "succeeded"
        return True

    def get(self, job_id):
        return copy.deepcopy(self.jobs.get(job_id))


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 16, 5, tzinfo=timezone.utc).timestamp()

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "checkpoint.json"
        self.clock = Clock()
        self.store = FakeStore()
        self.exported = []
        self.discovered = []
        self.cost = "0.01"
        self.config = {"run_id": "beta-open-20261116", "source_id": "one_piece_official", "content_type": "goods",
            "total_budget_usd": "5.00", "baseline_count": 1, "baseline_estimated_cost_usd": "0.01178341",
            "max_new_jobs": 4, "next_job_reserve_usd": "0.04", "job_timeout_seconds": 1500,
            "deadline": "2026-11-16T17:00:00+09:00", "scan_interval_seconds": 60,
            "artifact_root": str(self.root), "bundle_root": str(self.root / "bundles"),
            "excluded_urls": ["https://one-piece.com/news/83393/index.html"]}
        self.articles = [self.article(n) for n in range(83393, 83402)]

    def article(self, number):
        return {"source_id": "one_piece_official", "source_url": f"https://one-piece.com/news/{number}/index.html",
                "canonical_url": f"https://one-piece.com/news/{number}/index.html", "content_type": "goods", "menu_work_id": 8}

    def discover(self, source, kind, limit):
        self.discovered.append((source, kind, limit))
        return {"articles": copy.deepcopy(self.articles), "discovered": len(self.articles)}

    def result(self, job):
        return {"usage_records": [{"estimated_cost_usd": self.cost}],
                "usage_summary": {"llm_calls": 1, "estimated_cost_usd": self.cost}}

    def export(self, job_id, destination):
        self.exported.append(job_id)
        destination.mkdir(parents=True)
        file = destination / "bundle.json"
        file.write_text("{}")
        return {"bundle_path": str(file)}

    def controller(self, **overrides):
        args = dict(config=self.config, checkpoint=self.path, store=self.store, discover=self.discover,
                    validate=lambda fields: (fields["canonical_url"], fields["canonical_url"].split("/")[-2]),
                    publish=lambda job_id: "task-" + job_id, read_result=self.result, export=self.export,
                    now=self.clock.now, sleep=self.clock.sleep)
        args.update(overrides)
        return Controller(**args)

    def test_four_new_slots_and_excluded_baseline_finish_without_redis_duplicates(self):
        report = self.controller().run()
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["new_slots_consumed"], 4)
        self.assertEqual(report["estimated_total_cost_usd"], "0.05178341")
        self.assertEqual(len(self.store.published), 4)
        self.assertEqual(self.discovered, [("one_piece_official", "goods", 20)])
        self.assertNotIn(self.config["excluded_urls"][0], [r["canonical_url"] for r in self.store.submitted])
        self.controller().run()
        self.assertEqual(len(self.store.published), 4)

    def test_rejected_held_skipped_preserved_and_only_ready_exported(self):
        self.store.outcomes = ["rejected", "held", "skipped", "ready_for_staging"]
        controller = self.controller()
        controller.run()
        self.assertEqual(self.exported, ["4"])
        self.assertEqual([r["outcome"] for r in controller.state["records"]], self.store.outcomes or ["rejected", "held", "skipped", "ready_for_staging"])

    def test_terminal_duplicate_does_not_consume_slot(self):
        original = self.store.submit
        duplicate = {"job_id": "old", "execution_status": "succeeded"}
        def once(request, job_kind):
            if request["canonical_url"].endswith("83394/index.html"):
                return {**duplicate, "created": False}
            return original(request, job_kind)
        self.store.submit = once
        controller = self.controller()
        self.assertEqual(controller.run()["new_slots_consumed"], 4)
        self.assertEqual(len(controller.state["records"]), 5)
        self.assertEqual(len(self.store.published), 4)

    def test_foreign_queued_duplicate_stops_without_publication(self):
        self.store.duplicate = {"job_id": "foreign", "execution_status": "queued"}
        with self.assertRaisesRegex(StopRun, "foreign_or_uncheckpointed_pending_job"):
            self.controller().run()
        self.assertEqual(self.store.published, [])

    def test_crash_after_publication_resumes_owned_job_without_republishing(self):
        controller = self.controller()
        real_read = controller.read_result
        def crash(job):
            raise SystemExit("process lost")
        controller.read_result = crash
        with self.assertRaises(SystemExit):
            controller.run()
        self.assertEqual(self.store.published, ["1"])
        restarted = self.controller(read_result=real_read)
        restarted.run()
        self.assertEqual(self.store.published, ["1", "2", "3", "4"])

    def test_crash_before_broker_after_intent_never_republishes(self):
        def crashed(*args, **kwargs):
            raise SystemExit("process lost")
        self.store.publish_pending = crashed
        with self.assertRaises(SystemExit):
            self.controller().run()
        with self.assertRaisesRegex(StopRun, "ambiguous_delivery_no_republish"):
            self.controller().run()
        self.assertEqual(self.store.published, [])

    def test_missing_cost_needs_attention_and_timeout_stop_after_first_slot(self):
        with self.assertRaisesRegex(StopRun, "missing_cost_evidence"):
            self.controller(read_result=lambda job: {}).run()
        self.assertEqual(self.store.published, ["1"])
        self.assertEqual(self.controller().run()["status"], "stopped")

    def test_needs_attention_stops(self):
        original = self.store.publish_pending
        def attention(*args, **kwargs):
            result = original(*args, **kwargs)
            self.store.jobs[args[0]]["execution_status"] = "needs_attention"
            return result
        self.store.publish_pending = attention
        with self.assertRaisesRegex(StopRun, "job_needs_attention"):
            self.controller().run()
        self.assertEqual(self.store.published, ["1"])

    def test_job_timeout_stops_without_next_dispatch(self):
        self.store.defer_completion = True
        with self.assertRaisesRegex(StopRun, "job_wait_timeout"):
            self.controller().run()
        self.assertEqual(self.store.published, ["1"])

    def test_budget_guard_stops_between_jobs(self):
        self.cost = "4.96"
        with self.assertRaisesRegex(StopRun, "estimated_budget_reserve_unavailable"):
            self.controller().run()
        self.assertEqual(self.store.published, ["1"])

    def test_single_job_overrun_is_recorded_and_stops(self):
        self.cost = "5.01"
        with self.assertRaisesRegex(StopRun, "estimated_budget_target_exceeded"):
            self.controller().run()
        self.assertEqual(self.store.published, ["1"])
        self.assertEqual(self.exported, [])

    def test_no_new_job_without_full_timeout_window(self):
        self.clock.value = datetime(2026, 11, 16, 7, 40, tzinfo=timezone.utc).timestamp()
        with self.assertRaisesRegex(StopRun, "insufficient_time_for_new_job"):
            self.controller().run()
        self.assertEqual(self.store.published, [])

    def test_config_change_cannot_reset_day_count(self):
        self.controller()
        self.config["max_new_jobs"] = 10
        with self.assertRaisesRegex(StopRun, "checkpoint_configuration_mismatch"):
            self.controller()

    def test_duplicate_json_is_rejected(self):
        self.path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(StopRun, "duplicate_json_key"):
            self.controller()


class CostTests(unittest.TestCase):
    def test_explicit_no_call_evidence_is_zero(self):
        self.assertEqual(result_cost({"usage_records": [], "usage_summary": {"llm_calls": 0, "total_tokens": 0}}), Decimal(0))

    def test_missing_negative_nan_inconsistent_evidence_stops(self):
        for result in ({}, {"usage_records": []}, {"usage_records": [{"estimated_cost_usd": -1}]},
                       {"usage_records": [{"estimated_cost_usd": "NaN"}]},
                       {"usage_records": [{"estimated_cost_usd": ".1"}], "usage_summary": {"estimated_cost_usd": ".2"}},
                       {"usage_records": [], "usage_summary": {"llm_calls": 0, "total_tokens": 2}}):
            with self.subTest(result=result), self.assertRaises(StopRun):
                result_cost(result)


class DatabaseAuditTests(unittest.TestCase):
    def audit(self, rows, records):
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, query): return self
            def fetchall(self): return rows
        class Store:
            def _connect(self): return Connection()
        audit_existing_jobs(Store(), {"config": {"run_id": "beta-open-20261116", "max_new_jobs": 4}, "records": records})

    def test_deleted_checkpoint_cannot_reset_database_count(self):
        with self.assertRaisesRegex(StopRun, "database_history_not_owned_by_checkpoint"):
            self.audit([{"job_id": "j1", "idempotency_key": "beta-open-20261116:key"}], [])

    def test_owned_history_matches_and_foreign_or_overcap_blocks(self):
        record = {"job_id": "j1", "request": {"idempotency_key": "beta-open-20261116:key"}}
        row = {"job_id": "j1", "idempotency_key": "beta-open-20261116:key"}
        self.audit([row], [record])
        with self.assertRaisesRegex(StopRun, "database_job_cap_already_exceeded"):
            self.audit([row] * 5, [record])
        with self.assertRaisesRegex(StopRun, "checkpoint_job_missing_from_database"):
            self.audit([], [record])


class ReservationTests(unittest.TestCase):
    """Exercise receipt recovery using the real bound identity and synthetic jobs."""
    setUp = ControllerTests.setUp
    article = ControllerTests.article
    discover = ControllerTests.discover
    result = ControllerTests.result
    export = ControllerTests.export
    controller = ControllerTests.controller
    def receipt(self):
        return {"schema_version": 1, "records": [{**APPROVED_RESERVATION,
            "accounting_method": "reserve", "measured_cost": None, "reserved_usd": "1.00",
            "reason": "Extraction usage omitted at write_bound_skip; reservation is not measured cost."}]}

    def stopped_controller(self):
        controller = self.controller(reservations=validate_reservations(self.receipt()))
        first_request, second_request = self.article(83394), self.article(83388)
        first_request["idempotency_key"] = "beta-open-20261116:first"
        second_request["idempotency_key"] = "beta-open-20261116:second"
        first = {"request": first_request, "canonical_url": first_request["canonical_url"], "state": "complete",
                 "job_id": "1", "counts_slot": True, "reserved_at": self.clock.now(),
                 "estimated_cost_usd": "0.00772830", "publish_intent": True, "published": True}
        second = {"request": second_request, "canonical_url": second_request["canonical_url"], "state": "reserved",
                  "job_id": APPROVED_RESERVATION["job_id"], "counts_slot": True, "reserved_at": self.clock.now(),
                  "publish_intent": True, "published": True, "execution_status": "succeeded", "outcome": "skipped",
                  "producer_status": "content_type_mismatch_skipped"}
        self.store.jobs = {"1": {"job_id": "1", "execution_status": "succeeded"}, second["job_id"]: {
            "job_id": second["job_id"], "generation_attempt_id": APPROVED_RESERVATION["generation_attempt_id"],
            "result_sha256": APPROVED_RESERVATION["source_result_sha256"], "execution_status": "succeeded",
            "outcome": "skipped", "producer_status": APPROVED_RESERVATION["producer_status"]}}
        controller.state.update(status="stopped", stop_reason="missing_cost_evidence", records=[first, second])
        controller.state["seen"] += [first["canonical_url"], second["canonical_url"]]
        controller.read_result = self.reservation_result
        controller.save()
        return controller

    def reservation_result(self, job):
        if job["job_id"] == APPROVED_RESERVATION["job_id"]:
            return {"status": "content_type_mismatch_skipped"}
        return self.result(job)

    def test_receipt_resumes_only_remaining_two_slots_and_separates_budget(self):
        controller = self.stopped_controller()
        original_job = copy.deepcopy(self.store.jobs[APPROVED_RESERVATION["job_id"]])
        controller.resume_after_accounting()
        report = controller.run()
        self.assertEqual(report["new_slots_consumed"], 4)
        self.assertEqual(self.store.published, ["3", "4"])
        self.assertEqual(report["estimated_total_cost_usd"], "0.03951171")
        self.assertEqual(report["reserved_cost_usd"], "1.00")
        self.assertEqual(report["budget_accounted_total_usd"], "1.03951171")
        second = controller.state["records"][1]
        self.assertIsNone(second["estimated_cost_usd"])
        self.assertIsNone(second["measured_cost"])
        self.assertEqual(second["accounting_method"], "reserve")
        self.assertEqual(len(controller.state["accounting_resume_audit"]), 1)
        self.assertEqual(self.store.jobs[APPROVED_RESERVATION["job_id"]], original_job)
        restarted = self.controller(reservations=validate_reservations(self.receipt()), read_result=self.reservation_result)
        restarted.resume_after_accounting()
        self.assertEqual(restarted.run(), report)
        self.assertEqual(self.store.published, ["3", "4"])
        self.assertEqual(len(restarted.state["accounting_resume_audit"]), 1)

    def test_running_restart_after_resume_is_idempotent(self):
        controller = self.stopped_controller()
        controller.resume_after_accounting()
        restarted = self.controller(reservations=validate_reservations(self.receipt()), read_result=self.reservation_result)
        restarted.resume_after_accounting()
        restarted.run()
        self.assertEqual(self.store.published, ["3", "4"])
        self.assertEqual(len(restarted.state["accounting_resume_audit"]), 1)

    def test_wrong_sha_attempt_status_bool_negative_or_measured_claim_rejected(self):
        for key, value in (("source_result_sha256", "0" * 64), ("generation_attempt_id", "wrong"),
                           ("producer_status", "ready_for_feed"), ("reserved_usd", True),
                           ("reserved_usd", "-1"), ("reserved_usd", "0"), ("reserved_usd", "5.00"),
                           ("measured_cost", "1.00"), ("measured_cost", 0), ("accounting_method", "measured")):
            document = self.receipt()
            document["records"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(StopRun):
                validate_reservations(document)
        document = self.receipt()
        document["schema_version"] = True
        with self.assertRaises(StopRun):
            validate_reservations(document)

    def test_sealed_source_mismatch_never_changes_checkpoint(self):
        controller = self.stopped_controller()
        saved = self.path.read_bytes()
        for key in ("result_sha256", "generation_attempt_id", "producer_status"):
            job = self.store.jobs[APPROVED_RESERVATION["job_id"]]
            original = job[key]
            job[key] = "different"
            with self.subTest(key=key), self.assertRaisesRegex(StopRun, "accounting_reservation_source_mismatch"):
                controller.resume_after_accounting()
            self.assertEqual(self.path.read_bytes(), saved)
            job[key] = original

    def test_no_resume_for_other_stop_reason_or_another_missing_job(self):
        controller = self.stopped_controller()
        controller.state["stop_reason"] = "job_needs_attention"
        with self.assertRaisesRegex(StopRun, "accounting_resume_not_allowed"):
            controller.resume_after_accounting()
        controller.state["stop_reason"] = "missing_cost_evidence"
        original = controller.state["records"][1]["job_id"]
        controller.state["records"][1]["job_id"] = "other"
        self.store.jobs["other"] = {**self.store.jobs[original], "job_id": "other"}
        with self.assertRaisesRegex(StopRun, "accounting_reservation_not_for_this_job"):
            controller.resume_after_accounting()

    def test_reservation_cannot_exceed_budget_or_become_measured(self):
        self.config["total_budget_usd"] = "0.50"
        controller = self.stopped_controller()
        saved = self.path.read_bytes()
        with self.assertRaisesRegex(StopRun, "accounting_reservation_exceeds_budget"):
            controller.resume_after_accounting()
        self.assertEqual(self.path.read_bytes(), saved)
        self.config["total_budget_usd"] = "5.00"
        controller.read_result = lambda job: {"status": "content_type_mismatch_skipped",
                                             "usage_records": [{"estimated_cost_usd": "0.01"}]}
        with self.assertRaisesRegex(StopRun, "accounting_resume_not_missing_evidence"):
            controller.resume_after_accounting()

    def test_new_missing_cost_stops_again_and_old_receipt_cannot_resume_it(self):
        controller = self.stopped_controller()
        controller.resume_after_accounting()
        controller.read_result = lambda job: {"status": job.get("producer_status")}
        with self.assertRaisesRegex(StopRun, "missing_cost_evidence"):
            controller.run()
        self.assertEqual(self.store.published, ["3"])
        with self.assertRaisesRegex(StopRun, "accounting_reservation_not_for_this_job"):
            controller.resume_after_accounting()


if __name__ == "__main__":
    unittest.main()
