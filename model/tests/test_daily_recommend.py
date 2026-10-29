import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model.algo.preferences import LEGACY_ALIASES, apply_chip, load_chip_features, unmapped_summary
from model.algo.preflight import check_inputs
from model.scripts import daily_recommend as daily
from model.tests.storage_adapter import self_test


ROOT = Path(__file__).resolve().parents[2]


class PreferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.features = load_chip_features(ROOT / "model" / "catalog" / "chips.csv")

    def test_starter_mapping_is_explicit_and_lossless(self):
        tags = json.loads((ROOT / "backend/app/seeds/onboarding_catalog_starter.json").read_text(encoding="utf-8"))["tags"]
        user = {"genres": [], "themes": [], "unknown_chips": 0}
        for i, tag in enumerate(tags, 1):
            apply_chip(user, tag["kind"], tag["code"], tag["displayName"], i, self.features)
        self.assertEqual(len(user["preference_tags"]), 38)
        self.assertEqual(user["unknown_chips"], 8)
        self.assertEqual({c["code"] for c in user["unmapped_chips"]}, {
            "friendship", "family", "heroes", "mind_games", "science", "civilization", "letters", "bounty_hunters"})
        self.assertEqual(len([t for t in tags if (t["kind"], t["code"]) in LEGACY_ALIASES]), 11)
        self.assertIn("Body Swapping", user["themes"])
        self.assertIn("Sci-Fi", user["genres"])
        self.assertIn("Music", user["genres"])
        self.assertNotIn("Music", user["themes"])
        self.assertEqual(user["preference_tags"][0]["tag_id"], 1)

    def test_canonical_and_legacy_do_not_double_feature_weight(self):
        user = {"genres": [], "themes": []}
        apply_chip(user, "genre", "music", "음악", 1, self.features)
        apply_chip(user, "theme", "music", "음악", 2, self.features)
        self.assertEqual(user["genres"], ["Music"])
        self.assertEqual(len(user["preference_tags"]), 2)

    def test_unknown_preserves_identity_without_guessed_feature(self):
        user = {"genres": [], "themes": []}
        apply_chip(user, "theme", "family", "가족", 99, self.features)
        self.assertEqual(user["themes"], [])
        self.assertEqual(user["unmapped_chips"][0], {
            "tag_id": 99, "kind": "theme", "code": "family", "label": "가족", "reason": "no_exact_model_feature"})
        summary = unmapped_summary({123: user})
        self.assertEqual(summary[0]["selections"], 1)
        self.assertNotIn("tag_id", summary[0])
        self.assertNotIn("tap_id", summary[0])

    def test_missing_alias_target_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chips.csv"
            path.write_text("kind,code,anilist_name\ngenre,action,Action\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "alias target"):
                load_chip_features(path)

    def test_load_users_retains_unmapped_tag_and_cross_kind_alias(self):
        class Cursor:
            def __init__(self):
                self.rows = iter([[(1,)], [(1, "1")],
                                  [(1, "theme", "music", "음악", 41), (1, "theme", "family", "가족", 42)],
                                  [(1, "ost", None, None)]])

            def execute(self, *_):
                pass

            def fetchall(self):
                return next(self.rows)

        user = daily.load_users(Cursor(), 1, self.features)[1]
        self.assertEqual(user["works"], [1])
        self.assertEqual(user["genres"], ["Music"])
        self.assertEqual(user["unmapped_chips"][0]["tag_id"], 42)
        self.assertEqual(user["answers"], ["ost"])


class PreflightTests(unittest.TestCase):
    def test_missing_inputs_are_aggregated_without_connecting(self):
        with tempfile.TemporaryDirectory() as tmp, patch("psycopg.connect", side_effect=AssertionError("DB forbidden")):
            report = check_inputs(Path(tmp), Path(tmp) / "mal")
        self.assertFalse(report["ready"])
        self.assertEqual(len(report["missing_files"]), 9)
        self.assertTrue(any("media_recent.jsonl" in p for p in report["missing_files"]))
        self.assertTrue(any("interactions.csv" in p and "interactions.npz" in p for p in report["missing_files"]))

    def test_cache_only_input_is_accepted_as_presence_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in ["catalog/chips.csv", "catalog/menu_works.csv", "catalog/menu_work_entries.csv",
                             "data/anilist/media.jsonl", "data/anilist/media_recent.jsonl", "data/anilist/detail.jsonl",
                             "data/anilist/links.jsonl", "mal/anime.csv", "mal/interactions.npz"]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"presence only; not valid data")
            report = check_inputs(root, root / "mal")
        self.assertTrue(report["ready"])
        self.assertIn("Presence/size", report["limits"])
        self.assertTrue(report["warnings"])

    def test_preflight_cli_does_not_read_database_configuration(self):
        with patch("sys.argv", ["daily_recommend", "--preflight"]), patch.object(daily, "database_url", side_effect=AssertionError("env forbidden")), patch.object(daily, "check_inputs", return_value={"ready": False}), patch("builtins.print"):
            with self.assertRaises(SystemExit) as raised:
                daily.main()
        self.assertEqual(raised.exception.code, 2)

    def test_missing_dataset_stops_before_database_configuration(self):
        with patch("sys.argv", ["daily_recommend", "--expected-database", "isolated_test"]), patch.object(daily, "database_url", side_effect=AssertionError("env forbidden")), patch.object(daily, "require_inputs", side_effect=SystemExit("missing inputs")):
            with self.assertRaisesRegex(SystemExit, "missing inputs"):
                daily.main()

    def test_invalid_schedule_rejected_before_data_or_database_access(self):
        for extra in [["--every", "-1"], ["--every", "0"], ["--daily-at", "99:99"], ["--today", "2026-02-30"]]:
            with self.subTest(extra=extra), patch("sys.argv", ["daily_recommend", "--expected-database", "isolated_test", *extra]), patch.object(daily, "database_url", side_effect=AssertionError("env forbidden")), patch.object(daily, "require_inputs", side_effect=AssertionError("data forbidden")), patch("sys.stderr"):
                with self.assertRaises(SystemExit) as raised:
                    daily.main()
            self.assertEqual(raised.exception.code, 2)


class AdapterTests(unittest.TestCase):
    def test_synthetic_ranking_and_atomic_adapter_contract(self):
        with patch("psycopg.connect", side_effect=AssertionError("DB forbidden")):
            report = self_test()
        self.assertEqual(report["item_count"], 4)
        self.assertTrue(report["replace_rollback_verified"])

    def test_self_test_cli_cannot_apply_to_database(self):
        with patch("sys.argv", ["daily_recommend", "--self-test", "--apply"]), patch.object(daily, "database_url", side_effect=AssertionError("env forbidden")), patch("sys.stderr"):
            with self.assertRaises(SystemExit) as raised:
                daily.main()
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
