"""Count-only daily planning stays offline and preserves the historical ledger."""
import contextlib
import copy
from datetime import datetime
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from menu_worker.bounded_generation import StopRun, read_json, write_json
from menu_worker.bounded_resume import claim_path, digest
from menu_worker.daily_generation import KST
from menu_worker.daily_resume import (
    activate_plan, build_plan, main, preflight_plan, source_checkpoint,
    validate_child, validate_policy_history,
)
import test_daily_generation as legacy
import test_daily_route_plan as routed


class DailyCountPlanTests(unittest.TestCase):
    article = legacy.DailyTests.article
    read_result = legacy.DailyTests.read_result
    activate = legacy.DailyTests.activate
    setUp = routed.DailyRoutePlanTests.setUp
    append_rows = routed.DailyRoutePlanTests.append_rows

    def plan(self, **changes):
        args = dict(source=self.source, source_sha256=self.sha, checkpoint=self.child,
                    policy_date="2026-11-24", delivery_root=self.inbox, now=self.now)
        args.update(changes)
        return build_plan(**args)

    def test_new_plan_needs_no_budget_or_operator_deadline_and_keeps_ledger(self):
        files = sorted(self.root.iterdir())
        plan = self.plan()
        policy = plan["daily_policy"]
        self.assertEqual(policy["schema_version"], 3)
        self.assertEqual(policy["limit_mode"], "daily_counts")
        self.assertNotIn("daily_budget_usd", policy)
        self.assertNotIn("source_id", policy)
        self.assertEqual(policy["routes"], self.routes)
        self.assertEqual(policy["deadline"], "2026-11-25T00:00:00+09:00")
        self.assertEqual(plan["config"]["deadline"], policy["deadline"])
        self.assertIsNone(plan["config"]["total_budget_usd"])
        self.assertEqual(plan["config"]["limit_mode"], "daily_counts")
        self.assertEqual((policy["max_attempts"], policy["success_target"]), (40, 20))
        self.assertEqual(plan["config"]["max_new_jobs"], 44)
        self.assertEqual(policy["prior_accounted_usd"], "1.03266106")
        self.assertEqual(plan["prior_reserved_usd"], "1.00")
        self.assertFalse(plan["generation_enabled"])
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(sorted(self.root.iterdir()), files)

    def test_preflight_and_repeated_activation_preserve_history_and_cost(self):
        plan = self.plan()
        before = sorted(self.root.iterdir())
        report = preflight_plan(plan, store=self.store, read_result=self.read_result, now=self.now)
        self.assertEqual(report["historical_jobs_verified"], 4)
        self.assertEqual((report["llm_calls"], report["queue_writes"], report["service_db_writes"],
                          report["checkpoint_writes"]), (0, 0, 0, 0))
        self.assertEqual(sorted(self.root.iterdir()), before)
        state, reservations = self.activate(plan)
        parent = read_json(self.source)
        for key in ("records", "seen", "accounting_resume_audit"):
            self.assertEqual(state[key], parent[key])
        self.assertEqual(len(reservations), 1)
        checkpoint, claim = self.child.read_bytes(), claim_path(self.source).read_bytes()
        resumed, _ = self.activate(plan)
        self.assertEqual(resumed, state)
        self.assertEqual(self.child.read_bytes(), checkpoint)
        self.assertEqual(claim_path(self.source).read_bytes(), claim)
        self.assertEqual(self.store.publications, [])

    def test_legacy_v1_and_v2_plans_still_replay_with_original_limits(self):
        args = dict(daily_budget_usd="2.00", deadline="2026-11-24T23:00:00+09:00")
        for routes, version in ((None, 1), (self.routes, 2)):
            with self.subTest(version=version):
                plan = self.plan(routes=routes, **args)
                self.assertEqual(plan["daily_policy"]["schema_version"], version)
                self.assertEqual(plan["daily_policy"]["daily_budget_usd"], "2.00")
                self.assertEqual(plan["config"]["total_budget_usd"], "3.03266106")
                self.assertNotIn("limit_mode", plan["config"])
                self.assertNotIn("limit_mode", plan["daily_policy"])
                self.assertTrue(preflight_plan(plan, store=self.store, read_result=self.read_result,
                                               now=self.now)["verified"])

    def test_partial_legacy_inputs_never_silently_change_limit_mode(self):
        for args in ({"daily_budget_usd": "2"}, {"deadline": "2026-11-24T23:00:00+09:00"}):
            with self.subTest(args=args), self.assertRaisesRegex(StopRun, "legacy_budget_and_deadline_required_together"):
                self.plan(**args)
        self.assertFalse(self.child.exists())

    def test_daily_boundary_is_derived_and_start_leaves_time_for_one_job(self):
        too_late = datetime(2026, 11, 24, 23, 40, tzinfo=KST).timestamp()
        with self.assertRaisesRegex(StopRun, "insufficient_time_for_new_day"):
            self.plan(now=too_late)
        plan = self.plan()
        before_day = datetime(2026, 11, 23, 23, 59, tzinfo=KST).timestamp()
        with self.assertRaisesRegex(StopRun, "daily_window_not_open"):
            activate_plan(plan, store=self.store, read_result=self.read_result, now=before_day)
        self.assertFalse(self.child.exists())

    def test_completed_count_day_cannot_reset_and_next_day_preserves_cost_and_attempts(self):
        plan = self.plan()
        state, _ = self.activate(plan)
        self.append_rows(state, 40)
        state.update(status="complete", stop_reason="daily_attempt_limit_reached")
        write_json(self.child, state)
        sha = hashlib.sha256(self.child.read_bytes()).hexdigest()
        args = dict(source=self.child, source_sha256=sha, checkpoint=self.root / "next.json")
        with self.assertRaisesRegex(StopRun, "daily_limit_cannot_be_reset"):
            self.plan(**args)
        tomorrow = self.plan(**args, policy_date="2026-11-25")
        self.assertEqual(tomorrow["daily_policy"]["prior_slots"], 44)
        self.assertEqual(tomorrow["daily_policy"]["prior_accounted_usd"], "1.43266106")
        self.assertEqual(tomorrow["config"]["max_new_jobs"], 84)
        self.assertEqual(tomorrow["daily_policy"]["prior_records_sha256"], digest(state["records"]))
        self.assertEqual(tomorrow["config"]["deadline"], "2026-11-26T00:00:00+09:00")
        self.assertEqual(source_checkpoint(self.child, sha), state)

    def test_legacy_daily_history_and_hashes_survive_count_only_successor(self):
        old = self.plan(daily_budget_usd="2.00", deadline="2026-11-24T23:00:00+09:00", routes=self.routes)
        state, _ = self.activate(old)
        self.append_rows(state, 1)
        state.update(status="stopped", stop_reason="deadline_reached")
        write_json(self.child, state)
        sha = hashlib.sha256(self.child.read_bytes()).hexdigest()
        plan = self.plan(source=self.child, source_sha256=sha, checkpoint=self.root / "next.json",
                         policy_date="2026-11-25")
        self.assertEqual(plan["daily_policy"]["schema_version"], 3)
        self.assertEqual(plan["daily_policy"]["prior_records_sha256"], digest(state["records"]))
        self.assertEqual(source_checkpoint(self.child, sha)["daily_policies"], [old["daily_policy"]])
        self.assertEqual(plan["prior_reserved_usd"], "1.00")

    def test_changed_count_mode_or_boundary_and_record_scope_are_rejected(self):
        plan = self.plan()
        state, _ = self.activate(plan)
        for field, value in (("limit_mode", "unlimited"), ("deadline", "2026-11-24T23:00:00+09:00"),
                             ("daily_budget_usd", "2")):
            altered = copy.deepcopy(state)
            altered["daily_policy"][field] = value
            altered["daily_policies"][-1][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(StopRun, "invalid_daily_count_policy"):
                validate_policy_history(altered)
        for field, value in (("limit_mode", "legacy"), ("total_budget_usd", "2"),
                             ("deadline", "2026-11-27T00:00:00+09:00")):
            altered = copy.deepcopy(state)
            altered["config"][field] = value
            with self.subTest(config=field), self.assertRaisesRegex(StopRun, "daily_count_config_changed"):
                validate_policy_history(altered)
        self.append_rows(state, 1)
        state["records"][-1]["request"]["menu_work_id"] = 999999
        with self.assertRaisesRegex(StopRun, "daily_record_outside_scope"):
            validate_child(state, read_json(self.source), plan)

    def test_count_policy_retains_global_attempt_and_success_caps(self):
        state, _ = self.activate(self.plan())
        for count, success, reason in ((41, False, "daily_attempt_limit_exceeded"),
                                       (21, True, "daily_success_limit_exceeded")):
            changed = copy.deepcopy(state)
            self.append_rows(changed, count, successful=success)
            with self.subTest(reason=reason), self.assertRaisesRegex(StopRun, reason):
                validate_policy_history(changed)

    def test_cli_without_budget_or_deadline_only_proposes_count_policy(self):
        args = ["--source-checkpoint", str(self.source), "--source-sha256", self.sha,
                "--checkpoint", str(self.child), "--policy-date", "2026-11-24",
                "--delivery-root", str(self.inbox)]
        with patch("menu_worker.daily_resume.time.time", return_value=self.now), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(args), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["plan"]["daily_policy"]["schema_version"], 3)
        self.assertEqual(report["route_catalog"]["routes"], self.routes)
        self.assertEqual((report["llm_calls"], report["queue_writes"], report["service_db_writes"]), (0, 0, 0))
        with patch("menu_worker.daily_resume.time.time", return_value=self.now), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main([*args, "--apply"]), 2)
        self.assertEqual(json.loads(out.getvalue())["error"], "explicit_reviewed_plan_digest_required")
        self.assertFalse(self.child.exists())
        self.assertEqual(self.store.publications, [])


if __name__ == "__main__":
    unittest.main()
