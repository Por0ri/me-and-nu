import unittest

from model.algo import service

TEN_TYPES = {"news", "event", "guide", "people", "curation", "goods", "review", "character", "music", "humor"}


class AnswerTypeTests(unittest.TestCase):
    def test_six_q3_answers_cover_all_ten_types(self):
        expected = {"world": ["guide", "review"], "relationships": ["character", "people"], "ost": ["music"],
                    "goods": ["goods"], "events": ["event"], "news": ["news", "curation", "humor", "behind"]}
        for answer, types in expected.items():
            with self.subTest(answer=answer):
                self.assertEqual(service.user_types([answer]), types)
        covered = {t for answer in expected for t in service.user_types([answer])}
        self.assertEqual(covered - {"behind"}, TEN_TYPES)

    def test_answers_keep_order_and_drop_repeats(self):
        self.assertEqual(service.user_types(["news", "releases", "world"]),
                         ["news", "curation", "humor", "behind", "guide", "review"])

    def test_type_codes_pass_through_and_unknown_answers_drop(self):
        self.assertEqual(service.user_types(["people"]), ["people"])
        self.assertEqual(service.user_types(["unknown"]), [])


if __name__ == "__main__":
    unittest.main()
