"""카드 넘김 점수 · 본 글 14일 규칙 (EXPERIMENTS 부록 A #34 · #35)"""
import unittest
from datetime import date, datetime, timezone

import numpy as np

from model.algo import anime_ranker as ar, content_v1 as v1, service
from model.scripts import daily_recommend as daily

# 글 0 ~ 3 = 작품 자리 0 ~ 3, 같은 유형 · 같은 날 (100일) 올라옴
FEED = {"pos": np.arange(4), "type": np.zeros(4, dtype=np.int64), "day": np.full(4, 100), "until": np.full(4, 400)}
SERVICE = v1.Config(logs=service.LOGS, reread_days=service.REREAD_DAYS)


def learn(events, cfg=SERVICE, day=100, state=None):
    if state is None:
        empty = np.zeros(0, dtype=np.int64)
        h = ar.History(empty, empty, np.zeros(0, dtype=np.int8), np.zeros(0, dtype=np.int8), np.zeros(0, dtype=np.int8))
        state = v1.UserState(h, [], [], {}, 0, 4, 4)
        state.work_aff = np.zeros(4, dtype=np.float32)
    v1.learn(state, events, FEED, cfg, np.arange(4), day=day)
    return state


def score(state, post):
    return round(float(state.work_ev[post]), 5)


class SwipeScoreTests(unittest.TestCase):
    def test_one_swipe_is_a_weak_plus_whatever_the_length(self):
        s = learn([("impression", 0, None), ("swipe", 0, (2, 6)), ("impression", 1, None), ("swipe", 1, (2, 7))])
        self.assertEqual(score(s, 0), 0.2)   # 넘긴 글은 보고 지나침 −0.05 없음
        self.assertEqual(score(s, 1), 0.2)   # 7장 글도 첫 넘김 비율 0.29 의 −0.2 가 아님

    def test_last_card_is_a_full_read_from_three_cards(self):
        s = learn([("swipe", 0, (6, 6)), ("swipe", 1, (3, 3)), ("swipe", 2, (2, 2))])
        self.assertEqual([score(s, 0), score(s, 1)], [0.8, 0.8])
        self.assertEqual(score(s, 2), 0.2)   # 2장 글은 끝까지여도 한 장 넘김 (11/11 결정)

    def test_card_and_body_count_once_with_the_larger_in_any_order(self):
        s = learn([("open", 0, None), ("swipe", 0, (2, 6)), ("read", 0, 0.5),
                   ("open", 1, None), ("read", 1, 0.1), ("swipe", 1, (6, 6))])
        self.assertEqual(score(s, 0), 0.3)   # 카드 0.2 · 본문 0.3 → 0.3
        self.assertEqual(score(s, 1), 0.8)   # 본문 −0.2 · 카드 끝 0.8 → 0.8

    def test_body_only_and_untouched_keep_the_old_scores(self):
        s = learn([("open", 0, None), ("read", 0, 0.9), ("open", 1, None), ("read", 1, 0.5),
                   ("open", 2, None), ("read", 2, 0.1), ("impression", 3, None)])
        self.assertEqual([score(s, i) for i in range(4)], [0.8, 0.3, -0.2, -0.05])

    def test_strength_names_match_the_taste_hints(self):
        self.assertEqual(v1.SIGNAL["swipe_some"], 0.2)   # backend taste_hint_service.SIGNAL 와 같은 이름 · 값


class RereadTests(unittest.TestCase):
    def ranked(self, state, day, cfg=SERVICE):
        return sorted(v1.rank_feed(state, FEED, day, cfg, k=4).tolist())

    def test_only_fully_read_posts_are_hidden_for_14_days(self):
        s = learn([("swipe", 0, (6, 6)), ("open", 1, None), ("read", 1, 0.85),
                   ("open", 2, None), ("read", 2, 0.5), ("swipe", 3, (2, 6))], day=100)
        self.assertEqual(self.ranked(s, 100), [2, 3])         # 덜 본 글 2 · 3 은 바로 다시 후보
        self.assertEqual(self.ranked(s, 113), [2, 3])         # 본 날 포함 14일째까지 빠짐
        self.assertEqual(self.ranked(s, 114), [0, 1, 2, 3])   # 15일째부터 다시 후보

    def test_a_two_card_deck_read_to_the_end_is_hidden_too(self):
        self.assertNotIn(0, self.ranked(learn([("swipe", 0, (2, 2))], day=100), 105))

    def test_reading_again_restarts_the_14_days(self):
        s = learn([("swipe", 0, (6, 6))], day=100)
        learn([("open", 0, None), ("read", 0, 0.9)], day=110, state=s)
        self.assertNotIn(0, self.ranked(s, 123))
        self.assertIn(0, self.ranked(s, 124))

    def test_the_simulator_setting_still_hides_every_opened_post(self):
        cfg = v1.Config(logs=service.LOGS)   # reread_days 없음 = 지난 실험과 같은 규칙
        s = learn([("open", 0, None), ("read", 0, 0.5)], cfg=cfg, day=None)
        self.assertNotIn(0, self.ranked(s, 300, cfg))


