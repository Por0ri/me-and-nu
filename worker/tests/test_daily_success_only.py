"""Unlimited attempts still preserve daily successes, sealed history and stops."""
from copy import deepcopy
from datetime import datetime
import hashlib
import unittest
from unittest.mock import patch

import test_daily_allocation as allocation
import test_daily_generation as legacy
from menu_worker.bounded_generation import StopRun, audit_existing_jobs, read_json, write_json
from menu_worker.bounded_resume import digest
from menu_worker.daily_limits import (DailyLimitError, build_limit_revision, effective_daily_attempt_limit,
    effective_total_attempt_limit, validate_limit_revisions)
from menu_worker.daily_resume import activate_plan, build_plan, source_checkpoint, validate_child, validate_policy_history


class SuccessOnlyTests(unittest.TestCase):
    def fixture(self, *, unlimited=True):
        original = legacy.DailyTests.plan
        def plan(fixture, **changes):
            return original(fixture, daily_budget_usd=None, deadline=None,
                            success_target_only=unlimited, **changes)
        fixture = allocation.AllocationTests()
        with patch.object(legacy.DailyTests, "plan", plan):
            fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_schema4_explicitly_has_no_cap_and_same_legacy_plan_still_has_forty(self):
        for unlimited in (False, True):
            with self.subTest(unlimited=unlimited):
                f = self.fixture(unlimited=unlimited)
                self.assertEqual(f.plan["daily_policy"]["schema_version"], 4 if unlimited else 3)
                self.assertEqual(f.plan["daily_policy"]["max_attempts"], None if unlimited else 40)
                self.assertEqual(f.plan["config"]["max_new_jobs"], None if unlimited else 44)
                state, _ = activate_plan(f.plan, store=f.f.store, read_result=f.f.read_result, now=f.f.now)
                self.assertEqual(effective_daily_attempt_limit(state), None if unlimited else 40)
                validate_child(state, read_json(f.f.source), f.plan)
                self.assertEqual(f.f.source.read_bytes(), f.f.original)

    def test_forty_unpassed_then_twenty_passes_stay_sequential_and_accounted(self):
        f = self.fixture()
        f.f.store.outcomes = ["held", "rejected", "skipped", "held"] * 10 + ["ready_for_staging"] * 20
        report = f.controller().run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (60, 20))
        self.assertEqual(report["stop_reason"], "daily_success_target_reached")
        self.assertIsNone(report["daily_attempt_limit"])
        self.assertIsNone(report["max_new_jobs"])
        self.assertEqual(len(f.published), len(set(f.published)))
        self.assertEqual(len(f.published), 60)
        self.assertEqual(len(list(f.f.inbox.iterdir())), 20)
        state = read_json(f.f.child)
        validate_policy_history(state)
        audit_existing_jobs(f.f.store, state)
        self.assertEqual(state["records"][:4], read_json(f.f.source)["records"])
        self.assertEqual(f.f.source.read_bytes(), f.f.original)

    def test_same_day_revision_preserves_nine_attempts_six_passes_then_adds_fourteen(self):
        f = self.fixture(unlimited=False)
        f.f.store.outcomes = ["held"] * 3 + ["ready_for_staging"] * 6
        f.interrupt_after = 9
        with self.assertRaisesRegex(RuntimeError, "simulated_process_exit"):
            f.controller().run()
        state = read_json(f.f.child)
        state.update(status="stopped", stop_reason="operator_paused")
        original = deepcopy(state)
        plan_before = deepcopy(f.plan)
        revision = build_limit_revision(state, reason="Approved success-only daily generation",
            approved_at="2026-11-24T11:00:00+09:00")
        self.assertEqual((revision["prior_daily_attempts"], revision["prior_daily_successes"]), (9, 6))
        state["daily_limit_revisions"] = [revision]
        validate_child(state, read_json(f.f.source), f.plan)
        self.assertIsNone(effective_daily_attempt_limit(state))
        # This fixture's manual pause has no failure receipt; explicit resume
        # changes only status. Production uses its separately checked A08 path.
        state.update(status="running", stop_reason=None)
        write_json(f.f.child, state)
        f.interrupt_after = None
        report = f.controller().run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (23, 20))
        current = read_json(f.f.child)
        self.assertEqual(current["records"][:13], original["records"])
        self.assertEqual(current["seen"][:len(original["seen"])], original["seen"])
        for key in ("config", "daily_policy", "daily_policies", "daily_plan_sha256", "accounting_resume_audit"):
            self.assertEqual(current[key], original[key])
        self.assertEqual(f.plan, plan_before)
        self.assertEqual(len(f.published), 23)
        self.assertEqual({row["daily_limit_revision_sha256"] for row in current["records"][13:]}, {digest(revision)})
        validate_child(current, read_json(f.f.source), f.plan)
        self.assertEqual(f.controller().run()["daily_successes"], 20)
        self.assertEqual(len(f.published), 23)

    def test_revised_day_exceeds_old_cap_and_next_day_keeps_revision_and_source(self):
        f = self.fixture(unlimited=False)
        state = read_json(f.f.child)
        state.update(status="stopped", stop_reason="operator_paused")
        revision = build_limit_revision(state, reason="Approved unlimited attempt count",
            approved_at="2026-11-24T11:00:00+09:00")
        state["daily_limit_revisions"] = [revision]
        state.update(status="running", stop_reason=None)
        write_json(f.f.child, state)
        f.f.store.outcomes = ["skipped"] * 40 + ["ready_for_staging"] * 20
        report = f.controller().run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (60, 20))
        current = read_json(f.f.child)
        self.assertEqual(current["config"]["max_new_jobs"], 44)
        self.assertIsNone(effective_total_attempt_limit(current))
        audit_existing_jobs(f.f.store, current)
        original = f.f.child.read_bytes()
        sha = hashlib.sha256(original).hexdigest()
        self.assertEqual(source_checkpoint(f.f.child, sha), current)
        plan = build_plan(source=f.f.child, source_sha256=sha, checkpoint=f.f.root / "next.json",
            policy_date="2026-11-25", delivery_root=f.f.inbox, routes=f.routes,
            now=f.f.now + 86400, success_target_only=True)
        next_state, _ = activate_plan(plan, store=f.f.store, read_result=f.f.read_result, now=f.f.now + 86400)
        self.assertEqual(next_state["records"], current["records"])
        self.assertEqual(next_state["daily_limit_revisions"], [revision])
        self.assertEqual(next_state["daily_policy"]["prior_slots"], 64)
        self.assertIsNone(next_state["config"]["max_new_jobs"])
        self.assertEqual(f.f.child.read_bytes(), original)

    def test_revision_rejects_ambiguous_history_reset_or_unbound_new_rows(self):
        f = self.fixture(unlimited=False)
        state = read_json(f.f.child)
        state.update(status="stopped", stop_reason="operator_paused")
        revision = build_limit_revision(state, reason="Approved unlimited attempt count",
            approved_at="2026-11-24T11:00:00+09:00")
        state["daily_limit_revisions"] = [revision]
        cases = []
        changed = deepcopy(state); changed["records"][0]["estimated_cost_usd"] = "0"; cases.append(changed)
        changed = deepcopy(state); changed["seen"][0] = "replaced"; cases.append(changed)
        changed = deepcopy(state); changed["daily_limit_revisions"][0]["prior_daily_attempts"] = 9; cases.append(changed)
        changed = deepcopy(state); changed["daily_limit_revisions"][0]["max_attempts"] = 999999; cases.append(changed)
        changed = deepcopy(state); changed["daily_limit_revisions"] *= 2; cases.append(changed)
        changed = deepcopy(state); changed["records"].append({"policy_date": state["daily_policy"]["policy_date"]}); cases.append(changed)
        for index, changed in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(DailyLimitError):
                validate_limit_revisions(changed)

    def test_unknown_usage_still_stops_after_first_and_midnight_never_publishes(self):
        f = self.fixture()
        controller = f.controller()
        original = controller.read_result
        def unreadable(job):
            result = original(job); result.pop("usage_records", None); return result
        controller.read_result = unreadable
        with self.assertRaisesRegex(StopRun, "missing_cost_evidence"):
            controller.run()
        self.assertEqual(len(f.published), 1)
        f = self.fixture()
        controller = f.controller()
        f.f.now = controller.deadline - 1499
        with self.assertRaisesRegex(StopRun, "insufficient_time_for_new_job"):
            controller.run()
        self.assertEqual(f.published, [])
        f = self.fixture(); controller = f.controller(); f.f.now = controller.deadline
        with self.assertRaisesRegex(StopRun, "deadline_reached"):
            controller.run()
        self.assertEqual(f.published, [])


if __name__ == "__main__":
    unittest.main()
