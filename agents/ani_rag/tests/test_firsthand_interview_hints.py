from types import SimpleNamespace
from unittest import TestCase

from ani_rag.article_credits import ann_interview_answer_hint, article_credits, chunk_credit_matches, complete_named_interview_answer
from ani_rag.content import web_claims_can_generate
from ani_rag.interview_story import interview_subject, verified_interview_answer
from ani_rag.models import RagContentType
from ani_rag.source_evidence import interview_speaker_label


ROOT = "https://www.animenewsnetwork.com"
PAGE = ROOT + "/feature/2019-08-28/interview-demon-slayer-producer-yuma-takahashi/.149177"
EDITION = ROOT + "/encyclopedia/anime.php?id=21101"
HEAD = "Interview: Demon Slayer producer Yuma Takahashi"
ANSWER = "**Yūma Takahashi:** We drew the scene in ink before putting it on screen."
LATER = "We also tried a different approach for the ending."
BODY = (
    f"# {HEAD}\n\nby Reporter\n\n[Demon Slayer: Kimetsu no Yaiba]({EDITION}) is a TV series. "
    f"We spoke with producer [Yūma Takahashi]({ROOT}/encyclopedia/people.php?id=88219).\n\n"
    f"**How did you start the scene?**\n\n{ANSWER}\n\n**What about the ending?**\n\n{LATER}"
)
STAFF = "Producer\n:\nAkifumi Fujio\nHikaru Kondo\nMasanori Miyake\nYuma Takahashi\n2nd Key Animation\n:\nAnother Person"


def chunk(url=EDITION, source_id="ann_encyclopedia", text=STAFF):
    return SimpleNamespace(source_id=source_id, source_url=url, text=text)


def hint(body=BODY, refs=None):
    credits = article_credits(RagContentType.PEOPLE, body, title=HEAD, url=PAGE)
    return ann_interview_answer_hint(HEAD, PAGE, body, credits, refs if refs is not None else {"R2": chunk()})


class FirsthandInterviewHintTests(TestCase):
    def test_retrieval_prefers_the_staff_table_over_a_headline_or_a_different_edition(self):
        credits = article_credits(RagContentType.PEOPLE, BODY, title=HEAD, url=PAGE)
        self.assertEqual(credits[0].ann_staff_url, EDITION)
        self.assertEqual(chunk_credit_matches(credits, STAFF, source_url=EDITION), 1)
        self.assertEqual(chunk_credit_matches(credits, STAFF, source_url=EDITION + "0"), 0)
        self.assertEqual(chunk_credit_matches(credits, HEAD, source_url=EDITION), 0)

    def test_first_named_answer_points_only_to_the_linked_edition_staff_record(self):
        result = hint(refs={"R1": chunk(url=EDITION + "0"), "R2": chunk()})
        self.assertEqual(result, {"speaker": "Yūma Takahashi", "attribution_excerpt": ANSWER, "staff_credit_refs": ["R2"],
                                  "credit_passage": "producer Yūma Takahashi", "role": "producer",
                                  "question": "How did you start the scene?"})
        passage = ANSWER.split("** ")[1]
        self.assertEqual(interview_speaker_label(BODY, result["speaker"], ANSWER, passage), result["speaker"])
        self.assertIsNone(interview_speaker_label(BODY, result["speaker"], LATER, LATER))
        self.assertFalse(web_claims_can_generate([result], RagContentType.PEOPLE))

    def test_missing_name_or_staff_or_conflicting_speaker_is_not_a_hint(self):
        for bad in (
            BODY.replace("**Yūma Takahashi:** ", ""), BODY.replace("**Yūma Takahashi:", "**Ken Tanaka:"),
            BODY.replace(LATER, "**Other Speaker:** " + LATER), BODY.replace(LATER, "Other Speaker: " + LATER),
            BODY.replace("**How did you start the scene?**", "How did you start the scene?"),
        ):
            with self.subTest(body=bad):
                self.assertIsNone(hint(body=bad))
        for wrong in (
            chunk(url=EDITION + "0"), chunk(source_id="enwiki"),
            chunk(text=STAFF.replace("Producer\n", "Director\n")),
            chunk(text=STAFF.replace("Yuma Takahashi\n", "Director\n:\nYuma Takahashi\n")),
            chunk(text="Interview: Demon Slayer producer Yuma Takahashi"), chunk(text=STAFF.replace("Yuma", "Yuki")),
        ):
            with self.subTest(ref=wrong):
                self.assertIsNone(hint(refs={"R1": wrong}))

    def test_attach_the_printed_complete_answer_and_its_verified_staff_credit(self):
        passage = ANSWER.partition(":** ")[2]
        rows = [{"status": "attributed_quote", "claim": passage, "interview_answer": None}]
        restored = complete_named_interview_answer(rows, hint())
        self.assertEqual(restored["attribution_excerpt"], ANSWER)
        self.assertEqual(restored["speaker"], "Yūma Takahashi")
        self.assertEqual(interview_speaker_label(BODY, restored["speaker"], restored["attribution_excerpt"], passage),
                         restored["speaker"])
        self.assertEqual((rows[1]["claim"], rows[1]["evidence_refs"]), ("producer Yūma Takahashi", ["R2"]))
        for invalid in ({"status": "insufficient"}, {"speaker": "Another Person"}, {"claim": LATER},
                        {"claim": passage.split(" before")[0]}):
            with self.subTest(invalid=invalid):
                attempted = [{**rows[0], **invalid}]
                self.assertIsNone(complete_named_interview_answer(attempted, hint()))
                self.assertEqual(len(attempted), 1)
        vague = {"status": "attributed_quote", "claim": "Yes."}
        self.assertIsNone(complete_named_interview_answer([vague], {
            **hint(), "attribution_excerpt": "**Yūma Takahashi:** Yes.",
        }))

    def test_credited_works_and_reported_choices_are_separate_from_an_interview_answer(self):
        for title in ("Music and staff credits", "Character profiles", "Original Soundtrack"):
            self.assertIsNone(interview_subject(title, "The staff discussed the songs in an interview."))
        self.assertEqual(interview_subject("Violet Evergarden Interview - Director Taichi Ishidate", ""), "interview")
        focus = {"speaker": "Shoichiro Taguchi", "subject": "Tanaka", "detail": "his monologue encouraged him"}
        record = {"claim": "Shoichiro Taguchi chose Tanaka because his monologue encouraged him.", "kind": "rag_core"}
        for reference in ("Producer: Shoichiro Taguchi", "Taguchi chose Tanaka.", "Azuma chose Tanaka for his monologue.",
                          "Taguchi liked Yachi because her words helped him."):
            with self.subTest(reference=reference):
                self.assertIsNone(verified_interview_answer({**focus, "reference_excerpt": reference},
                                                            record, "preference", [reference]))
        self.assertIsNone(verified_interview_answer({**focus, "reference_excerpt": "Producer: Shoichiro Taguchi"},
                                                    record, "interview", ["Producer: Shoichiro Taguchi"]))
        reference = "Taguchi's favorite is Tanaka because his words gave Taguchi courage."
        self.assertIsNone(verified_interview_answer({**focus, "reference_excerpt": reference}, record, "preference", []))
        self.assertIsNone(verified_interview_answer({**focus, "detail": "chose Tanaka", "reference_excerpt": reference},
                                                    record, "preference", [reference]))
        self.assertEqual(verified_interview_answer({**focus, "reference_excerpt": reference}, record, "preference",
                                                   [reference]), focus)
