"""Offline enumeration never turns registered sources into unrestricted crawl targets."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from menu_worker import anime_catalog as catalog
from menu_worker.daily_routes import catalog_routes, validate_routes


class DailyRouteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(catalog, "CATALOG_ROOT", self.root))
        self.sources = [self.source("official_a", "Alpha"), self.source("official_b", "Beta")]
        self.types = {source["id"]: ["news", "event", "goods", "review"] for source in self.sources}
        self.works = [(11, "Alpha"), (12, "Beta")]
        self.write()

    def source(self, source_id, title, **overrides):
        return {"id": source_id, "url": f"https://{source_id.replace('_', '-')}.org/news/",
                "status": "active", "role": "primary", "collection": "html", "category": "official_news",
                "work_title_ko": title, "discovery_enabled": False, **overrides}

    def write(self):
        (self.root / "sources.json").write_text(json.dumps({"sources": self.sources}), encoding="utf-8")
        (self.root / "source_type_map.json").write_text(json.dumps({
            "types": {kind: kind for kind in ("news", "event", "goods", "review")}, "sources": self.types,
        }), encoding="utf-8")
        with (self.root / "menu_works.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("menu_work_id", "title_ko", "title_native", "title_romaji", "title_english"))
            writer.writerows((work_id, title, "", "", "") for work_id, title in self.works)

    def test_exact_registered_works_and_types_are_enumerated_without_changing_flags(self):
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        report = catalog_routes()
        self.assertEqual(len(report["routes"]), 6)
        self.assertEqual(report["excluded"], [])
        self.assertEqual({row["menu_work_id"] for row in report["routes"]}, {11, 12})
        self.assertEqual({row["content_type"] for row in report["routes"]}, {"news", "event", "goods"})
        self.assertEqual(validate_routes(list(reversed(report["routes"]))), report["routes"])
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})

    def test_unsupported_and_unresolved_sources_are_reported_not_guessed(self):
        variants = [
            ("blocked", "Alpha", {"status": "terms_review"}, "source_inactive"),
            ("rss", "Alpha", {"collection": "rss"}, "unsupported_daily_source"),
            ("editorial", "Alpha", {"role": "editorial"}, "unsupported_daily_source"),
            ("gallery", "Alpha", {"category": "character"}, "unsupported_daily_source"),
            ("general", None, {}, "source_work_unresolved"),
            ("new_work", "Unregistered", {}, "source_work_unresolved"),
        ]
        for source_id, title, overrides, _ in variants:
            self.sources.append(self.source(source_id, title, **overrides))
            self.types[source_id] = ["news"]
        self.write()
        report = catalog_routes()
        self.assertEqual({row["source_id"] for row in report["routes"]}, {"official_a", "official_b"})
        self.assertEqual({row["source_id"]: row["reason"] for row in report["excluded"]},
                         {source_id: reason for source_id, _, _, reason in variants})

    def test_ambiguous_titles_and_narrow_targeted_adapter_are_not_expanded(self):
        self.sources[0]["targeted_discovery_types"] = ["goods"]
        self.works.append((13, "Beta"))
        self.write()
        self.assertEqual(catalog_routes(), {
            "routes": [{"source_id": "official_a", "content_type": "goods", "menu_work_id": 11}],
            "excluded": [{"source_id": "official_b", "reason": "source_work_unresolved"}],
        })

    def test_existing_policy_is_readable_but_new_plan_rejects_removed_route(self):
        routes = catalog_routes()["routes"]
        self.sources[0]["status"] = "blocked"
        self.write()
        self.assertEqual(validate_routes(routes, current_catalog=False), routes)
        with self.assertRaisesRegex(ValueError, "daily_route_unavailable"):
            validate_routes(routes)

    def test_structural_validation_rejects_duplicates_wrong_work_and_extra_fields(self):
        route = catalog_routes()["routes"][0]
        malformed = ([], [route, route], [{**route, "menu_work_id": True}],
                     [{**route, "content_type": "review"}], [{**route, "other": "ignored"}],
                     [route, {**route, "menu_work_id": 100}])
        for routes in malformed:
            with self.subTest(routes=routes), self.assertRaisesRegex(ValueError, "invalid_daily_routes"):
                validate_routes(routes, current_catalog=False)
        with self.assertRaisesRegex(ValueError, "daily_route_unavailable"):
            validate_routes([{**route, "menu_work_id": 999}])

    def test_invalid_catalog_is_fatal_instead_of_silently_losing_routes(self):
        self.sources.append(dict(self.sources[0]))
        self.write()
        with self.assertRaisesRegex(catalog.AnimeInputError, "invalid_catalog"):
            catalog_routes()

    def test_main_registry_snapshot_has_existing_works_and_three_supported_types(self):
        repo = Path(__file__).resolve().parents[2]
        inputs = {"sources.json": repo / "agents/ani_me;nu/config/sources.json",
                  "source_type_map.json": repo / "agents/ani_me;nu/config/source_type_map.json",
                  "menu_works.csv": repo / "catalog/menu_works.csv"}
        if not all(path.is_file() for path in inputs.values()):
            inputs = {name: Path("/app/anime-catalog") / name for name in inputs}
        if not all(path.is_file() for path in inputs.values()):
            self.skipTest("Repository source catalog is not mounted")
        for name, path in inputs.items():
            (self.root / name).write_bytes(path.read_bytes())
        routes = catalog_routes()["routes"]
        self.assertEqual(len(routes), 16)
        self.assertEqual({row["menu_work_id"] for row in routes}, {3, 5, 8, 24, 26, 43, 377})
        self.assertEqual([row["content_type"] for row in routes if row["source_id"] == "one_piece_official"], ["goods"])


if __name__ == "__main__":
    unittest.main()
