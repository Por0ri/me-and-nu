"""Synthetic discovery/generation only; real policy limits are never lowered."""
from collections import Counter
import copy
import hashlib
import unittest

import test_daily_generation as legacy
from menu_worker.bounded_generation import StopRun, read_json
from menu_worker.daily_allocation import allocation_key, article_identity, seen_articles
from menu_worker.daily_routes import catalog_routes
from menu_worker.discovery_agent import DiscoveryError


class RoutedStore(legacy.FakeStore):
    def submit(self, request, job_kind):
        result = super().submit(request, job_kind)
        if result.get("created"):
            self.jobs[result["job_id"]].update({key: request[key] for key in ("source_id", "content_type", "menu_work_id")})
        return self.get(result["job_id"]) | {"created": result["created"]}


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.f = legacy.DailyTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.store.__class__ = RoutedStore
        self.routes = catalog_routes()["routes"]
        self.scanned, self.published = [], []
        self.fail_routes, self.empty_routes = set(), set()
        self.same_article_across_types = False
        self.interrupt_after = None
        self.plan = self.f.plan(routes=self.routes)
        _, self.reservations = self.f.activate(self.plan)

    def discover(self, source, kind, limit):
        self.scanned.append((source, kind))
        if (source, kind) in self.fail_routes:
            raise DiscoveryError("discovery_listing_timeout")
        if (source, kind) in self.empty_routes:
            return {"articles": [], "discovered": 0}
        route = next(r for r in self.routes if r["source_id"] == source and r["content_type"] == kind)
        articles = []
        for index in range(20):
            url = f"https://{source}.invalid/{'shared' if self.same_article_across_types else kind}/{index}"
            articles.append({**route, "source_url": url, "canonical_url": url})
        return {"articles": articles, "discovered": len(articles)}

    def controller(self):
        controller = self.f.controller(self.plan, self.reservations)
        controller.discover = self.discover
        def publish(job_id):
            # No prior job may be active when the next one is published.
            self.assertTrue(all(row["execution_status"] == "succeeded" for key, row in self.f.store.jobs.items() if key != job_id))
            self.published.append(job_id)
            return "synthetic-" + job_id
        controller.publish = publish
        def check():
            if self.interrupt_after is not None and len(self.published) >= self.interrupt_after:
                row = controller.state["records"][-1]
                if row["state"] == "complete" and row.get("delivery_envelope_path"):
                    raise RuntimeError("simulated_process_exit")
        controller.lock_check = check
        return controller

    def records(self):
        return read_json(self.f.child)["records"][4:]

    def test_twenty_passes_balanced_over_works_types_and_sequential(self):
        report = self.controller().run()
        rows = self.records()
        self.assertEqual((report["daily_attempts"], report["daily_successes"], report["daily_deliveries"]), (20, 20, 20))
        works = Counter(row["request"]["menu_work_id"] for row in rows)
        types = Counter(row["request"]["content_type"] for row in rows)
        self.assertEqual(set(works), {r["menu_work_id"] for r in self.routes})
        self.assertLessEqual(max(works.values()) - min(works.values()), 1)
        self.assertLessEqual(max(types.values()) - min(types.values()), 1)
        for row in rows:
            envelope = read_json(self.f.inbox / (row["job_id"] + ".json"))
            self.assertEqual(envelope["schema_version"], 2)
            for key in ("source_id", "content_type", "menu_work_id"):
                self.assertEqual(envelope[key], row["request"][key])
        self.assertEqual(len(self.published), 20)
        self.assertEqual(self.f.source.read_bytes(), self.f.original)

    def test_forty_attempt_cap_shared_by_all_routes_including_skips(self):
        self.f.store.outcomes = ["skipped", "held", "rejected", "skipped"] * 10
        report = self.controller().run()
        self.assertEqual(report["daily_attempts"], 40)
        self.assertEqual(report["daily_successes"], 0)
        self.assertEqual(report["stop_reason"], "daily_attempt_limit_reached")
        self.assertEqual(len(self.published), 40)
        self.assertGreater(len({row["request"]["menu_work_id"] for row in self.records()}), 1)

    def test_listing_error_and_empty_work_do_not_block_other_routes(self):
        unavailable = self.routes[0]["menu_work_id"]
        self.fail_routes = {(r["source_id"], r["content_type"]) for r in self.routes if r["menu_work_id"] == unavailable}
        self.empty_routes = {(self.routes[-1]["source_id"], self.routes[-1]["content_type"])}
        report = self.controller().run()
        self.assertEqual(report["daily_successes"], 20)
        self.assertNotIn(unavailable, {row["request"]["menu_work_id"] for row in self.records()})

    def test_empty_all_routes_waits_until_deadline_without_attempts(self):
        self.empty_routes = {(r["source_id"], r["content_type"]) for r in self.routes}
        controller = self.controller()
        controller.sleep = lambda _: setattr(self.f, "now", controller.deadline)
        with self.assertRaisesRegex(StopRun, "deadline_reached"):
            controller.run()
        self.assertEqual(controller.summary()["daily_attempts"], 0)
        self.assertEqual(self.published, [])

    def test_same_article_offered_for_multiple_types_is_attempted_only_once(self):
        self.same_article_across_types = True
        report = self.controller().run()
        identities = [article_identity(row) for row in self.records()]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(report["daily_successes"], 20)

    def test_restart_preserves_rotation_and_does_not_republish_completed_jobs(self):
        self.interrupt_after = 8
        with self.assertRaisesRegex(RuntimeError, "simulated_process_exit"):
            self.controller().run()
        prefix = copy.deepcopy(self.records())
        self.interrupt_after = None
        report = self.controller().run()
        self.assertEqual(self.records()[:8], prefix)
        self.assertEqual((report["daily_attempts"], len(self.published)), (20, 20))
        self.assertEqual(len(self.published), len(set(self.published)))
        counts = Counter(row["request"]["menu_work_id"] for row in self.records())
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_unreviewed_work_is_rejected_before_submit(self):
        controller = self.controller()
        original = controller.discover
        def wrong(*args):
            scan = original(*args)
            scan["articles"][0]["menu_work_id"] = 999999
            return scan
        controller.discover = wrong
        with self.assertRaisesRegex(StopRun, "discovery_scope_mismatch"):
            controller.run()
        self.assertEqual(self.published, [])

    def test_budget_is_global_and_preserves_old_reservation(self):
        controller = self.controller()
        controller.config["total_budget_usd"] = "1.08"  # Historical 1.03266106 + bounded remaining amount.
        with self.assertRaisesRegex(StopRun, "estimated_budget_reserve_unavailable"):
            controller.run()
        self.assertEqual(len(self.published), 1)
        self.assertEqual(str(controller.reserved_spent()), "1.00")

    def test_identity_is_source_and_url_without_type(self):
        state = {"records": [{"request": {"source_id": "a"}, "canonical_url": "https://site/article"}],
                 "config": {"source_id": "legacy"}, "seen": ["https://site/article", "excluded"]}
        self.assertEqual(seen_articles(state), {("a", "https://site/article"), ("legacy", "excluded")})

    def test_previous_day_turn_is_used_to_break_equal_daily_counts(self):
        state = {"records": [{"counts_slot": True, "request": self.routes[0]}],
                 "daily_policy": {"start_record_index": 1}}
        others = [r for r in self.routes if r["menu_work_id"] != self.routes[0]["menu_work_id"]]
        self.assertLess(allocation_key(state, others[0]), allocation_key(state, self.routes[0]))


if __name__ == "__main__":
    unittest.main()
