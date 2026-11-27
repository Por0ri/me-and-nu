"""Sealed held-cost reconciliation against an invocation-owned PostgreSQL DB.

Producer output is synthetic but sealed/read by the real artifact validator.
No producer, broker, provider, service DB or production artifact is contacted.
"""
import copy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_failure_recovery_postgres as previous
import test_jobs_migration_postgres as migration_tests
from menu_worker import real_generation
from menu_worker.bounded_generation import StopRun, database_lock, read_json, write_json
from menu_worker.bounded_resume import digest, verify_completed_history
from menu_worker.daily_resume import activate_plan, build_plan, resume_failure_recovery, source_checkpoint
from menu_worker.daily_generation import DailyController
from menu_worker.job_artifacts import artifact_directory
from menu_worker.job_store import JobConflict
from menu_worker.staging_export import export_staging_bundle


@unittest.skipUnless(os.environ.get(migration_tests.ADMIN_ENV), "Requires the isolated jobs-db fixture")
class HeldRecoveryPostgresTests(unittest.TestCase):
    make_plan = previous.FailureRecoveryPostgresTests.make_plan
    request = previous.FailureRecoveryPostgresTests.request
    started = previous.FailureRecoveryPostgresTests.started
    record = previous.FailureRecoveryPostgresTests.record
    complete = previous.FailureRecoveryPostgresTests.complete
    controller = previous.FailureRecoveryPostgresTests.controller
    artifact_bytes = previous.FailureRecoveryPostgresTests.artifact_bytes

    def read_result(self, job):
        if getattr(self, "held", None) and job["job_id"] == self.held["job_id"]:
            return real_generation.read_sealed_staging_result(self.root, job)
        return self.results[job["job_id"]]

    def setUp(self):
        previous.FailureRecoveryPostgresTests.setUp(self)
        old_plan = previous.FailureRecoveryPostgresTests.recovery_plan(self)
        self.old_receipt = self.recovery.apply_recovery(
            old_plan, expected_plan_sha256=digest(old_plan), store=self.store)
        state = resume_failure_recovery(read_json(self.child), digest(self.old_receipt),
                                        store=self.store, now=self.now)
        write_json(self.child, state)  # Isolated fixture's already-approved prior failure only.
        self.prior_failure = self.failed
        self.prior_failure_directory = self.directory
        self.held_request = self.request(83603)
        active = self.started(self.held_request)
        self.directory = artifact_directory(self.root, active, create=True)
        self.held_result = {
            "status": "expiry_unresolved_held", "request_id": "synthetic-sealed-held",
            "expected_work_request": real_generation._request(active),
            "content_type": "goods", "source_url": active["canonical_url"],
            "reason": "Synthetic article has no verified sales end date.",
        }
        write_json(self.directory / "launch.json", real_generation._identity(active))
        write_json(self.directory / "exit.json", {"returncode": 0})
        write_json(self.directory / "result.json", self.held_result)
        (self.directory / "agent.log").write_bytes(b"Synthetic held response; no provider call.\n")
        evidence = real_generation.validate_real_artifacts(self.root, active)
        self.assertEqual((evidence["outcome"], evidence["producer_status"]), ("held", "expiry_unresolved_held"))
        self.assertTrue(self.store.complete(active["job_id"], active["claim_token"],
            expected_kind="anime_generate", execution_status="succeeded", **evidence))
        self.held = self.store.get(active["job_id"])
        self.assertEqual(self.read_result(self.held), self.held_result)
        state["records"].append({**self.record(self.held, self.held_request, state),
            "execution_status": "succeeded", "outcome": "held",
            "producer_status": "expiry_unresolved_held", "generation_attempt_id": self.held["generation_attempt_id"],
            "result_sha256": self.held["result_sha256"]})
        state["seen"].append(self.held["canonical_url"])
        state.update(status="stopped", stop_reason="missing_cost_evidence")
        write_json(self.child, state)
        self.before = copy.deepcopy(state)
        self.original_checkpoint = self.child.read_bytes()
        self.original_database = self.full_snapshot()
        self.original_files = self.all_files()
        self.assertEqual(len(self.original_database["content_generation_job"]), 8)
        self.assertEqual(len(self.original_database["content_generation_attempt"]), 8)

    def full_snapshot(self):
        tables = ("menu_worker_schema", "content_generation_job", "content_generation_attempt",
                  "content_generation_review", "content_staging_selection")
        with self.fixture.connect() as connection:
            return {table: connection.execute(
                "SELECT * FROM public." + table + " AS t ORDER BY row_to_json(t)::text").fetchall()
                for table in tables}

    def all_files(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def assert_originals_preserved(self):
        self.assertEqual(self.full_snapshot(), self.original_database)
        for name, contents in self.original_files.items():
            if name != self.child.relative_to(self.root).as_posix():
                self.assertEqual((self.root / name).read_bytes(), contents, name)
        self.assertEqual(self.store.get(self.held["job_id"]), self.held)
        self.assertEqual(self.store.get(self.prior_failure["job_id"]), self.prior_failure)

    def held_plan(self):
        return self.recovery.plan_recovery(checkpoint=self.child,
            expected_sha256=hashlib.sha256(self.original_checkpoint).hexdigest(), store=self.store,
            artifact_root=self.root, job_id=self.held["job_id"],
            reason="operator_approved_preserve_sealed_held_unknown_cost")

    def apply_held(self, plan=None):
        plan = self.held_plan() if plan is None else plan
        receipt = self.recovery.apply_recovery(plan, expected_plan_sha256=digest(plan), store=self.store)
        return plan, receipt, read_json(self.child)

    def test_plan_and_apply_preserve_all_eight_jobs_sealed_bytes_and_prior_receipts(self):
        plan = self.held_plan()
        self.assertEqual(plan["operation"], "reconcile_held_generation_unknown_cost")
        self.assertEqual(self.child.read_bytes(), self.original_checkpoint)
        self.assertEqual(self.all_files(), self.original_files)
        self.assert_originals_preserved()
        _, receipt, state = self.apply_held(plan)
        self.assert_originals_preserved()
        self.assertEqual(state["records"][:-1], self.before["records"][:-1])
        for key in ("seen", "config", "daily_policy", "daily_policies", "daily_plan_sha256", "failure_recovery_resume_audit"):
            self.assertEqual(state[key], self.before[key])
        row = state["records"][-1]
        self.assertEqual((state["status"], state["stop_reason"]), ("stopped", "failure_reconciled_paused"))
        self.assertEqual((row["state"], row["execution_status"], row["outcome"]), ("complete", "succeeded", "held"))
        self.assertEqual(row["producer_status"], "expiry_unresolved_held")
        self.assertEqual(row["generation_attempt_id"], self.held["generation_attempt_id"])
        self.assertEqual(row["result_sha256"], self.held["result_sha256"])
        self.assertTrue(row["counts_slot"])
        self.assertTrue(row["cost_unknown"])
        self.assertEqual(row["accounting_method"], "unknown")
        self.assertIsNone(row["estimated_cost_usd"])
        self.assertIsNone(row["measured_cost"])
        self.assertNotIn("reserved_cost_usd", row)
        self.assertTrue(receipt["unknown_total"])
        self.assertFalse(receipt["generation_enabled"])
        self.assertFalse(receipt["resume_authorized"])
        self.assertIsNone(receipt["known_partial_estimated_usd"])
        self.assertEqual(Path(receipt["checkpoint_backup"]).read_bytes(), self.original_checkpoint)
        self.assertEqual(state["failure_recovery_audit"][:-1], self.before["failure_recovery_audit"])
        self.recovery.validate_recovery_history(state, store=self.store, artifact_root=self.root)
        verify_completed_history(self.store, state, self.read_result)
        after = self.all_files()
        self.recovery.apply_recovery(plan, expected_plan_sha256=digest(plan), store=self.store)
        self.assertEqual(self.all_files(), after)
        self.assert_originals_preserved()

    def test_no_auto_resume_no_export_and_old_receipt_cannot_authorize_new_hold(self):
        _, _, state = self.apply_held()
        report = self.controller().run()
        self.assertEqual((report["status"], report["stop_reason"]), ("stopped", "failure_reconciled_paused"))
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (4, 1))
        self.assertEqual(report["unknown_cost_jobs"], [self.prior_failure["job_id"], self.held["job_id"]])
        self.assertIsNone(report["estimated_total_cost_usd"])
        self.assertIsNone(report["daily_spent_usd"])
        self.assertEqual(Decimal(report["daily_known_and_reserved_subtotal_usd"]), Decimal("0.01276363"))
        with self.assertRaisesRegex(StopRun, "explicit_reviewed_failure_receipt_required"):
            resume_failure_recovery(copy.deepcopy(state), digest(self.old_receipt), store=self.store, now=self.now)
        paused = self.child.read_bytes()
        with self.assertRaisesRegex(StopRun, "daily_source_requires_reconciliation"):
            source_checkpoint(self.child, hashlib.sha256(paused).hexdigest())
        with self.assertRaisesRegex(real_generation.RealGenerationError, "job_not_ready_for_staging"):
            export_staging_bundle(self.root, self.store, self.held["job_id"], self.root / "forbidden-export")
        self.assertFalse((self.root / "forbidden-export").exists())
        self.assertEqual(self.child.read_bytes(), paused)
        self.assert_originals_preserved()

    def test_same_article_and_other_types_remain_blocked_while_other_article_can_start(self):
        self.apply_held()
        self.assertFalse(self.store.retry_preflight(self.held["job_id"], expected_kind="anime_generate"))
        self.assertIsNone(self.store.claim(self.held["job_id"], expected_kind="anime_generate"))
        replay = self.store.submit(self.held_request, job_kind="anime_generate")
        self.assertFalse(replay["created"])
        self.assertEqual(replay["job_id"], self.held["job_id"])
        for content_type in ("goods", "event", "news"):
            for suffix in ("", "/", "?utm_source=fixture", "?ref=fixture"):
                duplicate = {**self.held_request, "idempotency_key": "other-type-" + str(uuid4()),
                    "content_type": content_type, "source_url": self.held["canonical_url"] + suffix,
                    "canonical_url": self.held["canonical_url"] + suffix}
                with self.assertRaisesRegex(JobConflict, "candidate_exists"):
                    self.store.submit(duplicate, job_kind="anime_generate")
        self.assert_originals_preserved()
        other = self.store.submit(self.request(83604), job_kind="anime_generate")
        with self.store.generation_history_guard() as check:
            check()
            self.assertFalse(self.store.generation_history_busy(other["job_id"]))
            self.assertEqual({row["job_id"] for row in self.store.cover_history_sources(other["job_id"])},
                             self.old_ids | set(self.success_ids) | {self.held["job_id"]})
            claimed = self.store.claim(other["job_id"], expected_kind="anime_generate")
            self.assertIsNotNone(claimed)
            self.assertTrue(self.store.mark_generation_started(other["job_id"], claimed["claim_token"],
                                                              expected_kind="anime_generate"))
            check()
            self.assertTrue(self.store.generation_history_busy(str(uuid4())))
        self.assertEqual(self.store.get(self.held["job_id"]), self.held)

    def test_crash_before_checkpoint_commit_retries_without_rewriting_original_evidence(self):
        plan = self.held_plan()
        with patch.object(self.recovery, "write_json", side_effect=OSError("synthetic checkpoint interruption")):
            with self.assertRaisesRegex(OSError, "synthetic checkpoint interruption"):
                self.apply_held(plan)
        self.assertEqual(self.child.read_bytes(), self.original_checkpoint)
        self.assert_originals_preserved()
        interrupted_files = self.all_files()
        self.apply_held(plan)
        self.assert_originals_preserved()
        for name, value in interrupted_files.items():
            if name != self.child.relative_to(self.root).as_posix():
                self.assertEqual((self.root / name).read_bytes(), value)

    def test_crash_after_checkpoint_commit_accepts_exact_replay(self):
        plan = self.held_plan()
        original_write = self.recovery.write_json

        def committed_then_failed(path, value):
            original_write(path, value)
            raise OSError("synthetic crash after checkpoint commit")

        with patch.object(self.recovery, "write_json", side_effect=committed_then_failed):
            with self.assertRaisesRegex(OSError, "synthetic crash after checkpoint commit"):
                self.apply_held(plan)
        committed = self.all_files()
        self.assertNotEqual(self.child.read_bytes(), self.original_checkpoint)
        self.apply_held(plan)
        self.assertEqual(self.all_files(), committed)
        self.assert_originals_preserved()

    def test_controller_and_postgres_writer_locks_block_apply_without_mutations(self):
        plan = self.held_plan()
        with database_lock(self.store, plan["run_id"]):
            with self.assertRaisesRegex(StopRun, "controller_already_running"):
                self.apply_held(plan)
        with self.fixture.connect() as writer:
            writer.execute("LOCK TABLE public.content_generation_job IN ROW EXCLUSIVE MODE")
            with self.assertRaises(self.fixture.psycopg.errors.LockNotAvailable):
                self.apply_held(plan)
        self.assertEqual(self.child.read_bytes(), self.original_checkpoint)
        self.assert_originals_preserved()

    def test_another_live_job_blocks_recovery(self):
        self.started(self.request(83604))
        before = self.full_snapshot()
        with self.assertRaises(StopRun):
            self.held_plan()
        self.assertEqual(self.full_snapshot(), before)
        self.assertEqual(self.child.read_bytes(), self.original_checkpoint)

    def test_changed_attempt_row_rejects_stale_plan(self):
        plan = self.held_plan()
        with self.fixture.connect() as connection:
            connection.execute("UPDATE content_generation_attempt SET error_code='synthetic_changed_evidence' WHERE attempt_id=%s",
                               (self.held["generation_attempt_id"],))
        changed = self.full_snapshot()
        with self.assertRaises(StopRun):
            self.apply_held(plan)
        self.assertEqual(self.child.read_bytes(), self.original_checkpoint)
        self.assertEqual(self.full_snapshot(), changed)

    def test_sealed_result_tamper_rejects_plan_and_later_history_validation(self):
        plan, _, state = self.apply_held()
        (self.directory / "result.json").write_text('{"status":"expiry_unresolved_held"}', encoding="utf-8")
        checkpoint = self.child.read_bytes()
        with self.assertRaises((StopRun, real_generation.RealGenerationError)):
            self.recovery.validate_recovery_history(state, store=self.store, artifact_root=self.root)
        with self.assertRaises((StopRun, real_generation.RealGenerationError)):
            verify_completed_history(self.store, state, self.read_result)
        with self.assertRaises((StopRun, real_generation.RealGenerationError)):
            self.apply_held(plan)
        self.assertEqual(self.child.read_bytes(), checkpoint)
        self.assertEqual(self.full_snapshot(), self.original_database)

    def test_next_day_carries_both_unknown_costs_and_preserves_day_limits(self):
        _, receipt, state = self.apply_held()
        next_now = self.now + 86400
        authorized = resume_failure_recovery(state, digest(receipt), store=self.store, now=next_now)
        self.assertEqual((authorized["status"], authorized["stop_reason"]), ("stopped", "deadline_reached"))
        write_json(self.child, authorized)  # Explicit isolated transition, never a live resume.
        source_sha = hashlib.sha256(self.child.read_bytes()).hexdigest()
        source_checkpoint(self.child, source_sha)
        args = dict(source=self.child, source_sha256=source_sha, checkpoint=self.root / "next-day.json",
            policy_date="2026-11-18", delivery_root=self.root / "inbox", now=next_now,
            routes=self.plan["daily_policy"]["routes"])
        with self.assertRaisesRegex(StopRun, "unknown_cost_in_budget_mode"):
            build_plan(**args, daily_budget_usd="5.00", deadline="2026-11-18T23:00:00+09:00")
        next_plan = build_plan(**args)
        self.assertEqual(next_plan["config"]["max_new_jobs"], 48)
        self.assertEqual((next_plan["daily_policy"]["max_attempts"], next_plan["daily_policy"]["success_target"]), (40, 20))
        self.assertEqual(next_plan["daily_policy"]["prior_unknown_costs"], [
            {"job_id": row["job_id"], **row["failure_recovery_receipt"]}
            for row in state["records"] if row.get("cost_unknown")])
        next_state, _ = activate_plan(next_plan, store=self.store, read_result=self.read_result, now=next_now)
        self.assertEqual(next_state["records"], state["records"])
        report = self.controller(args["checkpoint"], now=lambda: next_now).summary()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (0, 0))
        self.assertEqual(report["unknown_cost_jobs"], [self.prior_failure["job_id"], self.held["job_id"]])
        self.assertEqual(report["daily_unknown_cost_jobs"], [])
        self.assertIsNone(report["estimated_total_cost_usd"])
        verify_completed_history(self.store, next_state, self.read_result)
        self.assert_originals_preserved()

    def test_explicit_isolated_continuation_uses_only_remaining_thirty_six_attempts(self):
        _, receipt, state = self.apply_held()
        authorized = resume_failure_recovery(state, digest(receipt), store=self.store, now=self.now)
        write_json(self.child, authorized)  # Test-only explicit transition; all jobs use a fake producer.
        original_get = self.store.get
        previous_ids = self.old_ids | set(self.success_ids) | {self.prior_failure["job_id"], self.held["job_id"]}
        publications, scans = [], []

        def fake_producer(job_id):
            job = original_get(job_id)
            if job and job_id not in previous_ids and job["execution_status"] == "queued" and job.get("last_task_id"):
                claimed = self.store.claim(job_id, expected_kind="anime_generate")
                self.assertTrue(self.store.mark_generation_started(job_id, claimed["claim_token"], expected_kind="anime_generate"))
                self.complete(claimed)
                return original_get(job_id)
            return job

        def discover(*_args):
            scans.append(len(scans))
            return {"articles": [self.held_request, self.request(85000 + len(scans))], "discovered": 2}

        def publish(job_id):
            publications.append(job_id)
            return "synthetic-held-recovery-task-" + job_id

        delivered = []

        def existing_delivery_only(_controller, record):
            # The legacy fixture substitutes historical result I/O and has no
            # service bundle. Bound that existing success to this stub; no held
            # result or newly generated result may reach delivery in this test.
            self.assertEqual(record["job_id"], self.success_ids[0])
            self.assertEqual(record["outcome"], "ready_for_staging")
            delivered.append(record["job_id"])

        with patch.object(self.store, "get", side_effect=fake_producer), \
                patch.object(DailyController, "_emit_delivery", autospec=True, side_effect=existing_delivery_only):
            controller = self.controller(discover=discover, publish=publish, sleep=lambda _: None,
                validate=lambda fields: (fields["canonical_url"], hashlib.sha256(fields["canonical_url"].encode()).hexdigest()))
            completed = controller.run()
        self.assertEqual((completed["status"], completed["stop_reason"]), ("complete", "daily_attempt_limit_reached"))
        self.assertEqual((completed["daily_attempts"], completed["daily_successes"]), (40, 1))
        self.assertEqual(len(publications), 36)
        self.assertNotIn(self.held["job_id"], publications)
        self.assertEqual(completed["unknown_cost_jobs"], [self.prior_failure["job_id"], self.held["job_id"]])
        self.assertIsNone(completed["daily_spent_usd"])
        self.assertEqual(Decimal(completed["daily_known_and_reserved_subtotal_usd"]), Decimal("0.37276363"))
        self.assertEqual(controller.state["records"][:8], state["records"])
        self.assertEqual(self.store.get(self.held["job_id"]), self.held)
        verify_completed_history(self.store, controller.state, self.read_result)
        stopped = self.child.read_bytes()
        with patch.object(DailyController, "_emit_delivery", autospec=True, side_effect=existing_delivery_only):
            replay = self.controller().run()
        self.assertEqual(replay["daily_attempts"], 40)
        self.assertEqual(self.child.read_bytes(), stopped)
        self.assertEqual(delivered, [self.success_ids[0], self.success_ids[0]])


if __name__ == "__main__":
    unittest.main()
