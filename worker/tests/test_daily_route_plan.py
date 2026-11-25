"""Offline plan/history tests; synthetic approvals never start a paid worker."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from menu_worker import anime_catalog, daily_routes
from menu_worker.bounded_generation import StopRun, read_json, write_json
from menu_worker.bounded_resume import digest
from menu_worker.daily_resume import (
    main, preflight_plan, source_checkpoint, validate_child, validate_policy_history,
)
import test_daily_generation as legacy


class DailyRoutePlanTests(unittest.TestCase):
    # Reuse the real historical-cost fixture, without inheriting its test cases.
    article = legacy.DailyTests.article
    plan = legacy.DailyTests.plan
    read_result = legacy.DailyTests.read_result
    activate = legacy.DailyTests.activate

    def setUp(self):
        legacy.DailyTests.setUp(self)
        repository = Path(__file__).resolve().parents[2]
        packaged = anime_catalog.CATALOG_ROOT
        if not (packaged / "sources.json").is_file():
            packaged = self.root / "catalog"
            packaged.mkdir()
            agent = repository / "agents" / "ani_me;nu" / "config"
            for filename in ("sources.json", "source_type_map.json"):
                shutil.copyfile(agent / filename, packaged / filename)
            shutil.copyfile(repository / "catalog" / "menu_works.csv", packaged / "menu_works.csv")
        self.enterContext(patch.object(anime_catalog, "CATALOG_ROOT", packaged))
        self.routes = daily_routes.catalog_routes()["routes"]
        self.assertGreater(len({r["menu_work_id"] for r in self.routes}), 1)

    def route_plan(self, **changes):
        return self.plan(routes=self.routes, **changes)

    def append_rows(self, state, count, *, successful=False):
        policy = state["daily_policy"]
        for index in range(count):
            route = self.routes[index % len(self.routes)]
            url = f"https://publisher.org/article/{index}"
            fields = {**route, "source_url": url, "canonical_url": url,
                      "idempotency_key": f"beta-open-20261116:new-{index}"}
            state["records"].append({"request": fields, "canonical_url": url,
                "counts_slot": True, "state": "complete", "job_id": f"job-{index}",
                "execution_status": "succeeded", "outcome": "ready_for_staging" if successful else "skipped",
                "estimated_cost_usd": "0.01", "policy_date": policy["policy_date"],
                "day_policy_sha256": digest(policy)})
            state["seen"].append(url)

    def test_v2_plan_preserves_global_limits_history_and_legacy_origin(self):
        before = sorted(self.root.iterdir())
        plan = self.route_plan()
        policy = plan["daily_policy"]
        self.assertEqual(policy["schema_version"], 2)
        self.assertEqual(policy["routes"], self.routes)
        self.assertEqual(policy["allocation"], "balanced_work_type_source_v1")
        self.assertNotIn("source_id", policy)
        self.assertNotIn("content_type", policy)
        self.assertEqual((policy["max_attempts"], policy["success_target"]), (40, 20))
        self.assertEqual(plan["config"]["max_new_jobs"], 44)
        self.assertEqual(plan["config"]["source_id"], "one_piece_official")
        self.assertEqual(plan["config"]["content_type"], "goods")
        self.assertEqual(plan["config"]["total_budget_usd"], "3.03266106")
        self.assertFalse(plan["generation_enabled"])
        self.assertEqual(sorted(self.root.iterdir()), before)
        self.assertEqual(self.source.read_bytes(), self.original)
        old = self.plan()
        self.assertEqual(old["daily_policy"]["schema_version"], 1)
        self.assertNotIn("routes", old["daily_policy"])

    def test_routes_are_canonical_and_scope_changes_change_review_digest(self):
        original = self.route_plan()
        self.assertEqual(original, self.plan(routes=list(reversed(self.routes))))
        subset = self.plan(routes=self.routes[:-1])
        self.assertNotEqual(original["day_policy_sha256"], subset["day_policy_sha256"])
        self.activate(original)
        with self.assertRaisesRegex(StopRun, "source_already_has_another_successor"):
            self.activate(subset)

    def test_invalid_or_unregistered_scope_never_creates_checkpoint(self):
        for routes in ([], [self.routes[0], self.routes[0]],
                       [{**self.routes[0], "menu_work_id": 99999999}],
                       [{**self.routes[0], "source_id": "unregistered"}],
                       [{**self.routes[0], "content_type": "review"}],
                       [{**self.routes[0], "menu_work_id": True}]):
            with self.subTest(routes=routes), self.assertRaises(StopRun):
                self.plan(routes=routes)
        self.assertFalse(self.child.exists())
        self.assertEqual(self.store.publications, [])

    def test_v2_preflight_and_resume_preserve_prior_cost_and_jobs(self):
        plan = self.route_plan()
        report = preflight_plan(plan, store=self.store, read_result=self.read_result, now=self.now)
        self.assertTrue(report["verified"])
        self.assertEqual(report["historical_jobs_verified"], 4)
        self.assertEqual(report["queue_writes"], 0)
        self.assertFalse(self.child.exists())
        state, _ = self.activate(plan)
        self.assertEqual(state["records"], read_json(self.source)["records"])
        self.assertEqual(state["seen"], read_json(self.source)["seen"])
        self.assertEqual(state["accounting_resume_audit"], read_json(self.source)["accounting_resume_audit"])
        before = self.child.read_bytes()
        self.activate(plan)
        self.assertEqual(self.child.read_bytes(), before)
        self.assertEqual(self.store.publications, [])

    def test_record_work_type_source_must_match_reviewed_tuple(self):
        plan = self.route_plan()
        state, _ = self.activate(plan)
        self.append_rows(state, 1)
        validate_child(state, read_json(self.source), plan)
        for field, value in (("source_id", "unknown"), ("menu_work_id", 99999999), ("content_type", "review")):
            changed = copy.deepcopy(state)
            changed["records"][-1]["request"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(StopRun, "daily_record_outside_scope"):
                validate_child(changed, read_json(self.source), plan)

    def test_attempt_cap_is_global_and_cannot_be_reset_by_new_route_scope(self):
        plan = self.route_plan()
        state, _ = self.activate(plan)
        self.append_rows(state, 40)
        state.update(status="complete", stop_reason="daily_attempt_limit_reached")
        write_json(self.child, state)
        child_sha = hashlib.sha256(self.child.read_bytes()).hexdigest()
        with self.assertRaisesRegex(StopRun, "daily_limit_cannot_be_reset"):
            self.plan(source=self.child, source_sha256=child_sha, checkpoint=self.root / "again.json",
                      routes=self.routes[:1])
        tomorrow = self.plan(source=self.child, source_sha256=child_sha, checkpoint=self.root / "tomorrow.json",
                             policy_date="2026-11-25", deadline="2026-11-25T23:00:00+09:00", routes=self.routes)
        self.assertEqual(tomorrow["daily_policy"]["prior_slots"], 44)
        self.assertEqual(tomorrow["daily_policy"]["prior_accounted_usd"], "1.43266106")
        self.assertEqual(tomorrow["config"]["max_new_jobs"], 84)
        self.append_rows(state, 1)
        with self.assertRaisesRegex(StopRun, "daily_attempt_limit_exceeded"):
            validate_policy_history(state)

    def test_success_cap_counts_source_and_url_across_all_routes(self):
        plan = self.route_plan()
        state, _ = self.activate(plan)
        self.append_rows(state, 21, successful=True)
        # Seven distinct URL values across three sources are still 21 identities.
        sources = list({r["source_id"]: r for r in self.routes}.values())[:3]
        for index, row in enumerate(state["records"][-21:]):
            row["request"].update(sources[index // 7])
            row["canonical_url"] = f"https://shared.org/article/{index % 7}"
        with self.assertRaisesRegex(StopRun, "daily_success_limit_exceeded"):
            validate_policy_history(state)

    def test_historical_routes_survive_catalog_changes_but_new_scope_is_rechecked(self):
        plan = self.route_plan()
        state, _ = self.activate(plan)
        self.append_rows(state, 1)
        state.update(status="stopped", stop_reason="deadline_reached")
        write_json(self.child, state)
        child_sha = hashlib.sha256(self.child.read_bytes()).hexdigest()
        with patch.object(daily_routes, "catalog_routes", return_value={"routes": [], "excluded": []}):
            self.assertEqual(source_checkpoint(self.child, child_sha), state)
            with self.assertRaisesRegex(StopRun, "daily_route_unavailable"):
                self.plan(source=self.child, source_sha256=child_sha, checkpoint=self.root / "tomorrow.json",
                          policy_date="2026-11-25", deadline="2026-11-25T23:00:00+09:00", routes=self.routes)

    def test_registered_routes_cli_only_plans_until_exact_digest_is_approved(self):
        args = ["--source-checkpoint", str(self.source), "--source-sha256", self.sha, "--checkpoint", str(self.child),
                "--policy-date", "2026-11-24", "--daily-budget-usd", "2", "--deadline", "2026-11-24T23:00:00+09:00",
                "--delivery-root", str(self.inbox), "--registered-routes"]
        with patch("menu_worker.daily_resume.time.time", return_value=self.now), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(args), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["plan"]["daily_policy"]["routes"], self.routes)
        self.assertEqual(report["route_catalog"]["routes"], self.routes)
        self.assertEqual((report["llm_calls"], report["queue_writes"], report["service_db_writes"]), (0, 0, 0))
        with patch("menu_worker.daily_resume.time.time", return_value=self.now), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main([*args, "--apply"]), 2)
        self.assertEqual(json.loads(out.getvalue())["error"], "explicit_reviewed_plan_digest_required")
        self.assertFalse(self.child.exists())
        self.assertEqual(self.store.publications, [])


if __name__ == "__main__":
    unittest.main()
