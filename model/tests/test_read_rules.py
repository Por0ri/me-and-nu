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
        state = v1.UserState(h, [], [], [], 0, 4, 4)
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


if __name__ == "__main__":
    unittest.main()
