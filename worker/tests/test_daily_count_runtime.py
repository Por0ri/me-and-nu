"""Count-only daily execution, with synthetic costs/results and no network."""
from decimal import Decimal
import unittest
from unittest.mock import patch

import test_daily_generation as legacy
import test_daily_allocation as allocation
from menu_worker.bounded_generation import StopRun, read_json


class DailyCountRuntimeTests(unittest.TestCase):
    def setUp(self):
        # Reuse the audited history and multi-route fixtures, without budget or
        # operator deadline inputs. Production 40/20 limits are never reduced.
        original_plan = legacy.DailyTests.plan
        def count_plan(fixture, **changes):
            return original_plan(fixture, daily_budget_usd=None, deadline=None, **changes)
        self.f = allocation.AllocationTests()
        with patch.object(legacy.DailyTests, "plan", count_plan):
            self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_high_recorded_cost_does_not_stop_twenty_passes_or_drop_history(self):
        original_submit = self.f.f.store.submit
        def expensive(*args, **kwargs):
            result = original_submit(*args, **kwargs)
            if result["created"]:
                self.f.f.store.results[result["job_id"]]["usage_records"] = [{"estimated_cost_usd": "10.00"}]
            return result
        self.f.f.store.submit = expensive
        self.f.test_twenty_passes_balanced_over_works_types_and_sequential()
        controller = self.f.controller()
        report = controller.summary()
        self.assertEqual(Decimal(report["daily_spent_usd"]), Decimal("200.00"))
        self.assertEqual(report["reserved_cost_usd"], "1.00")
        self.assertIsNone(report["target_budget_usd"])
        self.assertIsNone(report["daily_budget_usd"])
        self.assertEqual(report["limit_mode"], "daily_counts")
        self.assertFalse(report["automatic_service_publication"])
        parent = read_json(self.f.f.source)
        self.assertEqual(controller.state["records"][:4], parent["records"])
        self.assertEqual(controller.state["accounting_resume_audit"], parent["accounting_resume_audit"])

    def test_forty_attempts_shared_across_routes(self):
        self.f.test_forty_attempt_cap_shared_by_all_routes_including_skips()

    def test_restart_preserves_count_rotation_and_publication_identity(self):
        self.f.test_restart_preserves_rotation_and_does_not_republish_completed_jobs()

    def test_multiple_types_cannot_regenerate_same_article(self):
        self.f.test_same_article_offered_for_multiple_types_is_attempted_only_once()

    def test_unavailable_routes_do_not_block_other_works(self):
        self.f.test_listing_error_and_empty_work_do_not_block_other_routes()

    def test_kst_calendar_boundary_stops_without_new_publication(self):
        controller = self.f.controller()
        self.assertEqual(controller.config["deadline"], "2026-11-25T00:00:00+09:00")
        self.f.f.now = controller.deadline
        with self.assertRaisesRegex(StopRun, "deadline_reached"):
            controller.run()
        self.assertEqual(self.f.published, [])

    def test_job_timeout_room_is_required_before_calendar_boundary(self):
        controller = self.f.controller()
        self.f.f.now = controller.deadline - 1499
        with self.assertRaisesRegex(StopRun, "insufficient_time_for_new_job"):
            controller.run()
        self.assertEqual(self.f.published, [])

    def test_missing_usage_evidence_still_stops_after_first_job(self):
        controller = self.f.controller()
        original_read = controller.read_result
        def missing_usage(job):
            result = original_read(job)
            result.pop("usage_records", None)
            return result
        controller.read_result = missing_usage
        with self.assertRaisesRegex(StopRun, "missing_cost_evidence"):
            controller.run()
        self.assertEqual(len(self.f.published), 1)
        self.assertEqual(len(list(self.f.f.inbox.iterdir())), 0)
        self.assertEqual(self.f.f.source.read_bytes(), self.f.f.original)

    def test_count_mode_does_not_accept_a_hidden_budget(self):
        controller = self.f.controller()
        controller.config["total_budget_usd"] = "5.00"
        with self.assertRaisesRegex(StopRun, "invalid_daily_count_limits"):
            controller.run()
        self.assertEqual(self.f.published, [])


if __name__ == "__main__":
    unittest.main()