class LoadVisitsTests(unittest.TestCase):
    def test_card_and_body_stay_apart_and_the_visit_knows_its_korean_day(self):
        at = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)   # 한국 11/11 00:00
        events = [(1, "card_swipe", 501, {"session_id": "a", "card_index": 2, "card_count": 6}, at),
                  (1, "card_swipe", 501, {"session_id": "a", "card_index": 4, "card_count": 6}, at),
                  (1, "open", 501, {"session_id": "a", "scroll_pct": 40.0}, at),
                  (1, "open", 501, {"session_id": "a", "scroll_pct": 90.0}, at)]

        class Cursor:
            rows = iter([events, [], []])

            def execute(self, *_):
                pass

            def fetchall(self):
                return next(self.rows)

        visit = daily.load_visits(Cursor(), [1], {501: 0})[1][0]
        self.assertEqual(visit.day, date(2026, 11, 11).toordinal() - 719163)
        self.assertEqual([e for e in visit if e[0] in ("swipe", "read")], [("swipe", 0, (4, 6)), ("read", 0, 0.9)])


class RestTests(unittest.TestCase):
    """지나친 글은 하루 쉬고 다시 자리 후보, 오늘 나온 건 안 셈 (11/19, EXPERIMENTS 부록 A #44)"""

    def test_learn_keeps_the_last_two_days_a_post_was_shown(self):
        s = learn([("impression", 0, None)], day=100)
        learn([("impression", 0, None), ("impression", 1, None)], day=102, state=s)
        learn([("impression", 0, None)], day=102, state=s)        # 같은 날 또 나와도 그대로
        self.assertEqual([s.seen_last[0], s.seen_prev[0]], [102, 100])
        self.assertEqual([s.seen_last[1], s.seen_prev[1]], [102, -np.inf])

    def test_a_post_shown_yesterday_rests_today_and_returns_the_next_day(self):
        s = learn([("impression", 0, None)], day=100)
        self.assertEqual(service.slot_ready(s, 101).tolist(), [False, True, True, True])
        self.assertEqual(service.slot_ready(s, 102).tolist(), [True, True, True, True])

    def test_a_post_shown_only_today_keeps_its_slot(self):      # 하루 중간 다시 계산 (취향 저장)
        s = learn([("impression", 0, None)], day=100)
        self.assertTrue(service.slot_ready(s, 100)[0])
        learn([("impression", 0, None)], day=101, state=s)
        self.assertFalse(service.slot_ready(s, 101)[0])         # 어제도 나왔으면 오늘 나온 것과 상관없이 쉼

    def test_posts_never_shown_rank_above_returning_ones(self):
        s = learn([("impression", 0, None)], day=100)
        order = v1.rank_feed(s, FEED, 102, SERVICE, k=4).tolist()
        self.assertTrue(service.slot_ready(s, 102)[0])          # 돌아왔지만
        self.assertEqual(order[-1], 0)                          # 본 날 감점 · 보고 지나침 때문에 안 나온 글 뒤


class SeenTimeTests(unittest.TestCase):
    """빨리 넘긴 카드 (3초 미만) 는 본 글 감점 · 자리 쉬기 · 보고 지나침이 없음 (11/25, EXPERIMENTS 부록 A #48)"""

    def test_a_quickly_passed_card_leaves_no_trace(self):
        at = datetime(2026, 11, 24, 3, tzinfo=timezone.utc)    # 한국 11/24 12:00
        events = [(1, "impression", 501, {"session_id": "a", "visible_ms": 1500}, at),    # 글 0: 빨리 넘김
                  (1, "impression", 502, {"session_id": "a", "visible_ms": 4000}, at),    # 글 1: 봄
                  (1, "impression", 503, {"session_id": "a", "visible_ms": 8000, "focus_ms": 2000}, at)]   # 글 2: 80% 는 2초

        class Cursor:
            rows = iter([events, [], []])

            def execute(self, *_):
                pass

            def fetchall(self):
                return next(self.rows)

        visits = daily.load_visits(Cursor(), [1], {501: 0, 502: 1, 503: 2})[1]
        today, s = visits[0].day, None
        for visit in visits:
            s = learn(visit, day=visit.day, state=s)
        self.assertEqual(s.seen.tolist(), [0, 1, 0, 0])                                   # 본 글 감점은 본 글만
        self.assertEqual([score(s, i) for i in range(3)], [0.0, -0.05, 0.0])             # 보고 지나침도 본 글만
        self.assertEqual(service.slot_ready(s, today + 1).tolist(), [True, False, True, True])   # 다음 날 쉬는 것도 본 글만


if __name__ == "__main__":
    unittest.main()
