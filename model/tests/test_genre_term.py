import unittest

import numpy as np

from model.algo import anime_ranker as ar, content_v1 as v1, service

# 글 0 · 1 = 작품 자리 0 · 1, 같은 유형 · 같은 날
FEED = {"pos": np.array([0, 1]), "type": np.array([0, 0]), "day": np.array([10, 10]), "until": np.array([20, 20])}


def state(work_aff, genre_sim):
    empty = np.zeros(0, dtype=np.int64)
    h = ar.History(empty, empty, np.zeros(0, dtype=np.int8), np.zeros(0, dtype=np.int8), np.zeros(0, dtype=np.int8))
    s = v1.UserState(h, [], [], [], 0, len(work_aff), len(work_aff))
    s.work_aff, s.genre_sim = np.array(work_aff, dtype=np.float32), np.array(genre_sim, dtype=np.float32)
    return s


class GenreTermTests(unittest.TestCase):
    def test_genre_similarity_counts_only_when_weighted(self):
        st = state([0.5, 0.5], [0.1, 0.9])
        self.assertEqual(v1.rank_feed(st, FEED, 10, v1.Config()).tolist(), [0, 1])
        self.assertEqual(v1.rank_feed(st, FEED, 10, v1.Config(w_genre=service.W_GENRE)).tolist(), [1, 0])

    def test_score_parts_add_the_genre_term_to_the_work_part(self):
        st, cfg = state([0.5, 0.4], [0.1, 0.9]), v1.Config(w_genre=0.5)
        parts = [service.score_parts(st, FEED, i, 10, cfg) for i in range(2)]
        self.assertAlmostEqual(parts[0][0], 0.5 + 0.5 * 0.1, places=5)
        self.assertEqual(v1.rank_feed(st, FEED, 10, cfg).tolist(), sorted(range(2), key=lambda i: -sum(parts[i])))


if __name__ == "__main__":
    unittest.main()
