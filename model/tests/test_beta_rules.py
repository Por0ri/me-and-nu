"""베타 추천 규칙 (11/16 · 11/17 결정): 여러 작품 글의 기준 작품 · 무게, 팬 추천 거르기, 곁들임 · 다른 작품 자리,
같은 소재 하루 하나, 고른 작품 밖 칩 맞춤 취향, 이유 문구"""
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from model.algo import anime_ranker as ar, content_v1 as v1, service

# 작품 번호 101 원피스 · 102 나루토 · 103 블리치 · 105 기생수 → 시리즈 대표 자리 0 · 1 · 2 · 3. 104 는 101 과 같은 시리즈
CTX = {"rep_of": {101: 1, 102: 2, 103: 3, 104: 1, 105: 4}, "row": {1: 0, 2: 1, 3: 2, 4: 3}, "cw_pos": np.array([0, 1, 2, 3]),
       "works": {101: {"title_ko": "원피스"}, 102: {"title_ko": "나루토"}, 103: {"title_ko": "블리치"}, 105: {"title_ko": "기생수"}},
       "X": np.array([[1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 1.0]]), "vnorm": np.array([1.0, 2 ** 0.5, 1.0, 1.0])}


def post(works, anchor=0, kind=None, ty="character", theme=None, slug=None, names=None):
    return {"type": ty, "kind": kind, "works": works, "anchor": anchor, "theme": theme, "slug": slug,
            "names": names or [CTX["works"].get(w, {}).get("title_ko", "") for w in works],
            "menu_work_id": works[anchor] if anchor is not None else None,
            "published": date(2026, 11, 18), "until": date(2026, 12, 31)}


class PostKindTests(unittest.TestCase):
    def test_rag_ids_give_kind_and_theme(self):
        self.assertEqual(service.post_kind("dict-curation-like-w123"), ("like", None, None))
        self.assertEqual(service.post_kind("dict-curation-overlap-tzombie1"), ("overlap", "zombie", "zombie"))
        self.assertEqual(service.post_kind("dict-curation-overlap-tzombie3-v2"), ("overlap", "zombie", "zombie"))
        self.assertEqual(service.post_kind("dict-curation-overlap-tpostapocalyptic2"), ("overlap", "postapocalyptic", "postapocalyptic"))
        self.assertEqual(service.post_kind("dict-curation-overlap-w105"), ("overlap", "w105", None))   # 옛 형식: 글마다 한 소재
        self.assertEqual(service.post_kind("dict-character-bdaymonth-c100217"), ("bdaymonth", None, None))
        self.assertEqual(service.post_kind("checking-fixture-20261028-v1-post-office-3"), (None, None, None))
        self.assertEqual(service.post_kind(None), (None, None, None))

    def test_anchor_rule(self):
        self.assertEqual(service.anchor_of([102], ["나루토"], "아무 제목", "artist"), 0)
        self.assertEqual(service.anchor_of([101, 102], ["원피스", "나루토"], "카즈마 파티 다음엔", "like"), 0)  # 줄임말이어도 1번
        self.assertEqual(service.anchor_of([101, 102], ["원피스", "나루토"], "〈나루토〉 제작사 작품들", "studio"), 0)
        self.assertEqual(service.anchor_of([101, 102], ["원피스", "나루토"], "〈나루토〉에도 그 가수의 노래가", "artist"), 1)
        self.assertIsNone(service.anchor_of([101, 102], ["원피스", "나루토"], "츤데레 애니 5선", "overlap"))
        self.assertIsNone(service.anchor_of([101, 102], ["원", "나루토"], "원작이 궁금해", "director"))   # 한 글자 이름은 안 봄
        self.assertIsNone(service.anchor_of([], [], "작품 없는 글", None))


class FeedTests(unittest.TestCase):
    def test_make_feed_links_other_works_except_fan_posts(self):
        posts = [post([102, 101], anchor=0, kind="artist"), post([101, 102], anchor=0, kind="like"),
                 post([102, 103], anchor=None, kind="overlap"), {**post([103]), "works": None}]
        feed = service.make_feed(posts, CTX)
        self.assertEqual(feed["pos"].tolist(), [1, 0, -1, 2])
        self.assertEqual(feed["rel_pos"].tolist(), [[0, -1], [-1, -1], [1, 2], [-1, -1]])
        self.assertEqual(feed["rel_w"].tolist(), [[0.5, 0.0], [0.0, 0.0], [0.5, 0.5], [0.0, 0.0]])

    def test_feed_without_linked_works_has_no_extra_columns(self):
        feed = service.make_feed([{**post([101]), "works": None}], CTX)
        self.assertNotIn("rel_pos", feed)

    def test_work_part_is_the_largest_weighted_work_not_the_sum(self):
        state = SimpleNamespace(work_aff=np.array([1.0, 0.2, 0.4]), work_ev=np.zeros(3), genre_sim=None)
        rel = np.array([[0, -1], [1, 2], [-1, -1], [-1, -1]])
        feed = {"pos": np.array([1, -1, 0, -1]), "rel_pos": rel, "rel_w": np.where(rel >= 0, 0.5, 0.0)}
        parts = v1.work_parts(state, feed, np.arange(4), v1.Config())
        self.assertTrue(np.allclose(parts, [0.5, 0.2, 1.0, 0.0]))
        # 시뮬레이터 글 표 (같이 연결된 작품 없음) 는 예전처럼 기준 작품 몫만
        self.assertTrue(np.allclose(v1.work_parts(state, {"pos": np.array([1, -1, 0])}, np.arange(3), v1.Config()), [0.2, 0.0, 1.0]))


