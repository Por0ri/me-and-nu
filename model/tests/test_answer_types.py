import unittest

from model.algo import content_v1 as v1, service

TEN_TYPES = {"news", "event", "guide", "people", "curation", "goods", "review", "character", "music", "humor"}


class AnswerTypeTests(unittest.TestCase):
    def test_six_q3_answers_cover_all_ten_types(self):
        expected = {"world": ["guide", "review"], "relationships": ["character", "people"], "ost": ["music"],
                    "goods": ["goods"], "events": ["event"], "news": ["news", "curation", "humor", "behind"]}
        self.assertEqual(tuple(expected), service.Q3_ANSWERS)
        for answer, types in expected.items():
            with self.subTest(answer=answer):
                self.assertEqual(list(service.user_types([answer])), types)
        covered = {t for answer in expected for t in service.user_types([answer])}
        self.assertEqual(covered - {"behind"}, TEN_TYPES)

    def test_answers_keep_order_and_drop_repeats(self):
        self.assertEqual(list(service.user_types(["news", "releases", "world"])),
                         ["news", "curation", "humor", "behind", "guide", "review"])

    def test_rank_weights_follow_answer_order(self):
        # 답 하나의 유형은 모두 그 답의 순위 무게, 겹치는 유형은 앞 순위 무게, 무게 목록보다 뒤 순위는 안 씀
        w = (1.0, 0.5, 0.25)
        self.assertEqual(service.user_types(["ost", "world", "releases", "news"], w),
                         {"music": 1.0, "guide": 0.5, "review": 0.5, "news": 0.25})
        self.assertEqual(service.user_types(["ost"], w), {"music": 1.0})

    def test_first_answer_keeps_full_weight(self):
        # 예전 회원 (답 하나) 점수가 그대로이려면 1순위 무게 = 1
        self.assertEqual(service.TYPE_RANK_W[0], 1.0)
        self.assertEqual(len(service.TYPE_RANK_W), len(service.Q3_ANSWERS))

    def test_linear_rank_weights(self):
        # EXP-39: 선형 (7 − 순위) ÷ 6 → 6개 다 고르면 마지막 답도 0 보다 큼
        self.assertEqual(service.user_types(list(service.Q3_ANSWERS)),
                         {"guide": 1.0, "review": 1.0, "character": 0.833, "people": 0.833, "music": 0.667, "goods": 0.5,
                          "event": 0.333, "news": 0.167, "curation": 0.167, "humor": 0.167, "behind": 0.167})

    def test_q3_ranks_are_used_only_when_their_first_matches(self):
        self.assertEqual(service.q3_answers("ost", [(1, "ost"), (2, "news")]), ["ost", "news"])
        self.assertEqual(service.q3_answers("world", [(1, "ost"), (2, "news")]), ["world"])   # 운영 옛 코드가 1순위만 바꿈
        self.assertEqual(service.q3_answers("ost", [(2, "ost")]), ["ost"])                    # rank 1 줄이 없음
        self.assertEqual(service.q3_answers("ost", None), ["ost"])                            # 새 표가 생기기 전 회원
        self.assertEqual(service.q3_answers(None, None), [])                                  # 예전 (v1) 답

    def test_old_v1_second_answer_keeps_full_weight(self):
        # 예전 (v1) q2 '받고 싶은 소식' 은 Q3 와 다른 질문 → 순위 무게가 아니라 1 (예전 점수 그대로)
        self.assertEqual(service.answer_types(["ost"], ["events"]), {"music": 1.0, "event": 1.0})
        self.assertEqual(service.answer_types(["ost", "world"], ["releases"]),
                         {"music": 1.0, "guide": 0.833, "review": 0.833, "news": 1.0})
        self.assertEqual(service.answer_types(["ost", "news"]), {"music": 1.0, "news": 0.833, "curation": 0.833,
                                                                  "humor": 0.833, "behind": 0.833})

    def test_every_type_belongs_to_one_q3_group(self):
        self.assertEqual(set(service.GROUP_OF), set(v1.TYPES))
        self.assertEqual(set(service.GROUP_OF.values()), set(service.Q3_ANSWERS))

    def test_type_codes_pass_through_and_unknown_answers_drop(self):
        self.assertEqual(service.user_types(["people"]), {"people": 1.0})
        self.assertEqual(service.user_types(["unknown"]), {})


if __name__ == "__main__":
    unittest.main()
