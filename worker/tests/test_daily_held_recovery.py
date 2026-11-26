"""Real artifact seals and recovery receipts; synthetic jobs, no provider calls."""
import copy
import hashlib
import unittest

import test_daily_failure_recovery as failed_fixture
from menu_worker import failure_recovery as recovery
from menu_worker.bounded_generation import StopRun, read_json, write_json
from menu_worker.bounded_resume import digest, totals, verify_completed_history
from menu_worker.daily_resume import (build_plan, resume_failure_recovery,
                                      source_checkpoint, validate_failure_resume, _checked_state)
from menu_worker.job_artifacts import artifact_directory
from menu_worker.job_contract import validate_job_request
from menu_worker.real_generation import _identity, _request, read_sealed_staging_result, validate_real_artifacts


class DailyHeldRecoveryTests(unittest.TestCase):
    def setUp(self):
        # Keep the original failed-job receipt, then stop on a separately sealed
        # held result. This models a mixed history without altering old evidence.
        self.previous = failed_fixture.DailyFailureRecoveryTests()
        self.previous.setUp()
        self.addCleanup(self.previous.doCleanups)
        self.f = self.previous.f
        self.failed = self.previous.failed
        self.previous.resume()
        self.previous_history = read_json(self.f.child)
        original_publish = self.f.store.publish_pending

        def publish(job_id, callback, expected_kind):
            checkpoint = read_json(self.f.child)
            self.assertTrue(all(row["state"] in {"complete", "duplicate"} for row in checkpoint["records"][:-1]))
            self.assertTrue(all(job["execution_status"] in {"succeeded", "needs_attention"}
                                for key, job in self.f.store.jobs.items() if key != job_id))
            result = original_publish(job_id, callback, expected_kind)
            if len(self.f.store.publications) == 4:
                job = self.f.store.jobs[job_id]
                row = read_json(self.f.child)["records"][-1]
                request, _, input_sha = validate_job_request(row["request"], "anime_generate")
                job.update(**{key: getattr(request, key) for key in ("source_id", "content_type", "menu_work_id", "source_url")},
                    input_sha256=input_sha, job_kind="anime_generate", execution_status="succeeded",
                    current_attempt_id=job["generation_attempt_id"], generation_started_at="2026-11-24T01:02:00+00:00",
                    finished_at="2026-11-24T01:03:00+00:00", claim_token=None, lease_expires_at=None)
                directory = artifact_directory(self.f.root, job, create=True)
                write_json(directory / "launch.json", _identity(job))
                write_json(directory / "exit.json", {"returncode": 0})
                payload = {"status": "expiry_unresolved_held", "expected_work_request": _request(job)}
                write_json(directory / "result.json", payload)
                (directory / "agent.sqlite3").write_bytes(b"retained synthetic held Agent")
                job.update(validate_real_artifacts(self.f.root, job))
                job["attempts"] = [{"job_id": job_id, "attempt_id": job["generation_attempt_id"], "attempt_no": 1,
                    "mode": "generation", "execution_status": "succeeded", "outcome": "held",
                    **{key: job[key] for key in ("artifact_path", "result_sha256", "producer_status")},
                    "generation_started_at": job["generation_started_at"], "finished_at": job["finished_at"]}]
                self.f.store.results[job_id] = read_sealed_staging_result(self.f.root, job)
            return result

        self.f.store.publish_pending = publish
        with self.assertRaisesRegex(StopRun, "missing_cost_evidence"):
            self.controller().run()
        self.before = read_json(self.f.child)
        self.held = self.f.store.get(self.before["records"][-1]["job_id"])
        self.database_before = copy.deepcopy(self.f.store.jobs)
        directory = artifact_directory(self.f.root, self.held)
        self.sealed_before = {path.name: path.read_bytes() for path in directory.iterdir()}
        plan = recovery.plan_recovery(checkpoint=self.f.child,
            expected_sha256=hashlib.sha256(self.f.child.read_bytes()).hexdigest(), store=self.f.store,
            artifact_root=self.f.root, job_id=self.held["job_id"], reason="Preserve sealed held output and unknown charge; skip this article")
        self.assertEqual(plan["operation"], "reconcile_held_generation_unknown_cost")
        self.receipt = recovery.apply_recovery(plan, expected_plan_sha256=digest(plan), store=self.f.store)
        self.sha = digest(self.receipt)
        self.f.scans = 0  # Rediscovery offers both old articles again.

    def controller(self):
        return self.previous.controller()

    def resume(self, now=None):
        state = read_json(self.f.child)
        resume_failure_recovery(state, self.sha, store=self.f.store, now=self.f.now if now is None else now)
        write_json(self.f.child, state)
        return state

    def assert_original_evidence(self):
        self.assertEqual(self.f.store.get(self.held["job_id"]), self.held)
        self.assertEqual(self.f.store.get(self.failed["job_id"]), self.failed)
        directory = artifact_directory(self.f.root, self.held)
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, self.sealed_before)

    def test_mixed_reconciliation_stays_paused_preserves_seal_and_does_not_export_held(self):
        checkpoint = self.f.child.read_bytes()
        report = self.controller().run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (4, 1))
        self.assertEqual(report["stop_reason"], "failure_reconciled_paused")
        self.assertEqual(report["daily_unknown_cost_jobs"], [self.failed["job_id"], self.held["job_id"]])
        self.assertIsNone(report["daily_spent_usd"])
        self.assertIsNone(report["estimated_total_cost_usd"])
        self.assertEqual(report["daily_known_and_reserved_subtotal_usd"], "0.02000000")
        self.assertEqual(self.f.store.jobs, self.database_before)
        self.assertEqual(self.f.child.read_bytes(), checkpoint)
        self.assertEqual(len(self.f.store.publications), 4)
        self.assertEqual(len(list(self.f.inbox.iterdir())), 1)
        state = read_json(self.f.child)
        row = state["records"][-1]
        self.assertEqual((row["execution_status"], row["outcome"], row["producer_status"]),
                         ("succeeded", "held", "expiry_unresolved_held"))
        self.assertEqual(row["result_sha256"], self.held["result_sha256"])
        self.assertEqual(state["failure_recovery_audit"][:1], self.previous_history["failure_recovery_audit"])
        verify_completed_history(self.f.store, state, self.f.read_result)
        self.assert_original_evidence()
        with self.assertRaisesRegex(StopRun, "daily_source_requires_reconciliation"):
            source_checkpoint(self.f.child, hashlib.sha256(checkpoint).hexdigest())

    def test_explicit_resume_keeps_both_articles_seen_and_stops_at_twenty_passes(self):
        resumed = self.resume()
        self.assertEqual(self.resume(), resumed)
        report = self.controller().run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (23, 20))
        self.assertEqual(len(self.f.store.publications), 23)
        for job in (self.failed, self.held):
            self.assertEqual(self.f.store.publications.count(job["job_id"]), 1)
        records = read_json(self.f.child)["records"]
        self.assertEqual(records[:len(resumed["records"])], resumed["records"])
        self.assertEqual(len({row["canonical_url"] for row in records}), len(records))
        self.assertEqual(len(list(self.f.inbox.iterdir())), 20)
        self.assert_original_evidence()

    def test_held_and_failed_slots_count_toward_forty_without_repeating_them(self):
        self.f.store.outcomes = ["held"] * 50
        self.resume()
        report = self.controller().run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (40, 1))
        self.assertEqual(report["stop_reason"], "daily_attempt_limit_reached")
        self.assertEqual(len(self.f.store.publications), 40)
        self.assertEqual(report["unknown_cost_jobs"], [self.failed["job_id"], self.held["job_id"]])
        self.assert_original_evidence()

    def test_old_failure_receipt_cannot_authorize_the_later_held_stop(self):
        state = read_json(self.f.child)
        with self.assertRaisesRegex(StopRun, "explicit_reviewed_failure_receipt_required"):
            validate_failure_resume(state, self.previous.sha, store=self.f.store, now=self.f.now)
        self.assertEqual(read_json(self.f.child), state)
        self.assertEqual(len(self.f.store.publications), 4)
        self.resume()
        subsequent = read_json(self.f.child)
        subsequent.update(status="stopped", stop_reason="job_needs_attention")
        with self.assertRaisesRegex(StopRun, "failure_resume_not_allowed"):
            resume_failure_recovery(subsequent, self.sha, store=self.f.store, now=self.f.now)

    def test_changed_held_job_or_sealed_bytes_prevent_history_verification(self):
        state = read_json(self.f.child)
        self.f.store.jobs[self.held["job_id"]]["outcome"] = "ready_for_staging"
        with self.assertRaisesRegex(StopRun, "failure_recovery_job_history_changed"):
            verify_completed_history(self.f.store, state, self.f.read_result)
        self.f.store.jobs[self.held["job_id"]] = copy.deepcopy(self.held)
        result = artifact_directory(self.f.root, self.held) / "result.json"
        result.write_bytes(result.read_bytes() + b" ")
        with self.assertRaises(StopRun):
            verify_completed_history(self.f.store, state, self.f.read_result)
        self.assertEqual(len(self.f.store.publications), 4)

    def test_expired_day_carries_mixed_unknown_costs_without_regeneration(self):
        deadline = self.controller().deadline
        state = self.resume(now=deadline + 60)
        self.assertEqual((state["status"], state["stop_reason"]), ("stopped", "deadline_reached"))
        self.assertEqual(len(self.f.store.publications), 4)
        kwargs = dict(source=self.f.child, source_sha256=hashlib.sha256(self.f.child.read_bytes()).hexdigest(),
            checkpoint=self.f.root / "tomorrow.json", policy_date="2026-11-25", delivery_root=self.f.inbox,
            now=deadline + 3600, routes=self.previous.plan["daily_policy"]["routes"])
        plan = build_plan(**kwargs)
        tomorrow, _, _ = _checked_state(plan, store=self.f.store, read_result=self.f.read_result, now=deadline + 3600)
        self.assertEqual(tomorrow["records"], state["records"])
        self.assertEqual(tomorrow["seen"], state["seen"])
        self.assertEqual(tomorrow["failure_recovery_audit"], state["failure_recovery_audit"])
        self.assertEqual([ref["job_id"] for ref in plan["daily_policy"]["prior_unknown_costs"]],
                         [self.failed["job_id"], self.held["job_id"]])
        self.assertEqual((plan["config"]["max_new_jobs"], plan["daily_policy"]["success_target"]), (48, 20))
        self.assertEqual(totals(tomorrow), totals(state))
        with self.assertRaisesRegex(StopRun, "unknown_cost_in_budget_mode"):
            build_plan(**kwargs, daily_budget_usd="5", deadline="2026-11-25T23:00:00+09:00")
        self.assert_original_evidence()


if __name__ == "__main__":
    unittest.main()