class TasteTests(unittest.TestCase):
    def test_other_works_follow_chip_fit_with_a_small_popularity_tie_break(self):
        ctx = {**CTX, "series": {}, "series_items": {}, "cw_rows": np.array([0, 1, 2, 3]),
               "feature_col": {"g:Horror": 0, "t:Gore": 1}, "pop_norm": np.array([0.1, 0.9, 1.0, 0.5]),
               "X": np.array([[1.0, 0.9], [1.0, 0.2], [0.0, 0.0], [1.0, 0.5]])}
        history = ar.History(np.array([0]), np.array([1]), np.array([ar.STATUS["completed"]], dtype=np.int8),
                             np.zeros(1, dtype=np.int8), np.zeros(1, dtype=np.int8))
        state = v1.UserState(history, ["Horror"], ["Gore"], [], 0, 4, 1)

        def old_rank(state, ctx, cfg):
            state.work_aff = np.array([1.0, 0.0, 0.9, 0.0], dtype=np.float32)   # 예전 v0.5: 인기작 (자리 2) 이 높음
        with patch.object(service.v1, "refresh_work_affinity", old_rank):
            service.work_affinity(state, ctx, v1.Config())
        fit = np.array([0.95, 0.6, 0.0, 0.75]) / 0.95
        expected = (fit + 0.3 * ctx["pop_norm"]) / 1.3
        expected[0] = 1.0                                                     # 고른 작품은 그대로 1
        self.assertTrue(np.allclose(state.work_aff, expected, atol=1e-6))
        self.assertGreater(state.work_aff[3], state.work_aff[2])              # 칩이 맞는 작품 > 칩이 없는 인기작
        self.assertEqual(state.chip_names, ["Horror", "Gore"])


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.posts = [post([101]) for _ in range(6)]                                          # 0~5 고른 작품 글
        self.posts += [post([103, 102], anchor=0, kind="like", ty="curation"),                 # 6 다른 작품 팬 추천
                       post([102, 101], anchor=0, kind="artist", ty="music"),                  # 7 곁들임: 원피스가 같이 나옴
                       post([102, 103], anchor=None, kind="overlap", ty="curation", theme="gore", slug="gore"),   # 8 고른 칩 소재
                       post([101, 103], anchor=None, kind="overlap", ty="curation", theme="gore", slug="gore"),   # 9 같은 소재
                       post([102]), post([102]), post([105])]                                  # 10 · 11 · 12 다른 작품
        self.feed = service.make_feed(self.posts, CTX)
        self.order = [6, 0, 7, 1, 8, 2, 9, 10, 3, 11, 4, 12, 5]

    def compose(self, seen=None):
        return service.compose(self.order, self.feed, self.posts, [101], {"gore"}, CTX, seen=seen)

    def test_slots_follow_the_day_rules(self):
        out = self.compose()
        self.assertEqual([i for i, _ in out], [0, 1, 2, 7, 3, 10, 4, 8, 5, 12, 11])
        self.assertEqual([s for _, s in out], ["pinned", "pinned", "pinned", "side", "pinned", "discover", "pinned", "side",
                                               "pinned", "discover", "score"])
        self.assertNotIn(6, [i for i, _ in out])          # 고르지 않은 작품의 팬 추천은 안 보여 줌
        self.assertNotIn(9, [i for i, _ in out])          # 같은 소재 모음은 하루 하나

    def test_fan_post_of_a_picked_work_stays_on_top(self):
        self.posts[6] = post([101, 102], anchor=0, kind="like", ty="curation")
        self.feed = service.make_feed(self.posts, CTX)
        self.assertEqual(self.compose()[0], (6, "pinned"))

    def test_a_side_post_seen_before_waits_for_its_score_turn(self):
        seen = np.zeros(len(self.posts), dtype=np.int16)
        seen[7] = 1
        out = dict((i, s) for i, s in self.compose(seen))
        self.assertEqual(out[8], "side")
        self.assertEqual(out[7], "score")

    def test_a_resting_post_gives_its_slot_to_the_next_one(self):   # 11/19: 쉬는 글은 자리 대신 점수 순서로
        ready = np.ones(len(self.posts), dtype=bool)
        ready[0] = ready[7] = ready[10] = False
        out = service.compose(self.order, self.feed, self.posts, [101], {"gore"}, CTX, ready=ready)
        self.assertEqual(out[0], (1, "pinned"))
        slots = dict(out)
        self.assertEqual([slots[0], slots[7], slots[10]], ["score", "score", "score"])
        self.assertEqual(slots[8], "side")
        self.assertEqual(slots[11], "discover")

    def test_at_most_two_side_posts_a_day(self):
        self.posts += [post([102, 101], anchor=0, kind="director", ty="people") for _ in range(3)]
        self.feed = service.make_feed(self.posts, CTX)
        self.order += [13, 14, 15]
        self.assertEqual(sum(s == "side" for _, s in self.compose()), 2)


