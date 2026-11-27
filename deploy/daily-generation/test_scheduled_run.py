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
from unittest.mock import patch

MODULE = Path(__file__).with_name("scheduled_run.py")
spec = importlib.util.spec_from_file_location("daily_scheduled_run", MODULE)
scheduler = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scheduler
spec.loader.exec_module(scheduler)

RAG_CONTRACT = MODULE.parents[2] / "worker/menu_worker/rag_contract.py"
rag_spec = importlib.util.spec_from_file_location("host_test_rag_contract", RAG_CONTRACT)
rag_contract = importlib.util.module_from_spec(rag_spec)
sys.modules[rag_spec.name] = rag_contract
rag_spec.loader.exec_module(rag_contract)


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


class MixedScheduleTests(unittest.TestCase):
    """Exercise the real host orchestration with simulated container responses."""
    supervisor = ScheduleTests.supervisor
    entry = ScheduleTests.entry
    reconciled_failure_fixture = ScheduleTests.reconciled_failure_fixture

    def setUp(self):
        ScheduleTests.setUp(self)
        self.seed["config"]["artifact_root"] = "/var/lib/menu-jobs"
        scheduler.save_json(self.state / "seed.json", self.seed)
        self.policy["seed_sha256"] = scheduler.file_sha(self.state / "seed.json")
        self.rag_successes = 8
        self.rag_import_calls = 0
        self.rag_report_delta = 0
        self.environments = []
        self.rag_started = False
        self.foreign_before = False
        self.after_mismatch = None

    def mixed(self, *, enabled=False, feed_valid_days=7):
        (self.release / "rag").mkdir(exist_ok=True)
        for name in scheduler.RAG_COMPOSE:
            (self.release / name).write_text("# mixed fixture; never executed\n")
        scope = {"schema_version": 1, "purpose": "rag_daily_generation", "routes": [
            {"namespace": "dictionary_v1", "menu_work_id": 8, "content_type": "guide", "allowed_source_ids": ["source-a"]}]}
        scheduler.save_json(self.state / "rag-scope.json", scope)
        runtime = {"enabled": enabled, "offline": False, "confirm_isolated_copy": True,
            "isolated_root": "/var/lib/menu-rag", "database_path": "/var/lib/menu-rag/ani_rag.sqlite3",
            "image_root": "/opt/rag-images", "api_key_file": "/run/secrets/never-opened-test-key",
            "vector_dsn_file": "/run/secrets/rag-vector-dsn", "expected_vector_database": "menu_rag_vector",
            "catalog_work_ids": [8], "model": "test-model", "pricing": {}}
        runtime_path = self.release / "rag-runtime.json"
        scheduler.save_json(runtime_path, runtime)
        self.policy.update(schema_version=2, rag={"enabled": enabled, "scope_file": "rag-scope.json",
            "scope_sha256": scheduler.digest(scope), "runtime_config": str(runtime_path),
            "runtime_sha256": scheduler.file_sha(runtime_path), "feed_valid_days": feed_valid_days})
        values = [line for line in self.env.read_text().splitlines() if not line.startswith("RAG_WORKER_IMAGE=")]
        self.env.write_text("\n".join(values) + "\nRAG_WORKER_IMAGE='sha256:" + "a" * 64 + "'\n")

    def consumer_rows(self, after):
        main = {"id": "b" * 64, "name": "/main-api", "running": True, "image": "unrelated",
            "project": "main", "service": "api", "working_dir": "/main", "entrypoint": None,
            "command": ["server", "--token=NEVER_LOG_OTHER_SERVICE_SECRET"]}
        rag = {"id": "c" * 64, "name": "/menu-daily-generation-rag-worker-1", "running": True,
            "image": "sha256:" + "a" * 64, "project": scheduler.PROJECT, "service": "rag-worker",
            "working_dir": str(self.release), "entrypoint": None,
            "command": ["celery", "-A", "menu_worker.rag_app:app", "worker", "--concurrency=1",
                        "--queues=content.rag_generate", "--hostname=rag@scheduled", "--loglevel=INFO"]}
        rows = [main]
        if self.rag_started:
            rows.append(rag)
        if self.foreign_before:
            prepared = copy.deepcopy(rag)
            prepared.update(id="d" * 64, name="/menu-rag-prepared-rag-worker-1", project="menu-rag-prepared")
            prepared["command"][-2] = "--hostname=rag@prepared"
            rows.append(prepared)
        if after and self.after_mismatch:
            if self.after_mismatch == "duplicate":
                duplicate = copy.deepcopy(rag)
                duplicate.update(id="e" * 64, name="/menu-daily-generation-rag-worker-2")
                rows.append(duplicate)
            elif self.after_mismatch == "node":
                rag["command"][-2] = "--hostname=rag@other"
            elif self.after_mismatch == "queue":
                rag["command"][-3] = "--queues=content.rag_generate,anime-generation-live"
            elif self.after_mismatch == "image":
                rag["image"] = "sha256:" + "f" * 64
        return rows

    def decorate_child(self):
        entry = self.entry()
        path = self.state / entry["checkpoint"]
        if not path.exists():
            return
        child = scheduler.read_json(path)
        parent = scheduler.read_json(self.state / entry["source"])
        plan = scheduler.read_json(self.state / entry["directory"] / "plan.json")
        child.update(rag_scope=plan["rag_scope"], rag_scope_sha256=plan["rag_scope_sha256"])
        ready = [row for row in child["records"][len(parent["records"]):] if row.get("outcome") == "ready_for_staging"]
        for row in ready[:self.rag_successes]:
            request = rag_contract.RagRequest.parse({"namespace": "dictionary_v1", "work_id": "candidate:" + row["job_id"],
                "menu_work_id": 8, "content_type": "guide", "query": "offline evidence",
                "seed_evidence": [{"document_id": "source:" + row["job_id"], "content_sha256": "a" * 64}],
                "allowed_source_ids": ["source-a"]})
            row.update(producer="ani_rag", request={"source_id": "rag_" + request.namespace,
                       "content_type": request.content_type, "menu_work_id": request.menu_work_id,
                       "rag_request": request.as_dict(), "producer": "ani_rag",
                       "idempotency_key": child["config"]["run_id"] + ":rag:" + request.identity_key,
                       "canonical_url": "urn:menu-rag:" + request.identity_key},
                       rag_input_sha256=request.input_sha256,
                       canonical_url="urn:menu-rag:" + request.identity_key, rag_delivery={"retained": row["job_id"]})
        scheduler.save_json(path, child)

    def expected_imports(self):
        child = scheduler.read_json(self.state / self.entry()["checkpoint"])
        return self.supervisor().delivery_counts(self.entry(), child)

    def runner(self, stage, args, **kwargs):
        if "rag" not in self.policy:
            return ScheduleTests.runner(self, stage, args, **kwargs)
        self.environments.append(kwargs.get("environment", {}))
        if stage.startswith("rag-consumers-"):
            self.calls.append((stage, args))
            rows = self.consumer_rows("-after-" in stage)
            if stage.endswith("-list"):
                return scheduler.CommandResult(0, "\n".join(row["id"] for row in rows))
            return scheduler.CommandResult(0, "\n".join(json.dumps(row) for row in rows))
        if stage == "rag-import":
            self.calls.append((stage, args))
            if self.crash == stage:
                self.crash = None
                raise SimulatedCrash(stage)
            if self.failed_stage == stage:
                return scheduler.CommandResult(1, "", "synthetic")
            self.rag_import_calls += 1
            _, count = self.expected_imports()
            return scheduler.CommandResult(0, json.dumps({"created": count + self.rag_report_delta, "unchanged": 0,
                "publication_enabled": False, "operator_approvals_created": 0, "llm_calls": 0}))
        try:
            output = ScheduleTests.runner(self, stage, args, **kwargs)
        except SimulatedCrash:
            if stage == "generate":
                self.decorate_child()
            raise
        if stage == "plan" and output.returncode == 0:
            value = json.loads(output.stdout)
            value["plan"].update(rag_scope=scheduler.read_json(self.state / "rag-scope.json"),
                rag_scope_sha256=self.policy["rag"]["scope_sha256"], rag_runtime_sha256=self.policy["rag"]["runtime_sha256"],
                rag_delivery_root="/runtime/state/" + self.entry()["directory"] + "/rag-inbox")
            value["plan_sha256"] = scheduler.digest(value["plan"])
            return scheduler.CommandResult(0, json.dumps(value))
        if stage == "generate":
            self.decorate_child()
        if stage == "worker-start" and output.returncode == 0:
            self.rag_started = True
        if stage == "worker-stop" and output.returncode == 0:
            self.rag_started = False
        if stage == "import" and output.returncode == 0:
            value = json.loads(output.stdout)
            value["created"] = self.expected_imports()[0]
            return scheduler.CommandResult(0, json.dumps(value))
        return output

    def test_disabled_preflight_pins_scope_runtime_inbox_and_never_starts_or_imports(self):
        self.mixed()
        with patch.dict(os.environ, {"RAG_GENERATION_ENABLED": "true", "RAG_RUNTIME_CONFIG_HOST_FILE": "/unexpected"}):
            report = self.supervisor().run(preflight=True)
        self.assertEqual([stage for stage, _ in self.calls], ["plan", "preflight"])
        self.assertFalse(report["generation_enabled"])
        self.assertEqual((report["llm_calls"], report["queue_writes"], report["service_db_writes"]), (0, 0, 0))
        for _, args in self.calls:
            self.assertIn(str(self.release / scheduler.RAG_COMPOSE[0]), args)
            self.assertNotIn(str(self.release / scheduler.RAG_COMPOSE[1]), args)
            self.assertIn("--rag-scope", args)
            self.assertIn("--expected-rag-scope-sha256", args)
            self.assertIn("--rag-runtime-config", args)
            self.assertIn("--expected-rag-runtime-sha256", args)
            self.assertIn("/runtime/state/scheduled/2026-11-26/rag-inbox", args)
        self.assertTrue(all(not any(key.startswith("RAG_") for key in env) for env in self.environments))
        directory = self.state / self.entry()["directory"]
        period, rag = (scheduler.read_json(directory / name) for name in ("import-policy.json", "rag-import-policy.json"))
        self.assertEqual(period["day_policy_sha256"], rag["day_policy_sha256"])
        self.assertEqual((period["max_items"], rag["max_items"]), (20, 20))
        before = (self.state / "scheduled/journal.json").read_bytes()
        with self.assertRaisesRegex(scheduler.ScheduleError, "rag_schedule_disabled"):
            self.supervisor().run(apply=True)
        self.assertEqual((self.state / "scheduled/journal.json").read_bytes(), before)
        self.assertEqual([stage for stage, _ in self.calls], ["plan", "preflight"])
        self.assertFalse((self.state / self.entry()["checkpoint"]).exists())

    def test_shared_twenty_passes_use_two_workers_two_importers_and_one_generate_loop(self):
        self.mixed(enabled=True)
        report = self.supervisor().run(apply=True)
        self.assertEqual((report["attempts"], report["successes"]), (20, 20))
        self.assertEqual([stage for stage, _ in self.calls if not stage.startswith("rag-consumers-")],
            ["plan", "preflight", "worker-start", "generate", "broker-empty", "worker-stop", "import", "rag-import"])
        for stage in ("worker-start", "worker-stop"):
            self.assertEqual(dict(self.calls)[stage][-2:], ["generation-worker", "rag-worker"])
        self.assertIn(scheduler.MIXED_BROKER_CHECK, dict(self.calls)["broker-empty"])
        self.assertEqual((self.entry()["period_imported"], self.entry()["rag_imported"], self.entry()["imported"]), (12, 8, 20))
        self.assertTrue(all(str(self.release / scheduler.RAG_COMPOSE[1]) in args
            for stage, args in self.calls if not stage.startswith("rag-consumers-")))
        rag_args = dict(self.calls)["rag-import"]
        self.assertIn("--reviewed-policy-sha256", rag_args)
        self.assertIn("--feed-valid-until", rag_args)
        self.assertIn("/run/secrets/rag-service-dsn", rag_args)
        self.assertNotIn("--approve", rag_args)
        self.assertEqual(rag_args[rag_args.index("--feed-valid-until") + 1], "2026-12-04T00:00:00+09:00")
        original = scheduler.read_json(self.state / "seed.json")
        child = scheduler.read_json(self.state / self.entry()["checkpoint"])
        self.assertEqual(child["records"][:len(original["records"])], original["records"])
        for row in child["records"]:
            if row.get("producer") == "ani_rag":
                request = rag_contract.RagRequest.parse(row["request"]["rag_request"])
                self.assertEqual(row["canonical_url"], "urn:menu-rag:" + request.identity_key)
                self.assertEqual(row["rag_input_sha256"], request.input_sha256)

    def test_rag_expiry_policy_requires_explicit_null_or_integer_days(self):
        self.mixed()
        for value in (None, 1, 7, 365):
            with self.subTest(accepted=value):
                self.policy["rag"]["feed_valid_days"] = value
                self.assertEqual(scheduler.validate_policy(self.policy), scheduler.digest(self.policy))
        for value in (False, True, 0, -1, 366, 1.0, "7", "null", "", [], {}):
            with self.subTest(rejected=value):
                self.policy["rag"]["feed_valid_days"] = value
                with self.assertRaisesRegex(scheduler.ScheduleError, "explicit_rag_feed_expiry_required"):
                    scheduler.validate_policy(self.policy)
        del self.policy["rag"]["feed_valid_days"]
        with self.assertRaisesRegex(scheduler.ScheduleError, "invalid_rag_schedule_policy"):
            scheduler.validate_policy(self.policy)

    def test_disabled_native_expiry_preflight_keeps_null_policy_and_no_paid_actions(self):
        self.mixed(feed_valid_days=None)
        report = self.supervisor().run(preflight=True)
        self.assertEqual([stage for stage, _ in self.calls], ["plan", "preflight"])
        self.assertEqual((report["llm_calls"], report["queue_writes"], report["service_db_writes"]), (0, 0, 0))
        directory = self.state / self.entry()["directory"]
        rag_policy = scheduler.read_json(directory / "rag-import-policy.json")
        self.assertIn("feed_valid_until", rag_policy)
        self.assertIsNone(rag_policy["feed_valid_until"])
        self.assertEqual(scheduler.digest(rag_policy), self.entry()["rag_import_policy_sha256"])
        self.assertIn("RAG_FEED_VALID_UNTIL=''\n", (directory / "runtime.env").read_text())
        with self.assertRaisesRegex(scheduler.ScheduleError, "rag_schedule_disabled"):
            self.supervisor().run(apply=True)
        self.assertFalse((self.state / self.entry()["checkpoint"]).exists())
        # Expiry changes are still policy changes, including null -> finite.
        self.policy["rag"]["feed_valid_days"] = 7
        with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_configuration_changed"):
            self.supervisor().run(preflight=True)

    def test_native_expiry_shared_caps_rollover_and_original_history_are_preserved(self):
        self.mixed(enabled=True, feed_valid_days=None)
        first = self.supervisor().run(apply=True)
        self.assertEqual((first["attempts"], first["successes"]), (20, 20))
        self.assertEqual((self.entry()["period_imported"], self.entry()["rag_imported"]), (12, 8))
        original = scheduler.read_json(self.state / self.entry()["checkpoint"])
        self.now += timedelta(days=1)
        self.attempts, self.successes, self.rag_successes = 40, 3, 3
        second = self.supervisor().run(apply=True)
        self.assertEqual((second["policy_date"], second["attempts"], second["successes"]), ("2026-11-27", 40, 3))
        self.assertEqual((self.entry()["period_imported"], self.entry()["rag_imported"]), (0, 3))
        child = scheduler.read_json(self.state / self.entry()["checkpoint"])
        self.assertEqual(child["records"][:len(original["records"])], original["records"])
        self.assertEqual(child["accounting_resume_audit"], original["accounting_resume_audit"])
        for stage, args in self.calls:
            if stage == "rag-import":
                self.assertNotIn("--feed-valid-until", args)
                self.assertNotIn("--approve", args)
                self.assertTrue(all(isinstance(value, str) for value in args))
        for day in ("2026-11-26", "2026-11-27"):
            directory = self.state / "scheduled" / day
            period = scheduler.read_json(directory / "import-policy.json")
            rag = scheduler.read_json(directory / "rag-import-policy.json")
            self.assertIsNone(rag["feed_valid_until"])
            self.assertEqual((period["max_items"], rag["max_items"]), (20, 20))
            self.assertEqual(period["day_policy_sha256"], rag["day_policy_sha256"])
        calls = len(self.calls)
        self.assertEqual(self.supervisor().run(apply=True)["status"], "already_complete")
        self.assertEqual(len(self.calls), calls)

    def test_native_expiry_prior_day_recovery_keeps_unknown_cost_and_no_ttl_override(self):
        self.mixed(enabled=True, feed_valid_days=None)
        recovery, original = self.reconciled_failure_fixture(held=True)
        self.supervisor().run(reconcile=True, **recovery)
        self.now += timedelta(days=1)
        before = len(self.calls)
        report = self.supervisor().run(resume_reconciled=True, **recovery)
        self.assertEqual((report["policy_date"], report["attempts"], report["successes"]), ("2026-11-26", 3, 2))
        following = dict(self.calls[before:])
        self.assertNotIn("worker-start", following)
        # The existing controller CLI closes an expired recovered checkpoint;
        # no Worker starts and no new record or paid attempt is admitted.
        self.assertIn("--resume-failure-recovery", following["generate"])
        for stage in ("import", "rag-import"):
            self.assertEqual(following[stage][-2:], ["--reconcile-policy-date", "2026-11-26"])
        self.assertNotIn("--feed-valid-until", following["rag-import"])
        child = scheduler.read_json(self.state / self.entry()["checkpoint"])
        self.assertEqual(child["stop_reason"], "deadline_reached")
        self.assertEqual(child["records"], original["records"])
        self.assertEqual(child["failure_recovery_audit"], original["failure_recovery_audit"])
        self.assertIsNone(child["records"][3]["estimated_cost_usd"])

    def test_combined_forty_attempts_and_all_rag_successes_keep_exact_limits(self):
        self.mixed(enabled=True)
        self.attempts, self.successes, self.rag_successes = 40, 3, 3
        report = self.supervisor().run(apply=True)
        self.assertEqual((report["attempts"], report["successes"]), (40, 3))
        self.assertEqual((self.entry()["period_imported"], self.entry()["rag_imported"]), (0, 3))
        calls = len(self.calls)
        self.assertEqual(self.supervisor().run(apply=True)["status"], "already_complete")
        self.assertEqual(len(self.calls), calls)

    def test_scope_runtime_overlay_and_existing_period_journal_cannot_change_in_place(self):
        self.supervisor().run(preflight=True)
        self.mixed(enabled=True)
        with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_configuration_changed"):
            self.supervisor().run(apply=True)
        self.assertNotIn("worker-start", dict(self.calls))
        # Input changes are rejected even before reading that old journal.
        scheduler.save_json(self.state / "rag-scope.json", {})
        with self.assertRaisesRegex(scheduler.ScheduleError, "reviewed_rag_scope_changed"):
            self.supervisor()
        self.mixed(enabled=True)
        scheduler.save_json(Path(self.policy["rag"]["runtime_config"]), {"enabled": True})
        with self.assertRaisesRegex(scheduler.ScheduleError, "reviewed_rag_runtime_changed"):
            self.supervisor()

    def test_disabled_preflight_cannot_be_promoted_by_only_toggling_runtime_and_policy(self):
        self.mixed()
        self.supervisor().run(preflight=True)
        before = (self.state / "scheduled/journal.json").read_bytes()
        calls = len(self.calls)
        self.mixed(enabled=True)
        with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_configuration_changed"):
            self.supervisor().run(apply=True)
        self.assertEqual((self.state / "scheduled/journal.json").read_bytes(), before)
        self.assertEqual(len(self.calls), calls)

    def test_completed_mixed_crash_audits_both_histories_and_never_regenerates(self):
        self.mixed(enabled=True)
        self.crash = "after-generate"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(apply=True)
        count = len(self.calls)
        self.supervisor().run(apply=True)
        following = self.calls[count:]
        self.assertEqual([stage for stage, _ in following], ["resume-audit", "broker-empty", "worker-stop", "import", "rag-import"])
        script = dict(following)["resume-audit"][-3]
        self.assertEqual(script, scheduler.MIXED_RESUME_CHECK)
        self.assertIn("rag_store=runtime.store, rag_read_result=runtime.read_result", script)
        self.assertIn("local_lock(Path(plan['checkpoint']))", script)
        compile(script, "mixed-resume", "exec")
        compile(scheduler.MIXED_RECOVERY_CHECK, "mixed-recovery", "exec")
        self.assertIn("verify_empty_rag_broker(runtime.app)", script)
        with self.assertRaisesRegex(RuntimeError, "mixed_audit_source_contract_changed"):
            scheduler.mixed_audit(script)

    def test_crash_between_importers_replays_pending_imports_without_generation(self):
        self.mixed(enabled=True)
        self.crash = "rag-import"
        with self.assertRaises(SimulatedCrash):
            self.supervisor().run(apply=True)
        self.assertEqual(self.entry()["phase"], "importing")
        count = len(self.calls)
        self.supervisor().run(apply=True)
        self.assertEqual([stage for stage, _ in self.calls[count:]], ["broker-empty", "worker-stop", "import", "rag-import"])
        self.assertEqual(self.entry()["imported"], 20)
        self.assertEqual(self.import_calls, 2)

    def test_rag_import_count_mismatch_blocks_completion_and_no_automatic_retry(self):
        self.mixed(enabled=True)
        self.rag_report_delta = 1
        with self.assertRaisesRegex(scheduler.ScheduleError, "rag_pending_import_not_drained"):
            self.supervisor().run(apply=True)
        self.assertEqual(self.entry()["phase"], "blocked")
        before = len(self.calls)
        with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_requires_reconciliation"):
            self.supervisor().run(apply=True)
        self.assertEqual(len(self.calls), before)

    def test_mixed_prior_day_recovery_preserves_unknown_cost_and_drains_both_before_rollover(self):
        self.mixed(enabled=True)
        recovery, original = self.reconciled_failure_fixture(held=True)
        self.supervisor().run(reconcile=True, **recovery)
        self.now += timedelta(days=1)
        before = len(self.calls)
        report = self.supervisor().run(resume_reconciled=True, **recovery)
        following = self.calls[before:]
        self.assertEqual((report["policy_date"], report["attempts"], report["successes"]), ("2026-11-26", 3, 2))
        self.assertNotIn("worker-start", dict(following))
        self.assertIn(scheduler.MIXED_RECOVERY_CHECK, dict(following)["recovery-audit"])
        for stage in ("import", "rag-import"):
            self.assertEqual(dict(following)[stage][-2:], ["--reconcile-policy-date", "2026-11-26"])
        self.attempts, self.successes = 20, 20
        self.supervisor().run(apply=True)
        child = scheduler.read_json(self.state / "daily-2026-11-27.json")
        self.assertEqual(child["records"][:len(original["records"])], original["records"])
        self.assertEqual(child["failure_recovery_audit"], original["failure_recovery_audit"])
        self.assertIsNone(child["records"][3]["estimated_cost_usd"])
        self.assertEqual(self.entry()["imported"], 20)

    def test_prepared_off_worker_blocks_before_start_and_never_logs_other_service_arguments(self):
        self.mixed(enabled=True)
        self.foreign_before = True
        with self.assertRaisesRegex(scheduler.ScheduleError, "rag_queue_consumer_ownership_mismatch"):
            self.supervisor().run(apply=True)
        stages = [stage for stage, _ in self.calls]
        self.assertNotIn("worker-start", stages)
        self.assertNotIn("generate", stages)
        directory = self.state / self.entry()["directory"]
        for path in directory.glob("*.log"):
            self.assertNotIn("NEVER_LOG_OTHER_SERVICE_SECRET", path.read_text())
        summary = scheduler.read_json(directory / "rag-consumers-before.log")
        self.assertEqual(summary, {"started": False, "rag_consumers": 1, "verified": False})
        self.assertEqual(self.entry()["phase"], "prepared")
        reviewed_plan = (directory / "plan.json").read_bytes()
        before = len(self.calls)
        self.foreign_before = False
        report = self.supervisor().run(apply=True)
        self.assertEqual(report["status"], "complete")
        self.assertEqual((directory / "plan.json").read_bytes(), reviewed_plan)
        self.assertNotIn("plan", [stage for stage, _ in self.calls[before:]])

    def test_duplicate_wrong_node_queue_or_image_after_start_blocks_every_generation(self):
        for mismatch in ("duplicate", "node", "queue", "image"):
            with self.subTest(mismatch=mismatch):
                # Each subcase has an independent reviewed journal.
                case = MixedScheduleTests("test_shared_twenty_passes_use_two_workers_two_importers_and_one_generate_loop")
                case.setUp()
                self.addCleanup(case.doCleanups)
                case.mixed(enabled=True)
                case.after_mismatch = mismatch
                with self.assertRaisesRegex(scheduler.ScheduleError, "rag_queue_consumer_ownership_mismatch"):
                    case.supervisor().run(apply=True)
                stages = [stage for stage, _ in case.calls]
                self.assertIn("worker-start", stages)
                self.assertNotIn("generate", stages)
                self.assertNotIn("import", stages)
                self.assertNotIn("rag-import", stages)
                self.assertEqual(case.entry()["phase"], "blocked")

    def test_expected_single_consumer_is_checked_before_and_after_start(self):
        self.mixed(enabled=True)
        self.supervisor().run(apply=True)
        stages = [stage for stage, _ in self.calls]
        self.assertLess(stages.index("rag-consumers-before-inspect"), stages.index("worker-start"))
        self.assertLess(stages.index("worker-start"), stages.index("rag-consumers-after-inspect"))
        self.assertLess(stages.index("rag-consumers-after-inspect"), stages.index("generate"))
        directory = self.state / self.entry()["directory"]
        self.assertEqual(scheduler.read_json(directory / "rag-consumers-before.log"),
                         {"started": False, "rag_consumers": 0, "verified": True})
        self.assertEqual(scheduler.read_json(directory / "rag-consumers-after.log"),
                         {"started": True, "rag_consumers": 1, "verified": True})


if __name__ == "__main__":
    unittest.main()
