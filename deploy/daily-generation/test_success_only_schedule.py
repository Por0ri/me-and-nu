"""Success-target schedules keep old evidence and never enforce a hidden cap."""
import copy
import json
from pathlib import Path
import unittest

import test_scheduled_run as fixtures

scheduler = fixtures.scheduler


class SuccessOnlyScheduleTests(unittest.TestCase):
    setUp = fixtures.MixedScheduleTests.setUp
    supervisor = fixtures.ScheduleTests.supervisor
    entry = fixtures.ScheduleTests.entry
    mixed = fixtures.MixedScheduleTests.mixed
    consumer_rows = fixtures.MixedScheduleTests.consumer_rows
    decorate_child = fixtures.MixedScheduleTests.decorate_child
    expected_imports = fixtures.MixedScheduleTests.expected_imports

    def runner(self, stage, args, **kwargs):
        output = fixtures.MixedScheduleTests.runner(self, stage, args, **kwargs)
        if stage == "plan" and self.policy["max_attempts"] is None and output.returncode == 0:
            value = json.loads(output.stdout)
            value["plan"]["daily_policy"].update(schema_version=4, max_attempts=None)
            value["plan"]["config"]["max_new_jobs"] = None
            value["plan"]["day_policy_sha256"] = scheduler.digest(value["plan"]["daily_policy"])
            value["plan_sha256"] = scheduler.digest(value["plan"])
            return scheduler.CommandResult(0, json.dumps(value))
        return output

    def uncapped(self):
        self.mixed(enabled=True)
        self.policy.update(schema_version=3, max_attempts=None)

    def test_sixty_attempts_twenty_passes_import_pending_only(self):
        self.uncapped()
        self.attempts, self.successes = 60, 20
        result = self.supervisor().run(apply=True)
        self.assertEqual((result["attempts"], result["successes"]), (60, 20))
        self.assertFalse(result["publication_enabled"])
        entry = self.entry()
        self.assertEqual((entry["phase"], entry["imported"]), ("complete", 20))
        self.assertEqual((self.import_calls, self.rag_import_calls), (1, 1))
        for stage, args in self.calls:
            if stage in {"plan", "preflight", "generate"}:
                self.assertIn("--success-target-only", args)
        self.assertIsNone(self.supervisor().status()["max_attempts"])

    def test_unlimited_day_cannot_finish_at_the_former_attempt_cap(self):
        self.uncapped()
        self.attempts, self.successes = 40, 6
        with self.assertRaisesRegex(scheduler.ScheduleError, "day_completion_count_mismatch"):
            self.supervisor().run(apply=True)
        self.assertEqual((self.import_calls, self.rag_import_calls), (0, 0))

    def test_legacy_capped_policy_remains_capped_and_null_is_explicit(self):
        self.mixed(enabled=True)
        for wrong in (None, False, 0, 40000):
            with self.subTest(wrong=wrong):
                policy = {**self.policy, "max_attempts": wrong}
                with self.assertRaises(scheduler.ScheduleError):
                    scheduler.validate_policy(policy)
        self.uncapped()
        missing = dict(self.policy)
        del missing["max_attempts"]
        with self.assertRaises(scheduler.ScheduleError):
            scheduler.validate_policy(missing)
        self.attempts, self.successes = 60, 20
        self.policy.update(schema_version=2, max_attempts=40)
        with self.assertRaisesRegex(scheduler.ScheduleError, "day_limits_exceeded"):
            self.supervisor().run(apply=True)

    def test_same_day_revision_retains_plan_and_requires_pinned_evidence(self):
        self.mixed(enabled=True)
        self.supervisor().run(preflight=True)
        entry = self.entry()
        plan = scheduler.read_json(self.state / entry["directory"] / "plan.json")
        policy = plan["daily_policy"]
        policy["start_record_index"] = len(self.seed["records"])
        plan["day_policy_sha256"] = scheduler.digest(policy)
        entry["plan_sha256"] = scheduler.digest(plan)
        child = copy.deepcopy(self.seed)
        child.update(config=plan["config"], daily_policy=policy, daily_policies=[policy],
            daily_plan_sha256=entry["plan_sha256"], status="stopped", stop_reason="failure_reconciled_paused")
        for number in range(9):
            url = "https://example.invalid/retained/" + str(number)
            child["records"].append({"state": "complete", "counts_slot": True,
                "request": self.routes[0], "canonical_url": url, "policy_date": entry["date"],
                "day_policy_sha256": scheduler.digest(policy), "execution_status": "succeeded",
                "outcome": "ready_for_staging" if number < 6 else "held"})
            child["seen"].append(url)
        before = copy.deepcopy(child)
        revision = scheduler.daily_limits.build_limit_revision(child,
            reason="Explicitly approved attempt-cap removal", approved_at=self.now.isoformat())
        child["daily_limit_revisions"] = [revision]
        entry["daily_limit_revision_sha256"] = scheduler.digest(revision)
        scheduler.save_json(self.state / entry["checkpoint"], child)
        self.policy.update(schema_version=3, max_attempts=None)
        sup = self.supervisor()
        sup.validate_plan(plan, entry)
        self.assertIsNone(sup.attempt_limit(child, entry))
        self.assertEqual(child["records"], before["records"])
        self.assertEqual(child["config"], before["config"])
        self.assertEqual((revision["prior_daily_attempts"], revision["prior_daily_successes"]), (9, 6))
        scheduler.save_json(self.state / entry["directory"] / "plan.json", plan)
        self.assertNotIn("--success-target-only", sup.daily_args(entry))
        with self.assertRaisesRegex(scheduler.ScheduleError, "reviewed_daily_limit_revision_required"):
            sup.attempt_limit(child, {**entry, "daily_limit_revision_sha256": "0" * 64})
        child["records"][-1]["outcome"] = "ready_for_staging"
        with self.assertRaisesRegex(scheduler.ScheduleError, "daily_limit_revision_changed"):
            sup.attempt_limit(child, entry)


if __name__ == "__main__":
    unittest.main()
