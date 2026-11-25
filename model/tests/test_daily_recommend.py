import inspect
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from model.algo import anime_ranker as ar, service
from model.algo.preferences import LEGACY_ALIASES, apply_chip, load_chip_features, unmapped_summary
from model.algo.preflight import check_inputs
from model.scripts import daily_recommend as daily
from model.tests.fixtures import build_synthetic_world
from model.tests.storage_adapter import self_test


ROOT = Path(__file__).resolve().parents[2]


class SeqCursor:
    """load_users 가 부르는 순서대로 결과를 돌려주는 가짜 커서. fetchone = Q3 순위 표 (tap_consumption_type) 가 있나"""

    def __init__(self, rows, table=True):
        self.rows, self.table = iter(rows), table

    def execute(self, *_):
        pass

    def fetchall(self):
        return next(self.rows)

    def fetchone(self):
        return (self.table,)


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
        rows = [[(1,)], [(1, "1")], [(1, "theme", "music", "음악", 41), (1, "theme", "family", "가족", 42)],
                [], [(1, "ost", None, None)]]
        user = daily.load_users(SeqCursor(rows), 1, self.features)[1]
        self.assertEqual(user["works"], [1])
        self.assertEqual(user["genres"], ["Music"])
        self.assertEqual(user["unmapped_chips"][0]["tag_id"], 42)
        self.assertEqual(user["answers"], ["ost"])

    def test_load_users_reads_q3_ranks_only_when_the_first_matches(self):
        # tap 1: 새 표 순서 그대로 / tap 2: 운영 옛 코드가 1순위만 바꿈 → 그 하나 / tap 3: 새 표 없음 (예전 회원)
        # tap 4: 예전 (v1) 답 q1 · q2 (q2 는 따로) / tap 5: rank 1 줄이 없음 → 그 하나
        rows = [[(1,), (2,), (3,), (4,), (5,)], [], [],
                [(1, 1, "ost"), (1, 2, "news"), (1, 3, "world"), (2, 1, "goods"), (2, 2, "ost"), (5, 2, "ost")],
                [(1, "ost", None, None), (2, "world", None, None), (3, "news", None, None), (4, None, "ost", "events"),
                 (5, "ost", None, None)]]
        users = daily.load_users(SeqCursor(rows), 1, self.features)
        self.assertEqual([users[t]["answers"] for t in (1, 2, 3, 4, 5)],
                         [["ost", "news", "world"], ["world"], ["news"], ["ost"], ["ost"]])
        self.assertEqual([users[t]["q2"] for t in (1, 4)], [[], ["events"]])

    def test_load_users_without_the_rank_table_keeps_one_answer(self):
        rows = [[(1,)], [], [], [(1, "ost", None, None)]]           # 마이그레이션 전 DB: 순위 표를 안 읽음
        with patch("builtins.print"):
            user = daily.load_users(SeqCursor(rows, table=False), 1, self.features)[1]
        self.assertEqual(user["answers"], ["ost"])

    def test_load_visits_counts_one_impression_per_post_per_day(self):
        at = datetime(2026, 10, 31, 3, tzinfo=timezone.utc)                # 12:00 in Seoul
        seen = {"visible_ms": 4000}                                        # 3초 넘게 본 노출 (SeenTimeTests)
        events = [(1, "impression", 501, {"session_id": "a", **seen}, at),
                  (1, "impression", 501, {"session_id": "b", **seen}, at + timedelta(hours=2)),   # same day, refreshed
                  (1, "impression", 501, {"session_id": "c", **seen}, at + timedelta(days=1))]
        visits = daily.load_visits(EventCursor(events), [1], {501: 0})[1]
        self.assertEqual(sum(kind == "impression" for visit in visits for kind, *_ in visit), 2)


class EventCursor:
    """load_visits 가 읽는 순서 (활동 기록 → 반응 → 저장) 대로 돌려주는 가짜 커서. 반응 · 저장은 없음"""

    def __init__(self, events):
        self.rows = iter([events, [], []])

    def execute(self, *_):
        pass

    def fetchall(self):
        return next(self.rows)


