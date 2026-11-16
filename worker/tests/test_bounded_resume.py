import contextlib
import copy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from menu_worker.bounded_generation import APPROVED_RESERVATION, Controller, StopRun, local_lock, validate_reservations, write_json
from menu_worker.bounded_resume import activate_plan, build_plan, claim_path, digest, main, publish_with_deadline, verify_completed_history


class Store:
    def __init__(self):
        self.jobs, self.results, self.publications = {}, {}, []

    def _connect(self):
        store = self
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, query): return self
            def fetchall(self):
                return [{"job_id": key, "idempotency_key": value["idempotency_key"]} for key, value in store.jobs.items()]
        return Connection()

    def get(self, key):
        return copy.deepcopy(self.jobs.get(key))

    def submit(self, request, job_kind):
        key = str(len(self.jobs) + 1)
        job = {"job_id": key, "idempotency_key": request["idempotency_key"], "execution_status": "queued",
               "outcome": "skipped", "producer_status": "content_type_mismatch_skipped", "result_sha256": "a" * 64,
               "generation_attempt_id": "attempt-" + key, "generation_started_at": None, "attempts": [], "last_task_id": None}
        self.jobs[key] = job
        self.results[key] = {"usage_records": [{"estimated_cost_usd": "0.01"}],
                             "usage_summary": {"llm_calls": 1, "estimated_cost_usd": "0.01"}}
        return {**job, "created": True}

    def publish_pending(self, job_id, publish, expected_kind):
        self.publications.append(job_id)
        self.jobs[job_id]["last_task_id"] = publish(job_id)
        self.jobs[job_id]["execution_status"] = "succeeded"
        return True


class ResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source, self.child = self.root / "checkpoint.json", self.root / "resume.json"
        self.now = datetime(2026, 11, 16, 5, tzinfo=timezone.utc).timestamp()
        self.store = Store()
        self.config = {"run_id": "beta-open-20261116", "source_id": "one_piece_official", "content_type": "goods",
            "total_budget_usd": "5.00", "baseline_count": 1, "baseline_estimated_cost_usd": "0.01178341",
            "max_new_jobs": 4, "next_job_reserve_usd": "0.04", "job_timeout_seconds": 1500,
            "deadline": "2026-11-16T17:00:00+09:00", "scan_interval_seconds": 60,
            "artifact_root": str(self.root), "bundle_root": str(self.root), "excluded_urls": ["baseline"]}
        self.articles = [{"source_id": "one_piece_official", "content_type": "goods",
                          "canonical_url": "article-" + str(i)} for i in range(4)]
        self.controller(self.config, self.source).run()
        state = json.loads(self.source.read_text())
        costs = ("0.00772830", None, "0.00933559", "0.00381376")
        for record, cost in zip(state["records"], costs):
            key = record["job_id"]
            if cost:
                record["estimated_cost_usd"] = cost
                self.store.results[key] = {"usage_records": [{"estimated_cost_usd": cost}]}
            else:
                job = self.store.jobs.pop(key)
                self.store.results.pop(key)
                job.update(job_id=APPROVED_RESERVATION["job_id"], generation_attempt_id=APPROVED_RESERVATION["generation_attempt_id"],
                           result_sha256=APPROVED_RESERVATION["source_result_sha256"])
                self.store.jobs[job["job_id"]] = job
                self.store.results[job["job_id"]] = {"status": "content_type_mismatch_skipped"}
                original = {**APPROVED_RESERVATION, "accounting_method": "reserve", "measured_cost": None,
                            "reserved_usd": "1.00", "reason": "Historical missing extraction usage; not a measured charge."}
                receipt = validate_reservations({"schema_version": 1, "records": [original]})[job["job_id"]]
                record.update(job_id=job["job_id"], generation_attempt_id=job["generation_attempt_id"],
                    result_sha256=job["result_sha256"], estimated_cost_usd=None, measured_cost=None,
                    reserved_cost_usd="1.00", accounting_method="reserve", accounting_reservation=receipt)
                state["accounting_resume_audit"] = [{**receipt, "previous_status": "stopped"}]
        write_json(self.source, state)
        self.original = self.source.read_bytes()
        self.sha = hashlib.sha256(self.original).hexdigest()
        self.store.publications.clear()

    def read_result(self, job):
        return copy.deepcopy(self.store.results[job["job_id"]])

    def controller(self, config, path, **kwargs):
        return Controller(config=config, checkpoint=path, store=self.store,
            discover=lambda *args: {"discovered": len(self.articles), "articles": self.articles},
            validate=lambda fields: (fields["canonical_url"], hashlib.sha256(fields["canonical_url"].encode()).hexdigest()),
            publish=lambda job_id: "task-" + job_id, read_result=self.read_result,
            export=lambda *args: self.fail("Skipped results must not export"), now=lambda: self.now,
            sleep=lambda seconds: setattr(self, "now", self.now + seconds), **kwargs)

    def plan(self, **changes):
        arguments = dict(source=self.source, source_sha256=self.sha, checkpoint=self.child,
                         total_budget_usd="5.00", additional_jobs=2, deadline="2026-11-16T20:00:00+09:00", now=self.now)
        arguments.update(changes)
        return build_plan(**arguments)

    def activate(self, plan=None):
        return activate_plan(plan or self.plan(), store=self.store, read_result=self.read_result, now=self.now)

    def test_plan_is_local_only_and_carries_actual_budget_and_count(self):
        before = sorted(p.name for p in self.root.iterdir())
        plan = self.plan()
        self.assertEqual(plan["prior_accounted_usd"], "1.03266106")
        self.assertEqual(plan["remaining_budget_usd"], "3.96733894")
        self.assertEqual(plan["prior_attempts"], 5)
        self.assertEqual(plan["maximum_total_attempts"], 7)
        self.assertEqual(plan["config"]["run_id"], self.config["run_id"])
        self.assertFalse(plan["generation_enabled"])
        self.assertEqual(before, sorted(p.name for p in self.root.iterdir()))
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(self.store.publications, [])

    def test_continuation_preserves_parent_and_reserve_and_adds_only_requested_attempts(self):
        plan = self.plan()
        state, reservations = self.activate(plan)
        prior_records = copy.deepcopy(state["records"])
        self.articles += [{"source_id": "one_piece_official", "content_type": "goods", "canonical_url": "new-" + str(i)} for i in range(5)]
        report = self.controller(plan["config"], self.child, reservations=reservations).run()
        self.assertEqual(report["new_slots_consumed"], 6)
        self.assertEqual(report["reserved_cost_usd"], "1.00")
        self.assertEqual(Decimal(report["budget_accounted_total_usd"]), Decimal("1.05266106"))
        self.assertEqual(len(self.store.publications), 2)
        after, _ = self.activate(plan)
        self.assertEqual(after["records"][:4], prior_records)
        self.assertEqual(self.controller(plan["config"], self.child, reservations=reservations).run(), report)
        self.assertEqual(len(self.store.publications), 2)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_second_successor_and_changed_limit_are_rejected(self):
        self.activate()
        for changes in ({"checkpoint": self.root / "different.json"}, {"additional_jobs": 3}, {"total_budget_usd": "6"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(StopRun, "source_already_has_another_successor"):
                self.activate(self.plan(**changes))
        self.assertEqual(self.store.publications, [])

    def test_parent_tampering_and_missing_claimed_child_are_rejected(self):
        self.activate()
        self.child.unlink()
        with self.assertRaisesRegex(StopRun, "claimed_successor_checkpoint_missing"):
            self.activate()
        self.source.write_bytes(self.original + b"\n")
        with self.assertRaisesRegex(StopRun, "source_checkpoint_digest_mismatch"):
            self.plan()

    def test_lowered_historical_cost_rejected_against_sealed_result(self):
        state = json.loads(self.source.read_text())
        state["records"][0]["estimated_cost_usd"] = "0"
        write_json(self.source, state)
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(StopRun, "completed_cost_evidence_changed"):
            self.activate()
        self.assertFalse(self.child.exists())
        self.assertFalse(claim_path(self.source).exists())

    def test_external_baseline_cannot_be_reset_with_a_new_source_digest(self):
        for field, value in (("baseline_count", 0), ("baseline_estimated_cost_usd", "0"),
                             ("job_timeout_seconds", 1), ("next_job_reserve_usd", "0.001")):
            state = json.loads(self.original)
            state["config"][field] = value
            write_json(self.source, state)
            self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
            with self.subTest(field=field), self.assertRaisesRegex(StopRun, "legacy_.*changed"):
                self.plan()

    def test_changed_result_attempt_or_reservation_rejected(self):
        record = json.loads(self.source.read_text())["records"][1]
        job = self.store.jobs[record["job_id"]]
        for field in ("result_sha256", "generation_attempt_id", "outcome"):
            old = job[field]
            job[field] = "changed"
            with self.subTest(field=field), self.assertRaisesRegex(StopRun, "completed_job_evidence_changed"):
                self.activate()
            job[field] = old

    def test_checkpoint_rollback_after_new_job_cannot_reset_count(self):
        plan = self.plan(additional_jobs=1)
        _, reservations = self.activate(plan)
        snapshot = self.child.read_bytes()
        self.articles = [{"source_id": "one_piece_official", "content_type": "goods", "canonical_url": "new"}]
        self.controller(plan["config"], self.child, reservations=reservations).run()
        self.child.write_bytes(snapshot)
        with self.assertRaisesRegex(StopRun, "database_history_not_owned_by_checkpoint"):
            self.activate(plan)
        self.assertEqual(len(self.store.publications), 1)

    def test_inherited_record_cannot_be_dropped_or_cost_altered(self):
        self.activate()
        state = json.loads(self.child.read_text())
        state["records"][0]["estimated_cost_usd"] = "0"
        write_json(self.child, state)
        with self.assertRaisesRegex(StopRun, "inherited_history_changed"):
            self.activate()

    def test_unknown_new_usage_stops_before_next_job_without_reusing_reservation(self):
        plan = self.plan()
        _, reservations = self.activate(plan)
        self.articles = [{"source_id": "one_piece_official", "content_type": "goods", "canonical_url": "new-" + str(i)} for i in range(3)]
        controller = self.controller(plan["config"], self.child, reservations=reservations)
        controller.read_result = lambda job: {}
        with self.assertRaisesRegex(StopRun, "missing_cost_evidence"):
            controller.run()
        self.assertEqual(len(self.store.publications), 1)
        self.assertEqual(controller.reserved_spent(), Decimal("1"))
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_existing_reserve_counts_against_budget_before_each_new_job(self):
        plan = self.plan(total_budget_usd="1.08")
        _, reservations = self.activate(plan)
        self.articles = [{"source_id": "one_piece_official", "content_type": "goods", "canonical_url": "new-" + str(i)} for i in range(3)]
        controller = self.controller(plan["config"], self.child, reservations=reservations)
        with self.assertRaisesRegex(StopRun, "estimated_budget_reserve_unavailable"):
            controller.run()
        self.assertEqual(len(self.store.publications), 1)
        self.assertEqual(controller.spent(), Decimal("1.04266106"))
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_explicit_limits_timezone_budget_and_timeout_window_required(self):
        for changes in ({"additional_jobs": 0}, {"additional_jobs": True}, {"additional_jobs": 21},
                        {"total_budget_usd": "NaN"}, {"total_budget_usd": "1.04"},
                        {"deadline": "2026-11-16T14:00:00+09:00"}, {"deadline": "2026-11-16T14:10:00+09:00"},
                        {"deadline": "2026-11-16T20:00:00"}):
            with self.subTest(changes=changes), self.assertRaises(StopRun):
                self.plan(**changes)

    def test_checkpoint_cannot_replace_parent_lock_or_successor_claim(self):
        for checkpoint in (self.source, claim_path(self.source), self.source.with_name(self.source.name + ".lock")):
            with self.subTest(checkpoint=checkpoint), self.assertRaisesRegex(StopRun, "unsafe_resume_checkpoint_path"):
                self.plan(checkpoint=checkpoint)

    def test_parent_file_lock_excludes_simultaneous_continuations(self):
        with local_lock(self.source):
            with self.assertRaisesRegex(StopRun, "controller_already_running"):
                with local_lock(self.source):
                    self.fail("Second controller acquired lock")

    def test_cli_defaults_to_plan_and_apply_requires_matching_review_digest(self):
        args = ["--source-checkpoint", str(self.source), "--source-sha256", self.sha,
                "--checkpoint", str(self.child), "--total-budget-usd", "5", "--additional-jobs", "1",
                "--deadline", "2026-11-16T20:00:00+09:00"]
        with patch("menu_worker.bounded_resume.time.time", return_value=self.now), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(args), 0)
            output = json.loads(out.getvalue())
            self.assertEqual(output["queue_writes"], 0)
            self.assertEqual(output["mode"], "plan")
        with patch("menu_worker.bounded_resume.time.time", return_value=self.now), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main([*args, "--apply"]), 2)
            self.assertEqual(json.loads(out.getvalue())["error"], "explicit_reviewed_plan_digest_required")
        self.assertFalse(self.child.exists())
        self.assertEqual(self.store.publications, [])

    def test_delivery_expires_before_processing_window_and_late_publication_is_blocked(self):
        calls = []
        class App:
            def send_task(self, *args, **kwargs):
                calls.append((args, kwargs))
                return type("Result", (), {"id": "task-id"})()
        kwargs = dict(app=App(), task="content.anime_generate", queue="anime-generation-live",
                      deadline="2026-11-16T20:00:00+09:00", job_timeout_seconds=1500)
        self.assertEqual(publish_with_deadline("job", now=self.now, **kwargs), "task-id")
        expiry = calls[0][1]["expires"]
        self.assertEqual(expiry, datetime(2026, 11, 16, 10, 35, tzinfo=timezone.utc))
        self.assertEqual(calls[0][1]["kwargs"], {"job_id": "job"})
        for now in (expiry.timestamp(), expiry.timestamp() + 1):
            with self.subTest(now=now), self.assertRaisesRegex(StopRun, "insufficient_time_for_new_job"):
                publish_with_deadline("job", now=now, **kwargs)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
