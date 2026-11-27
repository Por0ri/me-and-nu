"""Synthetic producer exits and explicit recovery: no network or paid provider."""
import copy
from contextlib import contextmanager
import hashlib
import unittest
from unittest.mock import patch

import test_daily_generation as legacy
from test_daily_allocation import RoutedStore
from menu_worker import failure_recovery as recovery
from menu_worker.bounded_generation import StopRun, read_json, write_json
from menu_worker.bounded_resume import digest, totals, verify_completed_history
from menu_worker.daily_resume import (activate_plan, build_plan, resume_failure_recovery,
                                      source_checkpoint, validate_failure_resume, _checked_state)
from menu_worker.job_artifacts import artifact_directory
from menu_worker.job_contract import validate_job_request
from menu_worker.real_generation import _identity


class DailyFailureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = legacy.DailyTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.store.__class__ = RoutedStore
        self.plan = self.f.plan(daily_budget_usd=None, deadline=None,
                               routes=[{"source_id": "one_piece_official", "content_type": "goods", "menu_work_id": 8}])
        _, self.reservations = self.f.activate(self.plan)
        self.f.store.outcomes = ["ready_for_staging", "held", "ready_for_staging"]
        original_publish = self.f.store.publish_pending
        def publish(job_id, publish, expected_kind):
            result = original_publish(job_id, publish, expected_kind)
            if len(self.f.store.publications) == 3:
                job = self.f.store.jobs[job_id]
                row = read_json(self.f.child)["records"][-1]
                request, canonical, input_sha = validate_job_request(row["request"], "anime_generate")
                job.update(**{key: getattr(request, key) for key in ("source_id", "content_type", "menu_work_id", "source_url")},
                    input_sha256=input_sha, job_kind="anime_generate", execution_status="needs_attention",
                    outcome=None, producer_status=None, result_sha256=None, artifact_path=None,
                    current_attempt_id=job["generation_attempt_id"], generation_started_at="2026-11-24T01:00:00+00:00",
                    finished_at="2026-11-24T01:01:00+00:00", claim_token=None, lease_expires_at=None)
                job["attempts"] = [{"job_id": job_id, "attempt_id": job["generation_attempt_id"], "attempt_no": 1,
                    "mode": "generation", "execution_status": "needs_attention", "outcome": None,
                    "generation_started_at": job["generation_started_at"], "finished_at": job["finished_at"]}]
                directory = artifact_directory(self.f.root, job, create=True)
                write_json(directory / "launch.json", _identity(job))
                write_json(directory / "exit.json", {"returncode": 1})
                (directory / "agent.sqlite3").write_bytes(b"retained synthetic failed Agent")
                self.f.store.results.pop(job_id)
            return result
        self.f.store.publish_pending = publish
        with self.assertRaisesRegex(StopRun, "job_needs_attention"):
            self.controller().run()
        self.before = read_json(self.f.child)
        self.failed = copy.deepcopy(self.f.store.get(self.before["records"][-1]["job_id"]))
        self.old_database = copy.deepcopy(self.f.store.jobs)
        @contextmanager
        def view(store, *, lock=False):
            snapshot = {name: [] for name in recovery.TABLES}
            snapshot["content_generation_job"] = [{k: copy.deepcopy(v) for k, v in row.items() if k != "attempts"}
                                                  for row in store.jobs.values()]
            snapshot["content_generation_attempt"] = [copy.deepcopy(a) for row in store.jobs.values() for a in row["attempts"]]
            yield snapshot
        @contextmanager
        def lock(*args):
            yield lambda: None
        self.addCleanup(patch.stopall)
        patch.object(recovery, "_database_view", view).start()
        patch.object(recovery, "database_lock", lock).start()
        self.f.store.generation_history_guard = lock
        plan = recovery.plan_recovery(checkpoint=self.f.child,
            expected_sha256=hashlib.sha256(self.f.child.read_bytes()).hexdigest(), store=self.f.store,
            artifact_root=self.f.root, job_id=self.failed["job_id"], reason="Retain the unresolved charge and skip this failed article")
        self.receipt = recovery.apply_recovery(plan, expected_plan_sha256=digest(plan), store=self.f.store)
        self.sha = digest(self.receipt)
        self.f.scans = 0  # Offer the failed URL again along with new articles.

    def controller(self):
        return self.f.controller(self.plan, self.reservations)

    def resume(self, now=None):
        state = read_json(self.f.child)
        resume_failure_recovery(state, self.sha, store=self.f.store, now=self.f.now if now is None else now)
        write_json(self.f.child, state)
        return state

    def test_reconciliation_retains_failure_unknown_cost_and_does_not_resume(self):
        original = self.f.child.read_bytes()
        report = self.controller().run()
        self.assertEqual(report["stop_reason"], "failure_reconciled_paused")
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (3, 1))
        self.assertEqual(report["daily_unknown_cost_jobs"], [self.failed["job_id"]])
        self.assertIsNone(report["daily_spent_usd"])
        self.assertEqual(report["daily_known_and_reserved_subtotal_usd"], "0.02000000")
        self.assertIsNone(report["estimated_total_cost_usd"])
        self.assertEqual(self.f.store.jobs, self.old_database)
        self.assertEqual(len(self.f.store.publications), 3)
        self.assertEqual(self.f.child.read_bytes(), original)
        with self.assertRaisesRegex(StopRun, "daily_source_requires_reconciliation"):
            source_checkpoint(self.f.child, hashlib.sha256(original).hexdigest())
        verify_completed_history(self.f.store, read_json(self.f.child), self.f.read_result)

    def test_explicit_resume_skips_old_article_and_stops_at_twenty_passes(self):
        resumed = self.resume()
        self.assertEqual(self.resume(), resumed)  # An exact explicit retry adds no authorization or slots.
        report = self.controller().run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (22, 20))
        self.assertEqual(len(self.f.store.publications), 22)
        self.assertEqual(self.f.store.publications.count(self.failed["job_id"]), 1)
        self.assertEqual(self.f.store.get(self.failed["job_id"]), self.failed)
        records = read_json(self.f.child)["records"]
        self.assertEqual(len({r["canonical_url"] for r in records}), len(records))
        self.assertEqual(records[:6], self.before["records"][:6])
        self.assertEqual(len(list(self.f.inbox.iterdir())), 20)
        self.assertIsNone(report["daily_spent_usd"])
        self.assertEqual(report["reserved_cost_usd"], "1.00")

    def test_failed_slot_counts_toward_forty_and_cost_is_not_cleared(self):
        self.f.store.outcomes = ["held"] * 50
        self.resume()
        report = self.controller().run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (40, 1))
        self.assertEqual(report["stop_reason"], "daily_attempt_limit_reached")
        self.assertEqual(len(self.f.store.publications), 40)
        self.assertEqual(report["unknown_cost_jobs"], [self.failed["job_id"]])

    def test_wrong_receipt_and_changed_failed_attempt_block_explicit_resume(self):
        state = read_json(self.f.child)
        with self.assertRaisesRegex(StopRun, "explicit_reviewed_failure_receipt_required"):
            validate_failure_resume(state, "0" * 64, store=self.f.store)
        self.f.store.jobs[self.failed["job_id"]]["attempts"][0]["finished_at"] = "changed"
        with self.assertRaisesRegex(StopRun, "failure_recovery_job_history_changed"):
            self.resume()
        self.assertEqual(read_json(self.f.child), state)
        self.assertEqual(len(self.f.store.publications), 3)

    def test_new_failure_requires_its_own_reconciliation(self):
        self.resume()
        state = read_json(self.f.child)
        state.update(status="stopped", stop_reason="job_needs_attention")
        with self.assertRaisesRegex(StopRun, "failure_resume_not_allowed"):
            resume_failure_recovery(state, self.sha, store=self.f.store, now=self.f.now)

    def test_expired_day_finalizes_without_generation_and_carries_history_to_next_day(self):
        deadline = self.controller().deadline
        state = self.resume(now=deadline + 60)
        self.assertEqual((state["status"], state["stop_reason"]), ("stopped", "deadline_reached"))
        self.assertEqual(len(self.f.store.publications), 3)
        child_sha = hashlib.sha256(self.f.child.read_bytes()).hexdigest()
        kwargs = dict(source=self.f.child, source_sha256=child_sha, checkpoint=self.f.root / "tomorrow.json",
                      policy_date="2026-11-25", delivery_root=self.f.inbox, now=deadline + 3600,
                      routes=self.plan["daily_policy"]["routes"])
        plan = build_plan(**kwargs)
        tomorrow, _, _ = _checked_state(
            plan, store=self.f.store, read_result=self.f.read_result, now=deadline + 3600)
        self.assertEqual(tomorrow["records"], state["records"])
        self.assertEqual(tomorrow["failure_recovery_audit"], state["failure_recovery_audit"])
        self.assertEqual(plan["daily_policy"]["prior_unknown_costs"][0]["job_id"], self.failed["job_id"])
        self.assertEqual(plan["config"]["max_new_jobs"], 47)
        self.assertEqual(plan["daily_policy"]["success_target"], 20)
        self.assertEqual(totals(tomorrow), totals(state))
        with self.assertRaisesRegex(StopRun, "unknown_cost_in_budget_mode"):
            build_plan(**kwargs, daily_budget_usd="5", deadline="2026-11-25T23:00:00+09:00")


if __name__ == "__main__":
    unittest.main()