class SeenTimeTests(unittest.TestCase):
    """'봤다' = 카드가 80% 이상 보인 시간 3초 이상인 노출만 (11/25, EXPERIMENTS 부록 A #48)"""
    AT = datetime(2026, 11, 24, 3, tzinfo=timezone.utc)                  # 한국 11/24 12:00

    def impressions(self, visits):
        return sorted(c for visit in visits for kind, c, _ in visit if kind == "impression")

    def test_only_impressions_seen_for_three_seconds_reach_the_model(self):
        values = [{"visible_ms": 2999},                     # 0 반 이상 보인 시간 3초 미만 → 버림
                  {"visible_ms": 3000},                     # 1 focus_ms 가 없는 기록은 visible_ms 로 → 남김
                  {"visible_ms": 9000, "focus_ms": 2999},   # 2 focus_ms 가 있으면 그것만 봄 → 버림
                  {"visible_ms": 5000, "focus_ms": 3000},   # 3 → 남김
                  {},                                       # 4 시간이 없음 → 버림
                  {"visible_ms": 4000, "focus_ms": None}]   # 5 focus_ms 빈값 = 없음 → visible_ms 로 남김
        events = [(1, "impression", 501 + n, {"session_id": "a", **v}, self.AT) for n, v in enumerate(values)]
        visits = daily.load_visits(EventCursor(events), [1], {501 + n: n for n in range(len(values))})[1]
        self.assertEqual(self.impressions(visits), [1, 3, 5])
        self.assertEqual([daily.seen_enough(v) for v in values], [False, True, False, True, False, True])

    def test_opened_or_swiped_posts_count_as_seen_whatever_the_time(self):
        # 열었거나 카드를 넘긴 글은 확실히 본 글 (11/25 사용자 결정): 짧은 노출 (1.2초) 이어도 본 글로 남고,
        # 같은 접속에 열기 · 넘기기가 있어서 보고 지나침은 아님
        events = [(1, "impression", 501, {"session_id": "a", "visible_ms": 1200}, self.AT),
                  (1, "card_swipe", 501, {"session_id": "a", "card_index": 2, "card_count": 6}, self.AT),
                  (1, "open", 501, {"session_id": "a", "scroll_pct": 50.0}, self.AT)]
        visit = daily.load_visits(EventCursor(events), [1], {501: 0})[1][0]
        self.assertEqual(sorted(visit), [("impression", 0, None), ("open", 0, None), ("read", 0, 0.5), ("swipe", 0, (2, 6))])

    def test_posts_opened_without_an_impression_count_as_seen(self):
        # 검색 · 저장 · 알림에서 연 글처럼 홈 노출이 없어도 본 글, 하루 한 번만
        events = [(1, "open", 501, {"session_id": "a", "scroll_pct": 20.0}, self.AT),
                  (1, "card_swipe", 502, {"session_id": "a", "card_index": 1, "card_count": 4}, self.AT),
                  (1, "open", 501, {"session_id": "b", "scroll_pct": 40.0}, self.AT + timedelta(hours=1)),
                  (1, "impression", 501, {"session_id": "b", "visible_ms": 5000}, self.AT + timedelta(hours=1))]
        visits = daily.load_visits(EventCursor(events), [1], {501: 0, 502: 1})[1]
        self.assertEqual(self.impressions(visits), [0, 1])

    def test_a_short_impression_does_not_hide_a_long_one_the_same_day(self):
        # 하루 한 번은 거른 뒤에 셈: 앞 접속 (a) 의 짧은 노출 대신 뒤 접속 (b) 의 긴 노출이 남음.
        # 짧은 노출만 있던 접속 (c) 은 접속 자체가 안 남음
        events = [(1, "impression", 501, {"session_id": "a", "visible_ms": 1200}, self.AT),
                  (1, "card_swipe", 502, {"session_id": "a", "card_index": 2, "card_count": 6}, self.AT),
                  (1, "impression", 501, {"session_id": "b", "visible_ms": 4000}, self.AT + timedelta(hours=1)),
                  (1, "impression", 503, {"session_id": "c", "visible_ms": 1500}, self.AT + timedelta(hours=2))]
        visits = daily.load_visits(EventCursor(events), [1], {501: 0, 502: 1, 503: 2})[1]
        # 502 는 카드를 넘겨서 본 글 (11/25), 501 의 짧은 노출은 버리고 뒤 접속의 긴 노출이 남음
        self.assertEqual([list(v) for v in visits], [[("impression", 1, None), ("swipe", 1, (2, 6))], [("impression", 0, None)]])


