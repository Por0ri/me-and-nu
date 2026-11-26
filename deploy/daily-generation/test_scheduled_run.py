"""Offline host-supervisor tests: Docker, providers and service DB are never called."""
import copy
from datetime import datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).with_name("scheduled_run.py")
spec = importlib.util.spec_from_file_location("daily_scheduled_run", MODULE)
scheduler = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scheduler
spec.loader.exec_module(scheduler)


class SimulatedCrash(RuntimeError):
    pass


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.release = self.root / "release"
        self.state.mkdir(); self.release.mkdir()
        self.env = self.release / "private.env"
        self.env.write_text("CONTENT_JOB_DB_PASSWORD=synthetic\nMAIN_POSTGRES_PASSWORD=synthetic\n")
        os.chmod(self.env, 0o600)
        for name in ("compose.runtime.yaml", "compose.approved.yaml"):
            (self.release / name).write_text("# synthetic test fixture, never executed\n")
        self.routes = [{"source_id": "one_piece_official", "content_type": "goods", "menu_work_id": 8}]
        self.seed = {"schema_version": 1, "status": "complete", "stop_reason": "new_job_slots_consumed",
            "records": [{"state": "complete", "counts_slot": True, "job_id": "legacy",
                         "estimated_cost_usd": "1.00"}], "seen": ["old"], "accounting_resume_audit": [{"old": True}],
            "config": {"run_id": "beta-open-20261116", "max_new_jobs": 1, "bundle_root": "/runtime/state/bundles",
                       "job_timeout_seconds": 1500, "total_budget_usd": "5"}}
        scheduler.save_json(self.state / "seed.json", self.seed)
        self.policy = {"schema_version": 1, "purpose": "daily_count_schedule", "release_sha": "a" * 40,
            "seed_checkpoint": "seed.json", "seed_sha256": scheduler.file_sha(self.state / "seed.json"),
            "routes": self.routes, "max_attempts": 40, "success_target": 20, "target": scheduler.TARGET.copy()}
        self.now = datetime(2026, 11, 26, 10, tzinfo=scheduler.KST)
        self.calls = []
        self.crash = None
        self.failed_stage = None
        self.changed_scope = False
        self.attempts = 20
        self.successes = 20
        self.generation_stop = None
        self.import_calls = 0

    def supervisor(self):
        return scheduler.Supervisor(policy=copy.deepcopy(self.policy), reviewed_sha256=scheduler.digest(self.policy),
            state_root=self.state, runtime_env=self.env, release_dir=self.release,
            now=lambda: self.now, runner=self.runner, owner=None)

    def entry(self):
        return scheduler.read_json(self.state / "scheduled" / "journal.json")["days"][-1]

    def runner(self, stage, args, **kwargs):
        self.calls.append((stage, args))
        self.assertEqual(args[:7], ["docker", "--context", "default", "compose", "--project-name", "menu-daily-generation", "--env-file"])
        self.assertNotIn("shell", kwargs)
        if self.crash == stage:
            self.crash = None
            raise SimulatedCrash(stage)
        if self.failed_stage == stage:
            return scheduler.CommandResult(1, "", "private synthetic failure")
        entry = self.entry()
        parent = scheduler.read_json(self.state / entry["source"])
        directory = self.state / entry["directory"]
        if stage == "plan":
            policy = {"schema_version": 3, "limit_mode": "daily_counts", "routes": copy.deepcopy(self.routes),
                "max_attempts": 40, "success_target": 20, "policy_date": entry["date"],
                "delivery_root": "/runtime/state/" + entry["directory"] + "/inbox",
                "deadline": (datetime.fromisoformat(entry["date"]).replace(tzinfo=scheduler.KST) + timedelta(days=1)).isoformat()}
            if self.changed_scope:
                policy["routes"].append({"source_id": "unapproved", "content_type": "news", "menu_work_id": 999})
            config = {**parent["config"], "limit_mode": "daily_counts", "total_budget_usd": None,
                      "deadline": policy["deadline"], "max_new_jobs": len(parent["records"]) + 40}
            plan = {"config": config, "daily_policy": policy, "day_policy_sha256": scheduler.digest(policy),
                "source_checkpoint": "/runtime/state/" + entry["source"], "source_sha256": entry["source_sha256"],
                "checkpoint": "/runtime/state/" + entry["checkpoint"], "generation_enabled": False}
            return scheduler.CommandResult(0, json.dumps({"plan": plan, "plan_sha256": scheduler.digest(plan)}))
        if stage in {"preflight", "resume-audit"}:
            return scheduler.CommandResult(0, json.dumps({"verified": True, "generation_enabled": False}))
        if stage == "recovery-audit":
            reference = {"receipt_path": args[-3], "receipt_sha256": args[-2]}
            child = scheduler.read_json(self.state / entry["checkpoint"])
            self.assertEqual(child["failure_recovery_audit"][-1], reference)
            self.assertTrue(any(row.get("failure_recovery_receipt") == reference for row in child["records"]))
            deadline = datetime.fromisoformat(child["config"]["deadline"])
            return scheduler.CommandResult(0, json.dumps({"verified": True, "generation_enabled": False,
                "receipt_sha256": args[-2], "status": child["status"],
                "generation_window_open": self.now + timedelta(seconds=1500) < deadline}))
        if stage == "generate":
            plan = scheduler.read_json(directory / "plan.json")
            child = scheduler.read_json(self.state / entry["checkpoint"]) if "--resume-failure-recovery" in args else copy.deepcopy(parent)
            if "--resume-failure-recovery" in args and self.now + timedelta(seconds=1500) >= datetime.fromisoformat(plan["config"]["deadline"]):
                child.update(status="stopped", stop_reason="deadline_reached")
                scheduler.save_json(self.state / entry["checkpoint"], child)
                return scheduler.CommandResult(2, "{}")
            child.update(config=plan["config"], daily_policy=plan["daily_policy"], daily_plan_sha256=scheduler.digest(plan),
                         status="complete", stop_reason="daily_success_target_reached" if self.successes == 20 else "daily_attempt_limit_reached")
            successes = sum(row.get("outcome") == "ready_for_staging" for row in child["records"][len(parent["records"]):])
            for i in range(len(child["records"]) - len(parent["records"]), self.attempts):
                url = "https://example.invalid/" + entry["date"] + "/" + str(i)
                succeeds = successes < self.successes
                child["records"].append({"state": "complete", "counts_slot": True,
                    "job_id": entry["date"] + "-" + str(i), "request": self.routes[0], "canonical_url": url,
                    "execution_status": "succeeded", "outcome": "ready_for_staging" if succeeds else "skipped",
                    "estimated_cost_usd": "0.01"})
                child["seen"].append(url)
                successes += succeeds
            if self.generation_stop:
                child.update(status="stopped", stop_reason=self.generation_stop)
            scheduler.save_json(self.state / entry["checkpoint"], child)
            if self.crash == "after-generate":
                self.crash = None
                raise SimulatedCrash("after-generate")
            return scheduler.CommandResult(2 if self.generation_stop else 0, "{}")
        if stage == "broker-empty":
            return scheduler.CommandResult(0, json.dumps({"ready": 0, "unacked": 0, "unacked_index": 0}))
        if stage == "import":
            self.import_calls += 1
            return scheduler.CommandResult(0, json.dumps({"created": self.successes, "unchanged": 0,
                "publication_enabled": False, "operator_approvals_created": 0, "llm_calls": 0}))
        return scheduler.CommandResult(0)

    def reconciled_failure_fixture(self, *, held=False):
        """Model core recovery only; the supervisor must not change this evidence."""
        self.attempts, self.successes = 3, 2
        self.generation_stop = "job_requires_attention"
        with self.assertRaisesRegex(scheduler.ScheduleError, "day_requires_reconciliation"):
            self.supervisor().run(apply=True)
        path = self.state / self.entry()["checkpoint"]
        child = scheduler.read_json(path)
        failed = child["records"][-1]
        failed.update(execution_status="needs_attention", outcome=None, estimated_cost_usd=None,
                      accounting_method="unknown", cost_unknown=True)
        if held:
            failed.update(execution_status="succeeded", outcome="held", producer_status="expiry_unresolved_held",
                          result_sha256="b" * 64, generation_attempt_id="retained-held-attempt")
        receipt = {"purpose": "synthetic_failure_recovery", "unknown_total": True,
                   "checkpoint": "/runtime/state/" + self.entry()["checkpoint"], "original_record": copy.deepcopy(failed)}
        if held:
            receipt["operation"] = "reconcile_held_generation_unknown_cost"
        receipt_path = self.state / "failure-receipt.json"
        scheduler.save_json(receipt_path, receipt)
        reference = {"receipt_path": "/runtime/state/failure-receipt.json", "receipt_sha256": scheduler.digest(receipt)}
        failed["failure_recovery_receipt"] = reference
        child.update(status="stopped", stop_reason="failure_reconciled_paused", failure_recovery_audit=[reference])
        scheduler.save_json(path, child)
        self.generation_stop = None
        return {"recovery_receipt": receipt_path, "reviewed_recovery_sha256": scheduler.digest(receipt)}, child

    def test_held_reconciliation_does_not_start_worker_or_import_and_requires_explicit_resume(self):
        recovery, original = self.reconciled_failure_fixture(held=True)
        before = len(self.calls)
        report = self.supervisor().run(reconcile=True, **recovery)
        self.assertEqual(report["status"], "reconciled_paused")
        self.assertEqual([stage for stage, _ in self.calls[before:]], ["recovery-audit"])
        self.assertEqual((report["queue_writes"], report["service_db_writes"], report["llm_calls"]), (0, 0, 0))
        before = len(self.calls)
        for mode in ({"apply": True}, {"preflight": True}):
            with self.assertRaisesRegex(scheduler.ScheduleError, "explicit_failure_resume_required"):
                self.supervisor().run(**mode)
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.import_calls, 0)
        self.attempts, self.successes = 21, 20
        report = self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual((report["attempts"], report["successes"]), (21, 20))
        child = scheduler.read_json(self.state / self.entry()["checkpoint"])
        self.assertEqual(child["records"][:len(original["records"])], original["records"])
        self.assertEqual((child["records"][3]["execution_status"], child["records"][3]["outcome"]), ("succeeded", "held"))
        before = len(self.calls)
        self.assertEqual(self.supervisor().run(resume_reconciled=True, **recovery)["status"], "already_complete")
        self.assertEqual(len(self.calls), before)

    def test_held_recovery_counts_toward_forty_and_crash_retry_does_not_generate_again(self):
        recovery, original = self.reconciled_failure_fixture(held=True)
        self.supervisor().run(reconcile=True, **recovery)
        self.attempts, self.successes = 40, 2
        self.crash = "after-generate"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(resume_reconciled=True, **recovery)
        before = len(self.calls)
        with self.assertRaisesRegex(scheduler.ScheduleError, "explicit_failure_resume_required"):
            self.supervisor().run(apply=True)
        self.assertEqual(len(self.calls), before)
        report = self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual((report["attempts"], report["successes"]), (40, 2))
        self.assertEqual([stage for stage, _ in self.calls[before:]], ["recovery-audit", "broker-empty", "worker-stop", "import"])
        child = scheduler.read_json(self.state / self.entry()["checkpoint"])
        self.assertEqual(child["records"][:len(original["records"])], original["records"])

    def test_held_previous_day_finalizes_without_worker_and_preserves_result_on_rollover(self):
        recovery, original = self.reconciled_failure_fixture(held=True)
        self.supervisor().run(reconcile=True, **recovery)
        self.now += timedelta(days=1)
        before = len(self.calls)
        report = self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual((report["policy_date"], report["attempts"], report["successes"]), ("2026-11-26", 3, 2))
        self.assertNotIn("worker-start", [stage for stage, _ in self.calls[before:]])
        imported = next(args for stage, args in self.calls[before:] if stage == "import")
        self.assertEqual(imported[-2:], ["--reconcile-policy-date", "2026-11-26"])
        self.assertFalse((self.state / "daily-2026-11-27.json").exists())
        self.attempts, self.successes = 20, 20
        self.supervisor().run(apply=True)
        child = scheduler.read_json(self.state / "daily-2026-11-27.json")
        self.assertEqual(child["records"][:len(original["records"])], original["records"])
        self.assertEqual(child["failure_recovery_audit"], original["failure_recovery_audit"])
        self.assertEqual(child["records"][3]["result_sha256"], "b" * 64)
        self.assertIsNone(child["records"][3]["estimated_cost_usd"])

    def test_failure_reconciliation_is_paused_and_preserves_all_work_and_cost_evidence(self):
        recovery, original = self.reconciled_failure_fixture()
        before = len(self.calls)
        report = self.supervisor().run(reconcile=True, **recovery)
        self.assertEqual(report["status"], "reconciled_paused")
        self.assertEqual([stage for stage, _ in self.calls[before:]], ["recovery-audit"])
        self.assertEqual((report["job_checkpoint_writes"], report["queue_writes"], report["service_db_writes"], report["llm_calls"]), (0, 0, 0, 0))
        self.assertEqual(scheduler.read_json(self.state / self.entry()["checkpoint"]), original)
        self.assertIsNone(original["records"][-1]["estimated_cost_usd"])
        self.assertEqual(original["records"][0]["estimated_cost_usd"], "1.00")
        self.assertEqual(self.entry()["blocked_reason"], "day_requires_reconciliation")
        saved = (self.state / "scheduled/journal.json").read_bytes()
        self.supervisor().run(reconcile=True, **recovery)
        self.assertEqual((self.state / "scheduled/journal.json").read_bytes(), saved)
        before = len(self.calls)
        for mode in ({"apply": True}, {"preflight": True}):
            with self.assertRaisesRegex(scheduler.ScheduleError, "explicit_failure_resume_required"):
                self.supervisor().run(**mode)
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.import_calls, 0)

    def test_explicit_resume_continues_next_article_and_next_day_inherits_unknown_cost(self):
        recovery, original = self.reconciled_failure_fixture()
        self.supervisor().run(reconcile=True, **recovery)
        self.attempts, self.successes = 21, 20
        before = len(self.calls)
        report = self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual((report["attempts"], report["successes"]), (21, 20))
        stages = [stage for stage, _ in self.calls[before:]]
        self.assertEqual(stages, ["recovery-audit", "worker-start", "generate", "broker-empty", "worker-stop", "import"])
        generated = next(args for stage, args in self.calls[before:] if stage == "generate")
        self.assertEqual(generated[-2:], ["--resume-failure-recovery", recovery["reviewed_recovery_sha256"]])
        first = scheduler.read_json(self.state / self.entry()["checkpoint"])
        self.assertEqual(first["records"][:len(original["records"])], original["records"])
        self.assertEqual(first["seen"][:len(original["seen"])], original["seen"])
        self.assertEqual(len({row.get("canonical_url") for row in first["records"][1:]}), 21)
        calls = len(self.calls)
        self.assertEqual(self.supervisor().run(resume_reconciled=True, **recovery)["status"], "already_complete")
        self.assertEqual(len(self.calls), calls)
        self.now += timedelta(days=1)
        self.attempts = 20
        self.supervisor().run(apply=True)
        second = scheduler.read_json(self.state / self.entry()["checkpoint"])
        self.assertEqual(second["records"][:len(first["records"])], first["records"])
        self.assertEqual(second["failure_recovery_audit"], first["failure_recovery_audit"])
        self.assertTrue(second["records"][3]["cost_unknown"])

    def test_explicit_resume_after_controller_crash_stays_paused_and_does_not_regenerate(self):
        recovery, _ = self.reconciled_failure_fixture()
        self.supervisor().run(reconcile=True, **recovery)
        self.attempts, self.successes = 21, 20
        self.crash = "after-generate"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual(self.entry()["phase"], "reconciled_paused")
        calls = len(self.calls)
        with self.assertRaisesRegex(scheduler.ScheduleError, "explicit_failure_resume_required"):
            self.supervisor().run(apply=True)
        self.assertEqual(len(self.calls), calls)
        self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual([stage for stage, _ in self.calls[calls:]], ["recovery-audit", "broker-empty", "worker-stop", "import"])

    def test_recovery_receipt_or_audit_change_cannot_enable_worker(self):
        recovery, _ = self.reconciled_failure_fixture()
        before = len(self.calls)
        bad = {**recovery, "reviewed_recovery_sha256": "0" * 64}
        with self.assertRaisesRegex(scheduler.ScheduleError, "reviewed_recovery_digest_changed"):
            self.supervisor().run(reconcile=True, **bad)
        self.assertEqual(len(self.calls), before)
        self.failed_stage = "recovery-audit"
        with self.assertRaisesRegex(scheduler.ScheduleError, "failure_recovery_audit_failed"):
            self.supervisor().run(reconcile=True, **recovery)
        self.assertEqual(self.entry()["phase"], "blocked")
        self.assertNotIn("worker-start", [stage for stage, _ in self.calls[before:]])

    def test_reconciled_receipt_tamper_or_db_audit_failure_still_blocks_resume(self):
        recovery, _ = self.reconciled_failure_fixture()
        self.supervisor().run(reconcile=True, **recovery)
        before = len(self.calls)
        receipt = recovery["recovery_receipt"]
        original = receipt.read_bytes()
        receipt.write_bytes(original + b" ")  # Whitespace preserves canonical identity.
        self.failed_stage = "recovery-audit"
        with self.assertRaisesRegex(scheduler.ScheduleError, "failure_recovery_audit_failed"):
            self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual(self.entry()["phase"], "reconciled_paused")
        self.assertEqual([stage for stage, _ in self.calls[before:]], ["recovery-audit"])
        receipt.write_text("{}")
        before = len(self.calls)
        with self.assertRaisesRegex(scheduler.ScheduleError, "reviewed_recovery_digest_changed"):
            self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.import_calls, 0)

    def test_a_later_failure_needs_a_new_receipt_and_keeps_both_unknown_attempts(self):
        recovery, _ = self.reconciled_failure_fixture()
        self.supervisor().run(reconcile=True, **recovery)
        self.attempts, self.successes, self.generation_stop = 4, 2, "job_requires_attention"
        with self.assertRaisesRegex(scheduler.ScheduleError, "day_requires_reconciliation"):
            self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual(self.entry()["phase"], "blocked")
        old_history = copy.deepcopy(self.entry()["failure_recoveries"])
        with self.assertRaisesRegex(scheduler.ScheduleError, "recovery_requires_paused_schedule"):
            self.supervisor().run(resume_reconciled=True, **recovery)
        checkpoint = self.state / self.entry()["checkpoint"]
        child = scheduler.read_json(checkpoint)
        row = child["records"][-1]
        row.update(execution_status="needs_attention", outcome=None, estimated_cost_usd=None,
                   accounting_method="unknown", cost_unknown=True)
        receipt = {"purpose": "synthetic_failure_recovery", "unknown_total": True, "original_record": copy.deepcopy(row)}
        path = self.state / "second-failure-receipt.json"
        scheduler.save_json(path, receipt)
        reference = {"receipt_path": "/runtime/state/second-failure-receipt.json", "receipt_sha256": scheduler.digest(receipt)}
        row["failure_recovery_receipt"] = reference
        child["failure_recovery_audit"].append(reference)
        child.update(status="stopped", stop_reason="failure_reconciled_paused")
        scheduler.save_json(checkpoint, child)
        second = {"recovery_receipt": path, "reviewed_recovery_sha256": scheduler.digest(receipt)}
        self.supervisor().run(reconcile=True, **second)
        self.assertEqual(self.entry()["failure_recoveries"][:1], old_history)
        self.attempts, self.successes, self.generation_stop = 22, 20, None
        report = self.supervisor().run(resume_reconciled=True, **second)
        final = scheduler.read_json(checkpoint)
        self.assertEqual((report["attempts"], report["successes"]), (22, 20))
        self.assertEqual(final["records"][:len(child["records"])], child["records"])
        self.assertEqual(len(self.entry()["failure_recoveries"]), 2)
        self.assertEqual(sum(row.get("cost_unknown", False) for row in final["records"]), 2)

    def test_previous_day_reconciled_failure_requires_explicit_finalize_and_import_before_rollover(self):
        recovery, _ = self.reconciled_failure_fixture()
        self.supervisor().run(reconcile=True, **recovery)
        self.now += timedelta(days=1)
        before = len(self.calls)
        with self.assertRaisesRegex(scheduler.ScheduleError, "explicit_failure_resume_required"):
            self.supervisor().run(apply=True)
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.import_calls, 0)
        self.assertFalse((self.state / "daily-2026-11-27.json").exists())
        report = self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual((report["policy_date"], report["attempts"], report["successes"]), ("2026-11-26", 3, 2))
        self.assertNotIn("worker-start", [stage for stage, _ in self.calls[before:]])
        imported = next(args for stage, args in self.calls[before:] if stage == "import")
        self.assertEqual(imported[-2:], ["--reconcile-policy-date", "2026-11-26"])
        self.assertEqual(self.import_calls, 1)
        self.assertFalse((self.state / "daily-2026-11-27.json").exists())
        previous = scheduler.read_json(self.state / "daily-2026-11-26.json")
        self.assertEqual(previous["stop_reason"], "deadline_reached")
        self.attempts, self.successes = 20, 20
        self.supervisor().run(apply=True)
        current = scheduler.read_json(self.state / "daily-2026-11-27.json")
        self.assertEqual(current["records"][:len(previous["records"])], previous["records"])
        self.assertTrue(current["records"][3]["cost_unknown"])

    def test_old_day_import_crash_needs_explicit_retry_without_generation_or_rollover(self):
        recovery, _ = self.reconciled_failure_fixture()
        self.supervisor().run(reconcile=True, **recovery)
        self.now += timedelta(days=1)
        self.crash = "import"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual(self.entry()["phase"], "importing")
        before = len(self.calls)
        with self.assertRaisesRegex(scheduler.ScheduleError, "explicit_failure_resume_required"):
            self.supervisor().run(apply=True)
        self.assertEqual(len(self.calls), before)
        self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual([stage for stage, _ in self.calls[before:]], ["recovery-audit", "broker-empty", "worker-stop", "import"])

    def test_same_day_recovery_import_crash_blocks_timer_and_preflight_until_explicit_retry(self):
        recovery, _ = self.reconciled_failure_fixture()
        self.supervisor().run(reconcile=True, **recovery)
        self.attempts, self.successes = 21, 20
        self.crash = "import"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual(self.entry()["phase"], "importing")
        before = len(self.calls)
        for mode in ({"apply": True}, {"preflight": True}):
            with self.assertRaisesRegex(scheduler.ScheduleError, "explicit_failure_resume_required"):
                self.supervisor().run(**mode)
        self.assertEqual(len(self.calls), before)
        self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual([stage for stage, _ in self.calls[before:]], ["recovery-audit", "broker-empty", "worker-stop", "import"])

    def test_default_status_does_not_write_or_call_docker(self):
        before = sorted(self.root.rglob("*"))
        report = self.supervisor().run()
        self.assertEqual(report["phase"], "not_started")
        self.assertEqual(report["service_db_writes"], 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(sorted(self.root.rglob("*")), before)

    def test_preflight_only_prepares_and_audits_then_apply_reuses_exact_plan(self):
        report = self.supervisor().run(preflight=True)
        self.assertEqual(report["status"], "prepared")
        self.assertTrue(report["host_state_writes"])
        self.assertFalse(report["generation_enabled"])
        self.assertEqual((report["llm_calls"], report["queue_writes"], report["service_db_writes"],
                          report["job_checkpoint_writes"]), (0, 0, 0, 0))
        self.assertEqual([stage for stage, _ in self.calls], ["plan", "preflight"])
        self.assertFalse((self.state / "daily-2026-11-26.json").exists())
        plan = (self.state / "scheduled/2026-11-26/plan.json").read_bytes()
        self.supervisor().run(preflight=True)
        self.assertEqual([stage for stage, _ in self.calls], ["plan", "preflight", "preflight"])
        self.supervisor().run(apply=True)
        self.assertEqual((self.state / "scheduled/2026-11-26/plan.json").read_bytes(), plan)
        self.assertEqual(sum(stage == "plan" for stage, _ in self.calls), 1)

    def test_preflight_refuses_started_day_without_changing_phase(self):
        self.crash = "worker-start"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(apply=True)
        original = (self.state / "scheduled/journal.json").read_bytes()
        calls = len(self.calls)
        with self.assertRaisesRegex(scheduler.ScheduleError, "preflight_requires_unstarted_day"):
            self.supervisor().run(preflight=True)
        self.assertEqual((self.state / "scheduled/journal.json").read_bytes(), original)
        self.assertEqual(len(self.calls), calls)

    def test_two_days_keep_history_and_flat_checkpoints_and_twenty_cap(self):
        self.assertEqual(self.supervisor().run(apply=True)["successes"], 20)
        first = scheduler.read_json(self.state / "daily-2026-11-26.json")
        self.now += timedelta(days=1)
        report = self.supervisor().run(apply=True)
        self.assertEqual(report["successes"], 20)
        second = scheduler.read_json(self.state / "daily-2026-11-27.json")
        self.assertEqual(second["records"][:len(first["records"])], first["records"])
        self.assertEqual(second["accounting_resume_audit"], self.seed["accounting_resume_audit"])
        self.assertEqual(self.entry()["source"], "daily-2026-11-26.json")
        self.assertEqual(self.entry()["attempts"], 20)
        self.assertEqual(len(second["records"]), 41)
        self.assertEqual(self.import_calls, 2)
        self.assertEqual(scheduler.read_json(self.state / "seed.json"), self.seed)

    def test_repeated_same_day_tick_never_restarts_worker_or_resets_limits(self):
        self.supervisor().run(apply=True)
        calls = len(self.calls)
        raw = (self.state / "scheduled" / "journal.json").read_bytes()
        report = self.supervisor().run(apply=True)
        self.assertEqual(report["status"], "already_complete")
        self.assertEqual(len(self.calls), calls)
        self.assertEqual((self.state / "scheduled" / "journal.json").read_bytes(), raw)

    def test_forty_attempts_can_finish_with_less_than_twenty_successes(self):
        self.attempts, self.successes = 40, 3
        report = self.supervisor().run(apply=True)
        self.assertEqual((report["attempts"], report["successes"]), (40, 3))
        self.assertEqual(self.entry()["imported"], 3)

    def test_crash_after_immutable_plan_before_generation_reuses_same_plan(self):
        self.crash = "worker-start"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(apply=True)
        original = (self.state / "scheduled/2026-11-26/plan.json").read_bytes()
        self.supervisor().run(apply=True)
        self.assertEqual((self.state / "scheduled/2026-11-26/plan.json").read_bytes(), original)
        self.assertEqual(sum(stage == "plan" for stage, _ in self.calls), 1)
        self.assertEqual(sum(stage == "generate" for stage, _ in self.calls), 1)

    def test_crash_before_import_resumes_import_without_regeneration(self):
        self.crash = "import"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(apply=True)
        self.assertEqual(self.entry()["phase"], "importing")
        self.supervisor().run(apply=True)
        self.assertEqual(sum(stage == "generate" for stage, _ in self.calls), 1)
        self.assertEqual(self.import_calls, 1)

    def test_completed_checkpoint_restart_audits_db_and_does_not_start_worker_again(self):
        self.crash = "after-generate"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(apply=True)
        self.assertEqual(self.entry()["phase"], "generating")
        self.supervisor().run(apply=True)
        self.assertEqual(sum(stage == "resume-audit" for stage, _ in self.calls), 1)
        self.assertEqual(sum(stage == "worker-start" for stage, _ in self.calls), 1)
        self.assertEqual(sum(stage == "generate" for stage, _ in self.calls), 1)

    def test_failed_resume_audit_blocks_worker_before_consuming_queue(self):
        self.crash = "after-generate"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(apply=True)
        self.failed_stage = "resume-audit"
        with self.assertRaisesRegex(scheduler.ScheduleError, "resume_history_audit_failed"):
            self.supervisor().run(apply=True)
        self.assertEqual(sum(stage == "worker-start" for stage, _ in self.calls), 1)
        self.assertEqual(self.import_calls, 0)

    def test_previous_day_not_drained_blocks_new_day(self):
        self.crash = "import"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(apply=True)
        self.now += timedelta(days=1)
        before = len(self.calls)
        with self.assertRaisesRegex(scheduler.ScheduleError, "previous_day_requires_reconciliation"):
            self.supervisor().run(apply=True)
        self.assertEqual(len(self.calls), before)
        self.assertFalse((self.state / "daily-2026-11-27.json").exists())

    def test_changed_catalog_scope_blocks_before_worker_start(self):
        self.changed_scope = True
        with self.assertRaisesRegex(scheduler.ScheduleError, "generated_plan_outside_schedule"):
            self.supervisor().run(apply=True)
        self.assertEqual([stage for stage, _ in self.calls], ["plan"])
        self.assertEqual(self.entry()["phase"], "blocked")

    def test_unknown_cost_stop_never_imports_or_auto_retries(self):
        self.generation_stop = "missing_cost_evidence"
        with self.assertRaisesRegex(scheduler.ScheduleError, "day_requires_reconciliation"):
            self.supervisor().run(apply=True)
        self.assertNotIn("import", [stage for stage, _ in self.calls])
        before = len(self.calls)
        with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_requires_reconciliation"):
            self.supervisor().run(apply=True)
        self.assertEqual(len(self.calls), before)

    def test_unsupported_limits_or_unapproved_hash_are_rejected(self):
        self.policy["max_attempts"] = 41
        with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_limits_changed"):
            self.supervisor()
        self.policy["max_attempts"] = 40
        with self.assertRaisesRegex(scheduler.ScheduleError, "reviewed_schedule_digest_required"):
            scheduler.Supervisor(policy=self.policy, reviewed_sha256="0" * 64, state_root=self.state,
                                 runtime_env=self.env, release_dir=self.release)

    def test_changed_completed_checkpoint_or_environment_is_not_reused(self):
        self.supervisor().run(apply=True)
        original = self.env.read_bytes()
        self.env.write_bytes(original + b"EXTRA=changed\n")
        with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_configuration_changed"):
            self.supervisor().run()
        self.env.write_bytes(original)
        (self.state / "daily-2026-11-26.json").write_text("{}")
        with self.assertRaisesRegex(scheduler.ScheduleError, "completed_day_checkpoint_changed"):
            self.supervisor().run()

    @unittest.skipIf(os.name == "nt", "Production supervisor uses POSIX flock")
    def test_concurrent_supervisor_lock_blocks_without_commands(self):
        with scheduler.exclusive_lock(self.state / "daily-schedule.lock"):
            with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_already_running"):
                self.supervisor().run(apply=True)
        self.assertEqual(self.calls, [])

    def test_importer_is_once_pending_only_and_generation_is_sequential(self):
        self.supervisor().run(apply=True)
        stages = [stage for stage, _ in self.calls]
        self.assertEqual(stages, ["plan", "preflight", "worker-start", "generate", "broker-empty", "worker-stop", "import"])
        args = dict(self.calls)["import"]
        self.assertIn("--once", args)
        self.assertNotIn("--watch", args)
        self.assertNotIn("--approve", args)
        self.assertNotIn("--reconcile-policy-date", args)

    @unittest.skipUnless(os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0,
                         "Requires disposable Linux test container running as root")
    def test_producer_can_write_inbox_but_not_read_or_replace_private_metadata(self):
        os.chmod(self.root, 0o755)
        os.chmod(self.state, 0o755)
        supervisor = self.supervisor()
        supervisor.owner = 10001
        supervisor.run(apply=True)
        directory = self.state / "scheduled/2026-11-26"
        def become_producer():
            os.setgid(10001)
            os.setuid(10001)
        code = """from pathlib import Path
import sys
root = Path(sys.argv[1])
(root / 'inbox' / 'probe.json').write_text('{}')
assert (root / 'import-policy.json').read_text()
for name in ('runtime.env', 'plan.json'):
    try: (root / name).read_bytes()
    except PermissionError: pass
    else: raise AssertionError('producer read private host metadata')
    try: (root / name).unlink()
    except PermissionError: pass
    else: raise AssertionError('producer replaced host metadata')
"""
        result = subprocess.run([sys.executable, "-c", code, str(directory)], preexec_fn=become_producer,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
