import unittest

import numpy as np

from model.algo import service

# 작품 번호 101 · 102 · 103 → 시리즈 대표 자리 0 · 1 · 2. 104 는 101 과 같은 시리즈 (자리 0)
CTX = {"rep_of": {101: 1, 102: 2, 103: 3, 104: 1}, "row": {1: 0, 2: 1, 3: 2}, "cw_pos": np.array([0, 1, 2])}


def feed(*positions):
    # 글 번호 = 위치, 값 = 글의 작품 자리 (-1 = 작품 없음)
    return {"pos": np.array(positions, dtype=np.int64)}


class ArrangeTests(unittest.TestCase):
    def test_picked_work_posts_come_first_in_score_order(self):
        self.assertEqual(service.arrange([0, 1, 2, 3], feed(1, 0, 2, 0), [101], CTX), [1, 3, 0, 2])

    def test_a_picked_post_seen_before_loses_the_top_spot(self):
        seen = np.array([1, 0, 0])
        self.assertEqual(service.arrange([0, 1, 2], feed(0, 1, 0), [101], CTX, seen=seen), [2, 0, 1])

    def test_same_work_posts_wait_until_every_work_had_one(self):
        self.assertEqual(service.arrange([0, 1, 2, 3, 4], feed(0, 0, 1, 0, 2), [], CTX), [0, 2, 4, 1, 3])

    def test_same_series_counts_as_picked_and_posts_without_work_stay_separate(self):
        self.assertEqual(service.arrange([0, 1, 2, 3], feed(-1, 0, -1, 1), [104], CTX), [1, 0, 2, 3])

    def test_reason_names_the_picked_work_for_the_same_series(self):
        ctx = {**CTX, "works": {101: {"title_ko": "진격의 거인"}, 104: {"title_ko": "진격의 거인 2기"}}}
        why = service.reason((1.0, 0.5, 0.3, 0.0), 104, [101], "news", ctx)
        self.assertEqual((why["kind"], why["work"], why["text"]), ("picked", 101, "‘진격의 거인’을 골라서 추천해요"))


if __name__ == "__main__":
    unittest.main()