class DayLengthTests(unittest.TestCase):
    """하루 300개 (11/25, EXPERIMENTS 부록 A #47). 합성 작품 320편 위의 가짜 글"""

    @classmethod
    def setUpClass(cls):
        cls.world = build_synthetic_world()

    def rank(self, n):
        ctx, cfg, ids, _ = self.world
        today = date(2026, 11, 25)
        types = ("character", "news", "character", "guide", "music", "goods")   # Q3 묶음이 섞이게
        posts = [{"id": k, "type": types[k % len(types)], "title": f"synthetic-{k}", "published": today - timedelta(days=k % 20),
                  "until": today + timedelta(days=30), "menu_work_id": k % 200 + 1} for k in range(1, n + 1)]
        user = {"works": [1, 2], "genres": ["Action"], "themes": ["Isekai"], "answers": ["relationships", "news"]}
        return daily.recommend(user, [], ctx, service.make_feed(posts, ctx), posts, today.toordinal() - 719163,
                               ar.to_seconds(datetime(2026, 11, 25)), cfg, ids)

    def test_a_day_holds_300_posts(self):
        self.assertEqual(daily.TOP_N, 300)
        self.assertEqual(inspect.signature(service.compose).parameters["top_n"].default, daily.TOP_N)
        out = self.rank(400)
        self.assertEqual(len(out), 300)
        self.assertEqual(len({i for i, *_ in out}), 300)                  # 같은 글 두 번 없음

    def test_fewer_posts_just_end_early(self):                            # 백엔드는 '정확히 300개' 를 가정하면 안 됨
        self.assertEqual(len(self.rank(120)), 120)

    def test_the_first_100_stay_the_same_without_theme_collections(self):
        # 300 으로 늘려도 앞 100개는 그대로 (달라지는 건 소재 모음뿐: test_beta_rules 의 test_300_lets_a_later_theme_collection_move_up)
        long = self.rank(400)
        with patch.object(daily, "TOP_N", 100):
            short = self.rank(400)
        self.assertEqual(len(short), 100)
        self.assertEqual(long[:100], short)


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
            for relative in ["catalog/menu_works.csv", "catalog/menu_work_entries.csv", "model/catalog/chips.csv",
                             "model/data/anilist/media.jsonl", "model/data/anilist/media_recent.jsonl",
                             "model/data/anilist/detail.jsonl", "model/data/anilist/links.jsonl",
                             "model/mal/anime.csv", "model/mal/interactions.npz"]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"presence only; not valid data")
            report = check_inputs(root / "model", root / "model" / "mal")
        self.assertTrue(report["ready"])
        self.assertIn("Presence/size", report["limits"])
        self.assertTrue(report["warnings"])

    def test_server_context_mode_needs_only_chips_and_the_context_file(self):
        # 추천 계산 이미지엔 model/ 만 들어가서 공용 작품 목록(레포 catalog/)이 없다
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "model" / "catalog").mkdir(parents=True)
            (root / "model" / "catalog" / "chips.csv").write_bytes(b"presence only; not valid data")
            (root / "context.pkl.gz").write_bytes(b"presence only; not valid data")
            report = check_inputs(root / "model", context=root / "context.pkl.gz")
        self.assertEqual(report["missing_files"], [])
        self.assertTrue(report["ready"])

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
