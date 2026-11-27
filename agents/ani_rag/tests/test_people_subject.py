import unittest

from ani_rag.people_subject import ann_people_credit_body, people_subjects, supported_credit


class PeopleSubjectTests(unittest.TestCase):
    def test_verified_named_staff_in_an_interview_when_model_omits_credit_fields(self):
        claims = [
            {"claim": "Interview With Attack on Titan The Final Season Director, CG Producer Streamed", "people_credit": None},
            {"claim": "director Yuichiro Hayashi", "people_credit": None},
            {"claim": "manga creator Hajime Isayama", "people_credit": None},
        ]
        self.assertEqual(people_subjects(claims), (
            ("Yuichiro Hayashi", "director Yuichiro Hayashi"),
            ("Hajime Isayama", "manga creator Hajime Isayama"),
        ))
        self.assertEqual(people_subjects([{"claim": "invading Paradis", "people_credit": None}]), ())

    def test_parse_person_not_their_work_character_or_studio(self):
        for passage, expected in (
            ("Tetsuro Araki directed [Death Note](https://ann.test/works/1).", {"Tetsuro Araki"}),
            ("Tetsuro Araki voiced [Eren Yeager](https://ann.test/characters/1).", {"Tetsuro Araki"}),
            ("The series was directed by Tetsuro Araki.", {"Tetsuro Araki"}),
            ("This film was produced by Kyoto Animation.", set()),
            ("Eren Yeager was voiced by Yuki Kaji.", {"Yuki Kaji"}),
            ("Interview With The Final Season Director, CG Producer Streamed", set()),
        ):
            with self.subTest(passage=passage):
                self.assertEqual({name for name, _ in people_subjects([{"claim": passage}])}, expected)
        for passage, name, relation in (
            ("Tetsuro Araki directed Death Note.", "Death Note", "directed"),
            ("Eren Yeager was voiced by Yuki Kaji.", "Eren Yeager", "voiced"),
            ("Eren Yeager was voiced by Yuki Kaji.", "Eren Yeager", "was voiced by"),
            ("This film was produced by Kyoto Animation.", "Kyoto Animation", "produced"),
            ("Director Tetsuro Araki and voice actor Yuki Kaji met.", "Yuki Kaji", "Director"),
            ("Eren is invading Paradis.", "Paradis", "invading"),
            ("감독은 아라키 테츠로, 성우는 카지 유우키.", "카지 유우키", "감독"),
        ):
            with self.subTest(passage=passage):
                self.assertFalse(supported_credit(passage, name, relation))
        self.assertTrue(supported_credit("It was directed by Tetsuro Araki.", "Tetsuro Araki", "directed by"))
        self.assertTrue(supported_credit("Director: Tom Lee, Director: Taro Yamada", "Taro Yamada", "Director"))
        self.assertTrue(supported_credit("원작자는 이사야마 하지메야.", "이사야마 하지메", "원작자"))
        self.assertTrue(supported_credit("監督は林祐一郎", "林祐一郎", "監督"))
        self.assertTrue(supported_credit("한국어 더빙은 최원형이 맡았어.", "최원형", "더빙"))

    def test_ann_late_staff_and_localized_cast_sections(self):
        people = "https://www.animenewsnetwork.com/encyclopedia/people.php?id="
        body = (
            "# Title\n" + "Blu-ray releases\n" * 2500 + "\n"
            "| Japanese staff |  | Japanese cast |  |\n"
            f"| **Director**: [Yuichiro Hayashi]({people}123)<br>**Script**: [Hiroshi Seko]({people}456)"
            f" | | [Yui Ishikawa]({people}789) as Mikasa | |\n\n"
            "| Korean cast | |\n" + f"| [Kim Example]({people}999) as Mikasa | |\n\nRecommendations: Other Series"
        )
        url = "https://www.animenewsnetwork.com/encyclopedia/anime.php?id=22277"
        result = ann_people_credit_body(url, body)
        self.assertIn("Director: Yuichiro Hayashi\nScript: Hiroshi Seko", result)
        self.assertIn("Japanese cast\nYui Ishikawa as Mikasa", result)
        self.assertIn("Korean cast\nKim Example as Mikasa", result)
        self.assertNotIn("Other Series", result)
        self.assertNotIn("Blu-ray", result)
        self.assertIsNone(ann_people_credit_body("https://example.com/encyclopedia/anime.php?id=1", body))
        self.assertIsNone(ann_people_credit_body(url, "Only a plot summary."))
