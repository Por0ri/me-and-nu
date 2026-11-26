"""Offline reconciliation safety; actual database locks are covered by PG tests."""
import copy
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from menu_worker import failure_recovery as recovery
from menu_worker.bounded_generation import StopRun, read_json, write_json
from menu_worker.bounded_resume import digest
from menu_worker.job_artifacts import artifact_directory
from menu_worker.job_contract import validate_job_request
from menu_worker.real_generation import _identity, _request


class FailureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.checkpoint = self.root / "checkpoint.json"
        request = {"source_id": "one_piece_official", "source_url": "https://one-piece.com/news/83602/index.html",
                   "content_type": "goods", "menu_work_id": 8, "idempotency_key": "fixture:one"}
        _, canonical, input_sha = validate_job_request(request, "anime_generate")
        job_id, attempt_id = str(uuid4()), str(uuid4())
        self.job = {**request, "job_id": job_id, "job_kind": "anime_generate", "canonical_url": canonical,
            "input_sha256": input_sha, "execution_status": "needs_attention", "outcome": None,
            "current_attempt_id": attempt_id, "generation_attempt_id": attempt_id,
            "generation_started_at": "2026-11-26T01:00:00+00:00", "finished_at": "2026-11-26T01:01:00+00:00",
            "claim_token": None, "lease_expires_at": None, "artifact_path": None, "result_sha256": None}
        self.attempt = {"job_id": job_id, "attempt_id": attempt_id, "attempt_no": 1, "mode": "generation",
            "execution_status": "needs_attention", "generation_started_at": self.job["generation_started_at"],
            "finished_at": self.job["finished_at"], "outcome": None}
        self.snapshot = {name: [] for name in recovery.TABLES}
        self.snapshot["content_generation_job"] = [self.job]
        self.snapshot["content_generation_attempt"] = [self.attempt]
        self.directory = artifact_directory(self.artifacts, self.job, create=True)
        write_json(self.directory / "launch.json", _identity(self.job))
        write_json(self.directory / "exit.json", {"returncode": 1})
        (self.directory / "agent.sqlite3").write_bytes(b"synthetic evidence only")
        self.before = {"schema_version": 1, "status": "stopped", "stop_reason": "job_needs_attention",
            "config": {"run_id": "fixture", "artifact_root": str(self.artifacts), "limit_mode": "daily_counts",
                       "total_budget_usd": None},
            "daily_policy": {"schema_version": 3, "limit_mode": "daily_counts"}, "seen": [canonical],
            "records": [{"job_id": job_id, "state": "reserved", "counts_slot": True, "request": request,
                         "canonical_url": canonical, "execution_status": "needs_attention", "outcome": None}]}
        write_json(self.checkpoint, self.before)
        self.original = self.checkpoint.read_bytes()
        self.sha = hashlib.sha256(self.original).hexdigest()
        self.store = self

        @contextmanager
        def database_view(_store, *, lock=False):
            yield copy.deepcopy(self.snapshot)

        @contextmanager
        def database_lock(_store, _run_id):
            yield lambda: None

        self.addCleanup(patch.stopall)
        patch.object(recovery, "_database_view", database_view).start()
        patch.object(recovery, "database_lock", database_lock).start()

    @contextmanager
    def generation_history_guard(self):
        yield lambda: None

    def get(self, job_id):
        return recovery._job(copy.deepcopy(self.snapshot), job_id)

    def plan(self):
        return recovery.plan_recovery(checkpoint=self.checkpoint, expected_sha256=self.sha, store=self,
            artifact_root=self.artifacts, job_id=self.job["job_id"], reason="Operator retained unknown failed usage")

    def apply(self, plan=None):
        plan = self.plan() if plan is None else plan
        return recovery.apply_recovery(plan, expected_plan_sha256=digest(plan), store=self)

    def test_plan_has_no_filesystem_side_effect_and_apply_is_paused_idempotent(self):
        paths = sorted(str(path) for path in self.root.rglob("*"))
        snapshot = copy.deepcopy(self.snapshot)
        plan = self.plan()
        self.assertEqual(paths, sorted(str(path) for path in self.root.rglob("*")))
        receipt = self.apply(plan)
        state = read_json(self.checkpoint)
        self.assertEqual((state["status"], state["stop_reason"]), ("stopped", recovery.PAUSED_REASON))
        self.assertEqual(state["seen"], self.before["seen"])
        self.assertEqual(state["config"], self.before["config"])
        self.assertEqual(self.snapshot, snapshot)
        self.assertTrue(state["records"][0]["counts_slot"])
        self.assertEqual(state["records"][0]["accounting_method"], "unknown")
        self.assertIsNone(state["records"][0]["estimated_cost_usd"])
        self.assertIsNone(receipt["known_partial_estimated_usd"])
        self.assertTrue(receipt["unknown_total"])
        self.assertFalse(receipt["resume_authorized"])
        recovery.validate_recovery_history(state, store=self, artifact_root=self.artifacts)
        after = self.checkpoint.read_bytes()
        self.assertEqual(self.apply(plan), receipt)
        self.assertEqual(after, self.checkpoint.read_bytes())

    def test_wrong_review_digest_does_not_create_recovery_files(self):
        with self.assertRaisesRegex(StopRun, "explicit_reviewed_recovery_digest_required"):
            recovery.apply_recovery(self.plan(), expected_plan_sha256="0" * 64, store=self)
        self.assertEqual(self.checkpoint.read_bytes(), self.original)
        self.assertFalse(self.checkpoint.with_name(self.checkpoint.name + ".failure-recovery").exists())

    def test_checkpoint_compare_and_swap_rejects_changed_history(self):
        plan = self.plan()
        changed = copy.deepcopy(self.before)
        changed["seen"].append("https://example.invalid/unreviewed")
        write_json(self.checkpoint, changed)
        with self.assertRaises(StopRun):
            self.apply(plan)
        self.assertEqual(read_json(self.checkpoint), changed)

    def test_idempotent_apply_requires_exact_post_checkpoint_bytes(self):
        plan = self.plan()
        self.apply(plan)
        self.checkpoint.write_bytes(self.checkpoint.read_bytes() + b"\n")
        with self.assertRaisesRegex(StopRun, "recovery_checkpoint_changed"):
            self.apply(plan)

    def test_full_database_snapshot_change_rejects_apply(self):
        plan = self.plan()
        self.snapshot["menu_worker_schema"].append({"version": 7})
        with self.assertRaisesRegex(StopRun, "recovery_database_changed"):
            self.apply(plan)
        self.assertEqual(self.checkpoint.read_bytes(), self.original)

    def test_running_or_queued_jobs_and_running_attempts_are_rejected(self):
        for table, status in (("content_generation_job", "running"), ("content_generation_job", "queued"),
                              ("content_generation_attempt", "running")):
            with self.subTest(table=table, status=status):
                row = self.snapshot[table][0]
                previous = row["execution_status"]
                row["execution_status"] = status
                with self.assertRaisesRegex(StopRun, "recovery_database_not_idle"):
                    self.plan()
                row["execution_status"] = previous

    def test_nonzero_confirmed_exit_and_bound_launch_required(self):
        for value in (0, None, True, "1"):
            write_json(self.directory / "exit.json", {"returncode": value})
            with self.assertRaisesRegex(StopRun, "recovery_exit_not_confirmed"):
                self.plan()
        write_json(self.directory / "exit.json", {"returncode": 1})
        write_json(self.directory / "launch.json", {**_identity(self.job), "input_sha256": "0" * 64})
        with self.assertRaisesRegex(StopRun, "recovery_launch_mismatch"):
            self.plan()

    def test_result_ready_for_review_or_invalid_json_cannot_be_skipped(self):
        write_json(self.directory / "result.json", {"status": "ready_for_feed"})
        with self.assertRaisesRegex(StopRun, "recovery_result_not_eligible"):
            self.plan()
        (self.directory / "result.json").write_text('{"status": "generation_failed", "x": NaN}')
        with self.assertRaisesRegex(StopRun, "invalid_json_number"):
            self.plan()

    def test_partial_receipts_remain_known_but_total_never_becomes_zero(self):
        write_json(self.directory / "result.json", {"status": "generation_failed", "request_id": "crawl-fixture",
            "expected_work_request": _request(self.job),
            "failure": {"stage": "content_generation", "error_type": "TypeError"},
            "cost_accounting": {"complete": False},
            "usage_records": [{"estimated_cost_usd": "0.003"}, {"stage": "unknown_receipt"}]})
        receipt = self.apply()
        self.assertEqual(receipt["known_partial_estimated_usd"], "0.003")
        self.assertTrue(receipt["unknown_total"])
        self.assertIsNone(read_json(self.checkpoint)["records"][0]["estimated_cost_usd"])

    def test_artifact_mutation_before_apply_and_after_apply_is_rejected(self):
        plan = self.plan()
        data = self.directory / "agent.sqlite3"
        original = data.read_bytes()
        data.write_bytes(b"changed")
        with self.assertRaisesRegex(StopRun, "recovery_plan_evidence_changed"):
            self.apply(plan)
        data.write_bytes(original)
        self.apply(plan)
        data.write_bytes(b"changed again")
        with self.assertRaisesRegex(StopRun, "failure_recovery_artifacts_changed"):
            recovery.validate_recovery_history(read_json(self.checkpoint), store=self)

    def test_immutable_backup_and_original_history_are_bound(self):
        receipt = self.apply()
        state = read_json(self.checkpoint)
        backup = Path(receipt["checkpoint_backup"])
        self.assertEqual(backup.read_bytes(), self.original)
        backup.write_bytes(b"{}")
        with self.assertRaisesRegex(StopRun, "failure_recovery_backup_changed"):
            recovery.validate_recovery_history(state, store=self)

    def test_receipt_audit_and_row_fields_cannot_be_changed(self):
        self.apply()
        state = read_json(self.checkpoint)
        for mutate in (lambda value: value["failure_recovery_audit"].clear(),
                       lambda value: value["records"][0].update(estimated_cost_usd="0"),
                       lambda value: value["records"][0].update(counts_slot=False),
                       lambda value: value["config"].update(limit_mode="legacy")):
            changed = copy.deepcopy(state)
            mutate(changed)
            with self.assertRaises(StopRun):
                recovery.validate_recovery_history(changed, store=self)

    def test_runtime_accepts_new_unrelated_jobs_but_rejects_failed_history_change(self):
        self.apply()
        state = read_json(self.checkpoint)
        self.snapshot["content_generation_job"].append({"job_id": str(uuid4()), "execution_status": "succeeded"})
        recovery.validate_recovery_history(state, store=self)
        self.attempt["finished_at"] = "2026-11-26T02:00:00+00:00"
        with self.assertRaisesRegex(StopRun, "failure_recovery_job_history_changed"):
            recovery.validate_recovery_history(state, store=self)

    def test_crash_after_receipt_before_checkpoint_can_be_reapplied_exactly_once(self):
        plan = self.plan()
        with patch.object(recovery, "write_json", side_effect=OSError("synthetic crash")):
            with self.assertRaises(OSError):
                self.apply(plan)
        self.assertEqual(self.checkpoint.read_bytes(), self.original)
        self.apply(plan)
        self.assertEqual(len(read_json(self.checkpoint)["failure_recovery_audit"]), 1)

    @unittest.skipIf(os.name == "nt", "POSIX private receipt permissions")
    def test_existing_public_recovery_directory_is_rejected(self):
        plan = self.plan()
        directory = self.checkpoint.with_name(self.checkpoint.name + ".failure-recovery")
        directory.mkdir()
        directory.chmod(0o777)
        with self.assertRaisesRegex(StopRun, "unsafe_recovery_directory_permissions"):
            self.apply(plan)
        self.assertEqual(self.checkpoint.read_bytes(), self.original)
        self.assertEqual(list(directory.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "Symlink fixture requires a POSIX test container")
    def test_linked_artifact_cannot_enter_recovery_evidence(self):
        (self.directory / "linked.log").symlink_to(self.checkpoint)
        with self.assertRaisesRegex(StopRun, "unsafe_recovery_artifact"):
            self.plan()
        self.assertEqual(self.checkpoint.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