class ExplainTests(unittest.TestCase):
    def setUp(self):
        w = np.zeros((4, 2), dtype=np.float32)
        w[3] = [1.0, 0.8]                                             # 기생수 = 호러 1 · 고어 0.8
        self.state = SimpleNamespace(chip_w=w, chip_names=["Horror", "Gore"])
        self.labels = {"Horror": "호러", "Gore": "고어"}

    def text(self, p, parts=(1.0, 0.5, 0.1, 0.0), slot="score"):
        return service.explain(parts, p, slot, [101], self.state, CTX, self.labels)["text"]

    def test_picked_work_posts_say_what_kind_of_post(self):
        self.assertEqual(self.text(post([101, 102], kind="like", ty="curation")), "‘원피스’ 팬들이 추천한 다음 작품이에요")
        self.assertEqual(self.text(post([104], kind="intro")), "고른 ‘원피스’의 캐릭터 이야기예요")   # 같은 시리즈 → 고른 작품 이름
        self.assertEqual(self.text(post([101], kind="origin", ty="guide")), "고른 ‘원피스’의 원작 이야기예요")
        self.assertEqual(self.text(post([101], kind="bdaymonth")), "고른 ‘원피스’ 캐릭터의 생일 글이에요")
        self.assertEqual(self.text(post([101], ty="music")), "고른 ‘원피스’의 노래 이야기예요")
        self.assertEqual(self.text(post([101], ty="news")), "고른 ‘원피스’의 새 소식이에요")

    def test_picked_work_collection_reads_as_related(self):
        self.assertEqual(self.text(post([101, 102], kind="studio", ty="curation")), "고른 ‘원피스’ 관련 모음이에요")

    def test_side_posts_name_the_picked_work_or_the_chip(self):
        self.assertEqual(self.text(post([102, 101], kind="artist", ty="music")), "‘원피스’도 나오는 글이에요")
        both = {**post([102, 103, 101], kind="director", ty="people"), "title": "〈나루토〉도 〈원피스〉도 같은 감독"}
        why = service.explain((1.0, 0.5, 0.1, 0.0), both, "side", [101, 103], self.state, CTX, self.labels)
        self.assertEqual(why["text"], "‘원피스’도 나오는 글이에요")   # 고른 작품이 둘이면 제목에 나온 쪽 (블리치보다 원피스)
        theme = post([102, 103], anchor=None, kind="overlap", ty="curation", theme="gore", slug="gore")
        self.assertEqual(self.text(theme, slot="side"), "고른 ‘고어’ 소재 모음이에요")

    def test_other_works_name_the_chips_that_matched(self):
        p = post([105], ty="music")
        self.assertEqual(self.text(p), "고른 ‘호러’ · ‘고어’ 취향에 맞는 작품이에요")
        self.assertEqual(self.text(p, parts=(0.2, 0.5, 0.1, 0.0)), "고른 ‘호러’ 작품의 노래 이야기예요")
        self.assertEqual(self.text(p, parts=(0.1, 0.0, 0.3, 0.0)), "새로 올라온 ‘호러’ 작품 글이에요")

    def test_without_chips_the_reason_falls_back(self):
        self.assertEqual(self.text(post([102])), "‘원피스’와 분위기가 닮은 작품이에요")
        self.assertEqual(self.text(post([102]), parts=(0.2, 0.5, 0.1, 0.0)), "관심 있는 캐릭터 글이에요")
        news = {**post([101], ty="news"), "works": [], "anchor": None, "menu_work_id": None}
        self.assertEqual(self.text(news), "새로 올라온 글이에요")

    def test_reason_keeps_the_slot(self):
        why = service.explain((1.0, 0.5, 0.1, 0.0), post([105]), "discover", [101], self.state, CTX, self.labels)
        self.assertEqual((why["slot"], why["kind"], why["work"]), ("discover", "chips", 105))


if __name__ == "__main__":
    unittest.main()
