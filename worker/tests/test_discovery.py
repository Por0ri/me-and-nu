"""Periodic discovery admits bounded, verified candidates and leaves retries to later ticks."""
from contextlib import nullcontext
import os
import unittest
from unittest.mock import Mock, patch

from menu_worker.discovery import run_discovery_cycle
from menu_worker.discovery_settings import candidate_limit, load_routes
from menu_worker.job_contract import JobInputError
from menu_worker.job_store import JobConflict


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"ANIME_DISCOVERY_ROUTES": "prtimes_rss:news",
                                                  "ANIME_DISCOVERY_LIMIT": "3"}))
        self.store = Mock()
        self.store.discovery_slot.return_value = nullcontext(True)
        self.discover = Mock()
        self.article = {"source_id": "one_piece_official", "content_type": "news", "menu_work_id": 8,
                        "source_url": "https://one-piece.com/news/82812/index.html"}

    def cycle(self, **args):
        return run_discovery_cycle("prtimes_rss", args.get("content_type", "news"),
                                   store=self.store, discover=self.discover)

    def report(self, articles, *, unresolved=0, fetch_failed=0, **extra):
        self.discover.return_value = {"discovered": len(articles) + unresolved + fetch_failed, "articles": articles,
                                      "unresolved": unresolved, "fetch_failed": fetch_failed, **extra}

    def test_disabled_or_busy_route_does_not_crawl(self):
        with self.assertRaises(ValueError):
            self.cycle(content_type="goods")
        self.store.discovery_slot.assert_not_called()
        self.store.discovery_slot.return_value = nullcontext(False)
        self.assertEqual(self.cycle()["status"], "already_running")
        self.discover.assert_not_called()
        self.store.submit.assert_not_called()

    def test_candidate_bound_and_route_settings(self):
        self.assertEqual(load_routes("  "), ())
        self.assertEqual(load_routes("prtimes_rss:news, inside_figures:goods"),
                         (("prtimes_rss", "news"), ("inside_figures", "goods")))
        for raw in ("prtimes_rss", "a:invalid", "a:news,a:news", "a:goods,"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_routes(raw)
        for raw in ("-1", "0", "21", "1.5"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                candidate_limit(raw)
        self.report([self.article] * 4)
        with self.assertRaises(RuntimeError):
            self.cycle()
        self.store.submit.assert_not_called()

    def test_good_articles_survive_partial_scan_and_bad_candidates(self):
        self.report([{**self.article, "content_type": "goods"}, self.article], fetch_failed=1)
        self.store.submit.return_value = {"created": True}
        result = self.cycle()
        self.assertEqual((result["status"], result["rejected"], result["submitted"], result["fetch_failed"]),
                         ("partial", 1, 1, 1))
        self.discover.assert_called_once_with("prtimes_rss", "news", 3)
        fields = self.store.submit.call_args.args[0]
        self.assertEqual(self.store.submit.call_args.kwargs, {"job_kind": "anime_generate"})
        self.assertTrue(fields["idempotency_key"].startswith("discovery:"))
        self.assertEqual(fields["source_id"], "one_piece_official")
        self.report([], listing_partial=True)
        self.assertEqual(self.cycle()["status"], "partial")

    def test_manual_owner_and_scheduled_replay_are_both_counted_as_duplicates(self):
        self.report([self.article])
        self.store.submit.side_effect = [JobConflict("candidate_exists", "manual-owner"), {"created": False}]
        self.assertEqual(self.cycle()["duplicates"], 1)
        self.assertEqual(self.cycle()["duplicates"], 1)
        self.assertEqual(*self.store.submit.call_args_list)

    def test_system_failures_are_visible_to_worker(self):
        self.report([self.article])
        self.store.submit.side_effect = JobInputError("catalog not mounted", "catalog_unavailable")
        with self.assertRaisesRegex(JobInputError, "catalog not mounted"):
            self.cycle()
        self.discover.side_effect = RuntimeError("listing unavailable")
        with self.assertRaisesRegex(RuntimeError, "listing unavailable"):
            self.cycle()
