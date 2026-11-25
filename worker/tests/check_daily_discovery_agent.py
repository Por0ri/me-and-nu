"""Installed Agent parser bridge for daily work-scoped listings, with all HTTP replaced."""
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from menu_worker.discovery_agent import DiscoveryError, _collect, discover_daily_articles


class DailyInstalledDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("Network is forbidden")))
        self.enterContext(patch("menu_worker.discovery_agent._until", side_effect=lambda _: nullcontext()))

    def test_three_registered_works_use_their_own_type_and_existing_work_id(self):
        cases = (
            ("jujutsu_kaisen_official", "news", 3, "https://jujutsukaisen.jp/news/20261125_01.php",
             "TV anime new season announced"),
            ("frieren_official", "goods", 43, "https://frieren-anime.jp/news/20261125-01/",
             "葬送のフリーレン グッズ予約受付"),
            ("haikyu_official", "event", 24, "https://haikyu.jp/news/20261125-01/",
             "ハイキュー!! 展示イベント開催"),
        )
        for source_id, content_type, work_id, url, headline in cases:
            html = f'<article><a href="{url}"><h3>{headline}</h3></a></article>'
            with self.subTest(source=source_id), patch(
                "ani_menu_agent.discovery._get_html_with_client",
                side_effect=lambda _client, listing, *_: (html, listing),
            ) as listing, patch(
                "ani_menu_agent.sources.page_collector._get_html", return_value=("<html></html>", url),
            ) as article:
                result = _collect(source_id, content_type, 5, work_scoped=True)
            self.assertEqual(listing.call_count, 1)
            self.assertEqual(article.call_count, 1)
            self.assertEqual(result["discovered"], 1)
            self.assertEqual(result["unresolved"], 0)
            self.assertEqual(result["articles"][0]["source_id"], source_id)
            self.assertEqual(result["articles"][0]["content_type"], content_type)
            self.assertEqual(result["articles"][0]["menu_work_id"], work_id)

    def test_headline_types_are_not_forced_into_requested_lane(self):
        source_id = "frieren_official"
        url = "https://frieren-anime.jp/news/20261125-02/"
        html = f'<article><a href="{url}">葬送のフリーレン グッズ予約受付</a></article>'
        with patch("ani_menu_agent.discovery._get_html_with_client",
                   side_effect=lambda _client, listing, *_: (html, listing)), patch(
                       "ani_menu_agent.sources.page_collector._get_html") as article:
            result = _collect(source_id, "news", 5, work_scoped=True)
        self.assertEqual(result["articles"], [])
        article.assert_not_called()

    def test_default_discovery_stays_disabled_and_invalid_daily_routes_never_fetch(self):
        with patch("ani_menu_agent.discovery._get_html_with_client") as fetch:
            with self.assertRaisesRegex(DiscoveryError, "discovery_source_unavailable"):
                _collect("frieren_official", "goods", 5)
            for source_id, content_type in (("kimetsu_official", "news"), ("prtimes_rss", "news"),
                                             ("one_piece_official", "news"), ("mushoku_official_characters", "goods")):
                with self.subTest(source=source_id), self.assertRaisesRegex(DiscoveryError, "discovery_source_unavailable"):
                    _collect(source_id, content_type, 5, work_scoped=True)
            fetch.assert_not_called()
        with patch("menu_worker.discovery_agent.subprocess.run") as process:
            with self.assertRaisesRegex(DiscoveryError, "discovery_source_unavailable"):
                discover_daily_articles("prtimes_rss", "news", 5)
            process.assert_not_called()

    def test_agent_catalog_work_drift_is_rejected_before_fetch(self):
        with patch("ani_menu_agent.work_catalog.registered_service_work_ids", return_value={999}), patch(
            "ani_menu_agent.discovery._get_html_with_client"
        ) as fetch:
            with self.assertRaisesRegex(DiscoveryError, "discovery_catalog_unavailable"):
                _collect("frieren_official", "goods", 5, work_scoped=True)
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
