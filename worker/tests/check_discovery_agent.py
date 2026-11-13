"""Exercise the installed Agent's real RSS, official-link and work parsers without any network."""
import unittest
from time import monotonic
from unittest.mock import patch

import httpx

from menu_worker.discovery_agent import DiscoveryError, _collect, _DeadlineExceeded, discover_articles


OUTLET = "https://prtimes.jp/main/html/rd/p/000000001.000000002.html"
ARTICLE = "https://one-piece.com/news/82812/index.html"
RSS = f"""<rss version="2.0"><channel><title>Press releases</title><item>
<title>アニメ ONE PIECE 放送開始</title><link>{OUTLET}</link>
<description>TV anime announcement</description></item></channel></rss>"""


class InstalledDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.original = ARTICLE + "?ref=homepage"
        self.canonical = ""
        self.extra_link = ""
        self.failed = False
        self.rss = RSS
        self.fetches = []

        def read_page(url, source, timeout):
            self.fetches.append(url)
            if url == OUTLET:
                return (f'<article><a href="{self.original}">公式発表</a>{self.extra_link}</article>', url)
            if url == self.original:
                if self.failed:
                    raise httpx.ReadTimeout("fixture timeout")
                return (f"<html><head>{self.canonical}</head></html>", url)
            self.fail("Unexpected page fetch")

        self.enterContext(patch("ani_menu_agent.sources.page_collector._get_html", side_effect=read_page))
        self.feed = self.enterContext(patch("ani_menu_agent.discovery._get_with_backoff"))
        self.feed.side_effect = lambda *_: httpx.Response(200, text=self.rss)

    def collect(self):
        return _collect("prtimes_rss", "news", 5)

    def test_rss_to_official_article_and_registered_work(self):
        self.assertEqual(self.collect(), {
            "discovered": 1, "unresolved": 0, "fetch_failed": 0,
            "articles": [{"source_id": "one_piece_official", "source_url": self.original,
                          "content_type": "news", "menu_work_id": 8, "canonical_url": ARTICLE}],
        })
        self.assertEqual(self.fetches, [OUTLET, self.original])

    def test_publisher_relative_canonical_and_fallback_preserve_identifiers(self):
        self.original = ARTICLE + "?episode_id=11&ref=home"
        self.canonical = '<link rel="canonical" href="./index.html?episode_id=10">'
        self.assertEqual(self.collect()["articles"][0]["canonical_url"], ARTICLE + "?episode_id=10")
        for invalid in ("https://other.org/news/11", "https://[broken]"):
            with self.subTest(invalid=invalid):
                self.canonical = f'<link rel="canonical" href="{invalid}">'
                self.assertEqual(self.collect()["articles"][0]["canonical_url"], ARTICLE + "?episode_id=11")

    def test_conflicting_official_links_and_unregistered_hosts_do_not_create_jobs(self):
        self.extra_link = '<a href="https://one-piece.com/news/82813/index.html">公式発表</a>'
        for original in [self.original, "https://unregistered.org/news/123"]:
            with self.subTest(original=original):
                self.original = original
                if "unregistered" in original:
                    self.extra_link = ""
                self.fetches.clear()
                self.assertEqual(self.collect(), {"discovered": 1, "unresolved": 1, "fetch_failed": 0,
                                                  "articles": []})
                self.assertEqual(self.fetches, [OUTLET])

    def test_failed_fetch_is_visible_and_can_be_retried(self):
        self.failed = True
        self.assertEqual(self.collect(), {"discovered": 1, "unresolved": 0, "fetch_failed": 1,
                                          "articles": []})
        self.failed = False
        self.assertEqual(len(self.collect()["articles"]), 1)

    def test_partial_listing_and_candidate_deadlines_keep_resolved_articles(self):
        from ani_menu_agent.discovery import DiscoveryItem
        item = DiscoveryItem(id="id", title="Anime", source_id="prtimes_rss", url=OUTLET, content_types=["news"])
        self.enterContext(patch("ani_menu_agent.discovery.collect_candidates",
                                return_value=([item, item, item], ["incomplete listing"])))
        now = monotonic()
        self.enterContext(patch("menu_worker.discovery_agent.time.monotonic",
                                side_effect=(now, now, now + 10, now + 211)))
        self.enterContext(patch("menu_worker.discovery_agent._original_article", side_effect=[
            {"source_id": "one_piece_official"}, _DeadlineExceeded,
        ]))
        self.assertEqual(self.collect(), {
            "articles": [{"source_id": "one_piece_official"}], "discovered": 3, "unresolved": 0,
            "fetch_failed": 2, "listing_partial": True,
        })

    def test_disabled_listing_is_never_contacted(self):
        with self.assertRaisesRegex(DiscoveryError, "discovery_source_unavailable"):
            _collect("one_piece_official", "news", 5)
        self.feed.assert_not_called()
        self.assertEqual(self.fetches, [])

    def test_keyless_subprocess_returns_a_bounded_failure_for_a_disabled_listing(self):
        with self.assertRaisesRegex(DiscoveryError, "discovery_source_unavailable"):
            discover_articles("one_piece_official", "news", 1)

    def test_failed_listing_is_distinct_from_no_new_items(self):
        self.feed.side_effect = httpx.ReadTimeout("fixture timeout")
        with self.assertRaisesRegex(DiscoveryError, "discovery_listing_failed"):
            self.collect()
        self.rss = "<rss version='2.0'><channel><title>Empty</title></channel></rss>"
        self.feed.side_effect = lambda *_: httpx.Response(200, text=self.rss)
        self.assertEqual(self.collect(), {"articles": [], "discovered": 0, "unresolved": 0, "fetch_failed": 0})


if __name__ == "__main__":
    unittest.main()
