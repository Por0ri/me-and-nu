from dataclasses import replace
from datetime import datetime, timezone
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from ani_rag.content import CardDraft, ContentDraft, ImageAsset, SupportedClaim
from ani_rag.quality import QualityIssue, validate_carousel_readability, validate_draft
from ani_rag.review import HeadlineEdit, ReviewDecision, ReviewInput, ReviewResult, ReviewRoute, route_review
from ani_rag.model_services import COPY_DECK_SCHEMA, EvidenceBoundIndependentReviewer
from ani_rag.models import CollectionMethod, EvidenceDocument, EvidenceType, RagContentType, WorkLinkStatus
from ani_rag.agents.content_generation.layout_director import LAYOUT_SCHEMA
from ani_rag.queues import QueueItem, QueueName, QueueStatus
from ani_rag.workers import (
    DraftReviewContext,
    ReviewWorker,
    _apply_deterministic_review_gates,
)


class ReviewGateTests(unittest.TestCase):
    def test_review_preview_fits_card_copy_by_height_and_routes_actual_overflow_to_revision(self):
        from io import BytesIO
        from PIL import Image, ImageFont
        from ani_rag.agents.content_generation.renderer import HEIGHT, WIDTH, render_review_preview

        draft = self.character_draft()
        card = replace(draft.cards[0], headline="First line\nSecond line\nThird line",
                       body="First point\nSecond point\nThird point\nFourth point")
        long = replace(draft.cards[1], body="A line\n" * 24)
        draft = replace(draft, cards=(card, long))
        png = BytesIO()
        Image.new("RGB", (WIDTH, HEIGHT)).save(png, format="PNG")
        original = png.getvalue()
        # Use Pillow's bundled test font so CI needs no platform-specific Korean font.
        font_bytes = ImageFont.load_default(size=16).font_bytes
        truetype = ImageFont.truetype
        model = AsyncMock()
        reviewer = EvidenceBoundIndependentReviewer(model)
        with patch("ani_rag.agents.content_generation.renderer.Path.is_file", return_value=True), \
                patch("PIL.ImageFont.truetype", side_effect=lambda _path, size: truetype(BytesIO(font_bytes), size)):
            preview = render_review_preview(original, card)
            with Image.open(BytesIO(preview)) as rendered:
                self.assertEqual(rendered.size, (WIDTH, HEIGHT))
            result = asyncio.run(reviewer.review(ReviewInput(
                draft, {}, {}, {}, {c.card_id: original for c in draft.cards},
            )))
        self.assertEqual(png.getvalue(), original)
        self.assertEqual([issue.code for issue in result.issues], ["CARD_COPY_OVERFLOW"])
        self.assertIn("2번 카드", result.revision_instructions[0])
        self.assertIs(route_review(result, 0), ReviewRoute.GENERATION_QUEUE)
        self.assertIs(route_review(result, 3), ReviewRoute.ARCHIVE)
        model.generate_json.assert_not_awaited()
        model.generate_json_with_images.assert_not_awaited()

    def test_review_usage_from_each_provider_response_is_persisted_with_its_own_draft(self):
        import json
        import sqlite3
        from io import BytesIO
        from pathlib import Path
        from types import SimpleNamespace
        from PIL import Image
        from ani_rag.model_services import OpenAIResponsesStructuredModel
        from ani_rag.storage.sqlite_core import SQLiteRagStore
        from ani_rag.storage.sqlite_workflow import SQLiteReviewHistoryRepository

        db = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(db.close)
        schema = Path(__file__).resolve().parents[1] / "src/ani_rag/storage/schema.sql"
        db.executescript(schema.read_text(encoding="utf-8"))
        history = SQLiteReviewHistoryRepository(SQLiteRagStore(db))
        for key in ("vision", "text", "unreported"):
            db.execute("""INSERT INTO rag_content_draft(draft_id,content_type,title,body,source_footer,
                       ai_disclosure,status,created_at,updated_at) VALUES(?,'guide','title','body',
                       'source','ai','pending_review','t','t')""", (key,))
        db.commit()
        parsed = {"decision": "pass", "issues": [], "revision_instructions": [], "evidence_requests": [],
                  "scorecard": {"cards": []}, "model_name": "claimed-in-copy", "input_tokens": 999999}
        shared = OpenAIResponsesStructuredModel.__new__(OpenAIResponsesStructuredModel)
        shared.model = "configured-alias"
        async def provider(**kwargs):
            content = kwargs["input"][1]["content"]
            vision = isinstance(content, list)
            await asyncio.sleep(0)
            return SimpleNamespace(output_text=json.dumps(parsed), model="snapshot-vision" if vision else "snapshot-text",
                                   usage=SimpleNamespace(input_tokens=20 if vision else 10, output_tokens=7,
                                                         input_tokens_details=SimpleNamespace(cached_tokens=0) if vision else None))
        shared.client = SimpleNamespace(responses=SimpleNamespace(create=provider))
        ordinary_mock = AsyncMock()
        ordinary_mock.generate_json.return_value = parsed
        png = BytesIO()
        Image.new("RGB", (2, 2)).save(png, format="PNG")

        async def review_and_save(key):
            draft = replace(self.character_draft(), draft_id=key)
            rendered = {card.card_id: png.getvalue() for card in draft.cards} if key == "vision" else {}
            reviewer = EvidenceBoundIndependentReviewer(ordinary_mock if key == "unreported" else shared)
            result = await reviewer.review(ReviewInput(draft, {}, {}, {}, rendered))
            await asyncio.sleep(0)
            await history.save_result(result, 0, datetime.now(timezone.utc))
        async def run():
            await asyncio.gather(*(review_and_save(key) for key in ("vision", "text", "unreported")))
        with patch("ani_rag.model_services._enforce_review_scorecard", return_value=([], (), 95)), \
                patch("ani_rag.model_services.render_review_preview", side_effect=lambda png, _: png):
            asyncio.run(run())
        rows = {key: (name, json.loads(tokens) if tokens else None) for key, name, tokens in
                db.execute("SELECT draft_id,model_name,token_cost_json FROM rag_judgment_log")}
        self.assertEqual(rows, {"text": ("snapshot-text", {"input_tokens": 10, "output_tokens": 7}),
                                "vision": ("snapshot-vision", {"input_tokens": 20, "output_tokens": 7, "cached_input_tokens": 0}),
                                "unreported": (None, None)})

    def test_only_used_work_scoped_references_must_match_one_of_the_draft_works(self):
        def source(doc, state, ids=(), candidates=()):
            return EvidenceDocument(
                doc, RagContentType.PEOPLE, doc, "https://example.org/" + doc, "reference",
                datetime.now(timezone.utc), EvidenceType.REFERENCE_FACT, CollectionMethod.MCP_READ,
                "ko", body="출처의 원문", work_link_status=state, menu_work_ids=ids,
                candidate_menu_work_ids=candidates,
            )

        evidence = {
            "first": source("first", WorkLinkStatus.MATCHED, (1,)),
            "second": source("second", WorkLinkStatus.MATCHED, (2, 4)),
            "general": source("general", WorkLinkStatus.NOT_APPLICABLE),
            "other": source("other", WorkLinkStatus.MATCHED, (8,)),
            "undecided": source("undecided", WorkLinkStatus.AMBIGUOUS, candidates=(1, 8)),
        }
        # Only documents attached to a claim or used by the web verifier count.
        cited = ("first", "second", "general")
        draft = replace(self.make_draft(()), content_type=RagContentType.PEOPLE,
                        evidence_ids=tuple(evidence), retrieved_chunk_ids=("chunk",), menu_work_ids=(1, 2),
                        detail_claims=(SupportedClaim("콜라보", cited, ("chunk",)),),
                        ai_judgment_basis={"web_discovery_candidate": {"corroborating_document_ids": ["gate"]}})
        def work_issues(subject, gate):
            return [x for x in validate_draft(subject, evidence, {}, {}, web_gate_work_ids={"gate": gate})
                    if x.code in {"CITED_WORK_LINK_UNCONFIRMED", "WEB_EVIDENCE_WORK_LINK_UNCONFIRMED"}]
        self.assertEqual(work_issues(draft, (2,)), [])
        changed = replace(draft, detail_claims=(
            SupportedClaim("주장", (*cited, "other", "undecided"), ("chunk",)),
        ))
        issues = work_issues(changed, ())
        self.assertEqual([i.code for i in issues].count("CITED_WORK_LINK_UNCONFIRMED"), 2)
        self.assertTrue(any("other" in i.message and "8" in i.message for i in issues))
        self.assertTrue(any("undecided" in i.message and "ambiguous" in i.message for i in issues))
        self.assertTrue(any(i.code == "WEB_EVIDENCE_WORK_LINK_UNCONFIRMED" for i in issues))
        passed = ReviewResult(changed.draft_id, ReviewDecision.PASS, (), reviewer_id="reviewer")
        result = _apply_deterministic_review_gates(passed, issues)
        self.assertIs(route_review(result, revision_count=4), ReviewRoute.COLLECTION_QUEUE)

    def make_draft(self, cards):
        return ContentDraft(
            draft_id="draft-test",
            content_type="guide",
            title="테스트 제목",
            cards=tuple(cards),
            detail_body="본문",
            detail_claims=(),
            evidence_ids=("evidence-1",),
            menu_work_ids=(1,),
            retrieval_id="retrieval-test",
            created_at=datetime.now(timezone.utc),
            source_footer="https://example.com/source",
            retrieved_chunk_ids=("chunk-1",),
        )

    def test_two_card_carousel_is_allowed(self):
        draft = self.make_draft((
            CardDraft("cover", "cover", "표지", "본문", (), "image-1"),
            CardDraft("ending", "ending", "마무리", "본문", (), "image-2"),
        ))
        codes = {issue.code for issue in validate_carousel_readability(draft)}
        self.assertNotIn("CAROUSEL_CARD_COUNT", codes)
        self.assertEqual(COPY_DECK_SCHEMA["properties"]["cards"]["minItems"], 2)
        self.assertEqual(LAYOUT_SCHEMA["properties"]["cards"]["minItems"], 2)

    def test_source_metadata_copy_is_blocking(self):
        draft = self.make_draft((
            CardDraft("cover", "cover", "출처 확인", "작품 정보를 DB에서 확인했어", (), "image-1"),
            CardDraft("ending", "ending", "마무리", "본문", (), "image-2"),
        ))
        issue = next(
            issue for issue in validate_carousel_readability(draft)
            if issue.code == "SOURCE_METADATA_AS_COPY"
        )
        self.assertTrue(issue.blocking)

    def character_draft(self):
        cards = (
            CardDraft("cover", "cover", "사이타마의 한 방", "괴인 앞에 취미로 히어로를 하는 남자가 섰어.", (), "image-1"),
            CardDraft("ending", "ending", "강함의 역설", "지루함도 한 방에 날릴 수 있을까?", (), "image-2"),
        )
        return replace(
            self.make_draft(cards), content_type=RagContentType.CHARACTER,
            title="취미로 히어로를 하는 사이타마", detail_body="괴인을 한 방에 쓰러뜨리는 사이타마의 고민은 지루함이야.",
        )

    def high_scoring_review(self, draft, *, decision="pass", issues=(), revision_instructions=(),
                            evidence_requests=(), conflicts=(), page_score=95, primary_web_source=None,
                            people_focus=None, retrieved_chunks=None, interview_focus=None):
        card_score = dict.fromkeys((
            "korean_naturalness", "translation_naturalness", "reader_clarity", "fan_engagement",
            "card_flow", "safety_respect", "image_relevance", "image_composition",
        ), 5)
        model = AsyncMock()
        model.generate_json.return_value = {
            **({"people_focus": people_focus} if people_focus is not None else {}),
            **({"interview_answer_focus": interview_focus} if interview_focus is not None else {}),
            "decision": decision, "issues": list(issues), "revision_instructions": list(revision_instructions),
            "title_fact_conflicts": list(conflicts),
            "evidence_requests": list(evidence_requests),
            "scorecard": {
                "cards": [dict(card_score, page=page, card_score=page_score, safety_blocker=False,
                               safety_category=None, feedback="좋아", visual_feedback="좋아")
                          for page in range(1, len(draft.cards) + 1)],
                "detail": {"naturalness": 5, "factual_support": 5, "coherence": 5,
                           "completeness": 5, "feedback": "좋아"},
                "aggregate_score": 95,
            },
        }
        return asyncio.run(EvidenceBoundIndependentReviewer(model).review(ReviewInput(
            draft, {}, {}, retrieved_chunks or {}, primary_web_source=primary_web_source,
        )))

    def test_people_review_needs_a_real_contributor_in_the_copy_with_a_verified_role(self):
        from ani_rag.content import web_claims_can_generate
        for source, person, role, translated, reader, credit in (
            ("director Yuichiro Hayashi", "Yuichiro Hayashi", "director", "하야시 유이치로가 감독을 맡았어.", "하야시 유이치로", "감독"),
            ("声優の石川由依", "石川由依", "声優", "성우 이시카와 유이가 참여했어.", "이시카와 유이", "성우"),
            ("한국어판에서 최원형이 주연을 더빙했어.", "최원형", "더빙", "주연 목소리를 맡은 건 최원형이야.", "최원형", "주연 목소리"),
            ("原作者は諫山創", "諫山創", "原作者", "원작 작가는 이사야마 하지메야.", "이사야마 하지메", "원작 작가"),
        ):
            verified = [{"claim": source, "kind": "rag_core", "standalone": True,
                         "people_credit": {"name": person, "relation": role}}]
            self.assertTrue(web_claims_can_generate(verified, RagContentType.PEOPLE))
            basis = {"web_discovery_candidate": {"verified_claims": verified}}
            draft = replace(self.character_draft(), content_type=RagContentType.PEOPLE,
                            ai_judgment_basis=basis, detail_body=translated)
            focus = {"source_name": person, "source_excerpt": source, "source_relation": role,
                     "reader_name": reader, "relation": credit, "excerpt": translated, "location": "detail_article",
                     "same_person": True, "same_contribution": True}
            self.assertEqual(self.high_scoring_review(draft, people_focus=focus).decision, ReviewDecision.PASS)
            plot = replace(draft, detail_body="침공이 시작됐어.")
            self.assertEqual(self.high_scoring_review(plot, people_focus=focus).decision, ReviewDecision.REVISE)

    def test_people_review_binds_the_same_person_role_and_reader_location(self):
        name, source = "Yuichiro Hayashi", "Director: Yuichiro Hayashi"
        verified = [{"kind": "rag_core", "claim": source, "people_credit": {"name": name, "relation": "Director"}},
                    {"kind": "rag_core", "claim": "Composer: Hiroyuki Sawano",
                     "people_credit": {"name": "Hiroyuki Sawano", "relation": "Composer"}}]
        draft = replace(self.character_draft(), content_type=RagContentType.PEOPLE,
                        ai_judgment_basis={"web_discovery_candidate": {"verified_claims": verified}})
        focus = {"source_name": name, "source_excerpt": source, "source_relation": "Director",
                 "location": "detail_article", "same_person": True, "same_contribution": True}
        for reader, contribution, changes, expected in (
            ("Hayashi Yūichirō", "directed", {}, ReviewDecision.PASS),
            ("Tetsuro Araki", "directed", {}, ReviewDecision.REVISE),
            ("Yuichiro Hayashi", "composed the music", {}, ReviewDecision.REVISE),
            ("Yuichiro Hayashi", "composed", {"source_excerpt": "Composer: Hiroyuki Sawano",
                                               "source_relation": "Composer"}, ReviewDecision.REVISE),
            ("하야시 유이치로", "감독", {"location": "card:cover"}, ReviewDecision.REVISE),
            ("하야시 유이치로", "감독", {"same_person": False}, ReviewDecision.REVISE),
        ):
            with self.subTest(reader=reader, contribution=contribution, changes=changes):
                line = f"{reader} — {contribution}."
                review = self.high_scoring_review(replace(draft, detail_body=line), people_focus={
                    **focus, "reader_name": reader, "relation": contribution, "excerpt": line, **changes})
                self.assertEqual(review.decision, expected)
                if expected == ReviewDecision.REVISE:
                    self.assertTrue(any(issue.code == "PEOPLE_COPY_OFF_TOPIC" for issue in review.issues))

    def test_people_review_matches_the_speaker_of_a_verified_interview_answer(self):
        quote = "We drew the characters repeatedly."
        name = "Chikashi Kubota"
        basis = {"web_discovery_candidate": {"verified_claims": [
            {"kind": "attributed_quote", "claim": quote, "speaker": name, "attribution_excerpt": f"{name}: {quote}"},
        ]}}
        line = "구보타 치카시는 캐릭터를 여러 차례 그렸다고 답했어."
        draft = replace(self.character_draft(), content_type=RagContentType.PEOPLE, detail_body=line, ai_judgment_basis=basis)
        focus = dict(source_name=name, reader_name="구보타 치카시", source_excerpt=quote,
                     source_relation="drew the characters repeatedly", relation="캐릭터를 여러 차례 그렸", excerpt=line,
                     location="detail_article", same_person=True, same_contribution=True)
        self.assertEqual(self.high_scoring_review(draft, people_focus=focus).decision, ReviewDecision.PASS)
        for changes in (dict(source_name="Another Person"), dict(source_relation="We changed the series"),
                        dict(same_contribution=False)):
            self.assertEqual(self.high_scoring_review(draft, people_focus={**focus, **changes}).decision, ReviewDecision.REVISE)
        title_only = replace(draft, title=line, detail_body="인물 인터뷰")
        self.assertEqual(self.high_scoring_review(title_only, people_focus={**focus, "location": "title"}).decision,
                         ReviewDecision.PASS)

    def test_people_review_can_point_to_exact_source_and_reader_sentences_without_bypassing_review(self):
        from ani_rag.model_services import _people_focus_options
        credit = {"kind": "rag_core", "claim": "producer Yūma Takahashi"}
        basis = {"verified_claims": [credit]}
        line = "《귀멸의 칼날》 프로듀서 유마 타카하시는 이 조합이 자연스럽다고 말했어."
        draft = replace(self.character_draft(), content_type=RagContentType.PEOPLE,
                        ai_judgment_basis={"web_discovery_candidate": basis}, detail_body=line)
        sources, readers = _people_focus_options(draft, basis)
        source = next(key for key, row in sources.items() if row["source_name"] == "Yūma Takahashi")
        reader = next(key for key, row in readers.items() if row == {"location": "detail_article", "excerpt": line})
        focus = dict(source_ref=source, reader_ref=reader, source_relation="producer", reader_name="유마 타카하시",
                     relation="프로듀서", same_person=True, same_contribution=True)
        passed = self.high_scoring_review(draft, people_focus=focus)
        self.assertEqual(passed.decision, ReviewDecision.PASS)
        self.assertTrue(passed.scorecard["people_focus_check"]["grounded"])
        for change in ({"reader_ref": "missing"}, {"source_ref": "missing"}, {"same_person": False},
                       {"same_contribution": False}, {"source_relation": "director"}, {"reader_name": "다른 사람"}):
            result = self.high_scoring_review(draft, people_focus={**focus, **change})
            self.assertEqual(result.decision, ReviewDecision.REVISE)
            self.assertFalse(result.scorecard["people_focus_check"]["grounded"])
        independent_issue = {"code": "WEB_REWRITE_FROM_EXISTING_SOURCES", "message": "과장된 인과관계", "blocking": True}
        result = self.high_scoring_review(draft, people_focus=focus, issues=(independent_issue,),
                                          primary_web_source={"title": "Interview", "original_text": "A conversation."})
        self.assertEqual(result.decision, ReviewDecision.REVISE)
        self.assertTrue(any(i.code == independent_issue["code"] and i.blocking for i in result.issues))

    def test_reference_only_people_review_requires_the_chunk_cited_at_the_reader_location(self):
        from ani_rag.retrieval import EvidenceChunk
        source = "Director: Yuichiro Hayashi"
        line = "감독은 하야시 유이치로야."
        focus = dict(source_name="Yuichiro Hayashi", reader_name="하야시 유이치로", source_excerpt=source,
                     source_relation="Director", relation="감독", excerpt=line, location="card:cover",
                     same_person=True, same_contribution=True)
        chunk = EvidenceChunk("credit", "e", RagContentType.PEOPLE, "Staff", source, "p1", "text", None, 0,
                              len(source), "ref", "https://example.com/staff", "en", None, (1,))
        draft = replace(self.character_draft(), content_type=RagContentType.PEOPLE,
                        evidence_ids=("e",), retrieved_chunk_ids=("credit", "unrelated"))
        for cited_id, expected in (("credit", ReviewDecision.PASS), ("unrelated", ReviewDecision.REVISE)):
            card = replace(draft.cards[0], body=line, claims=(SupportedClaim(line, ("e",), (cited_id,)),))
            result = self.high_scoring_review(replace(draft, cards=(card, draft.cards[1])), people_focus=focus,
                                              retrieved_chunks={"credit": chunk})
            self.assertEqual(result.decision, expected)

    def test_removable_web_detail_uses_revision_even_at_high_score(self):
        primary = {"title": "Interview", "original_text": "The director introduced the cast."}
        issue = {"code": "WEB_REWRITE_FROM_EXISTING_SOURCES", "message": "Remove the unsupported date.", "blocking": True}
        result = self.high_scoring_review(self.character_draft(), decision="revise", issues=(issue,),
                                          revision_instructions=("Remove the unsupported date.",),
                                          primary_web_source=primary)
        self.assertIs(route_review(result, 0), ReviewRoute.GENERATION_QUEUE)
        missing = self.high_scoring_review(self.character_draft(), decision="request_evidence", issues=(issue,),
                                           evidence_requests=("Confirm who was introduced.",),
                                           primary_web_source=primary)
        self.assertIs(route_review(missing, 0), ReviewRoute.COLLECTION_QUEUE)

    def test_confirmed_reader_copy_defects_route_to_revision_even_if_independently_missed(self):
        original = self.character_draft()
        cover, ending = original.cards
        for text, code in [
            ("C1은 내부 핸들이라 금지 … 실제 본문에 ref 안 써야", "INTERNAL_EDITORIAL_NOTE_LEAK"),
            ("크레딧を見ると、우리 눈과 귀가 같이 반응해.", "MIXED_LANGUAGE_COPY"),
        ]:
            for draft in (replace(original, title=text), replace(original, summary=text), replace(original, detail_body=text),
                          replace(original, cards=(replace(cover, headline=text), ending)),
                          replace(original, cards=(cover, replace(ending, body=text)))):
                gated = _apply_deterministic_review_gates(self.high_scoring_review(draft), validate_carousel_readability(draft))
                self.assertIs(route_review(gated, 0), ReviewRoute.GENERATION_QUEUE)
                self.assertTrue(any(i.code == code and i.blocking for i in gated.issues))
            # A different language or editorial instruction can be recognized semantically.
            reason = "2번 카드에 남은 집필 지시 또는 번역 누락을 고쳐줘."
            result = self.high_scoring_review(original, issues=[{"code": code, "message": reason, "blocking": False}])
            self.assertIs(route_review(result, 0), ReviewRoute.GENERATION_QUEUE)
            self.assertIn(reason, result.revision_instructions)
        clean = replace(original, detail_body="ONE과 Yūma Takahashi, 『君の名は。』가 등장해. "
                        "원어 ‘を見ると’와 대사 “크레딧を見ると、우리 눈과 귀가”도 인용했어. "
                        "C1 구역은 출입 금지야.")
        self.assertEqual(validate_carousel_readability(clean), ())
        self.assertIs(route_review(self.high_scoring_review(clean), 0), ReviewRoute.READY)

    def test_confirmed_image_mismatch_excludes_the_reviewed_asset_on_regeneration(self):
        original = self.character_draft()
        middle = replace(original.cards[1], card_id="middle", role="detail", image_asset_id="middle-image")
        draft = replace(original, cards=(original.cards[0], middle, original.cards[1]))
        reason = "3페이지는 로이드의 태그를 다루는데 이미지에는 안야가 크게 보여. 같은 작품의 이미지지만 카드 내용과 인물 초점이 어긋나."
        issue = {"code": "IMAGE_SUBJECT_MISMATCH", "message": reason, "blocking": False}
        for fields in ({}, {"image_asset_id": "image-1"}, {"image_asset_id": "image-2", "search_subject": "Loid Forger"}):
            result = self.high_scoring_review(draft, issues=[{**issue, **fields}])
            self.assertIs(route_review(result, 0), ReviewRoute.GENERATION_QUEUE)
            self.assertEqual(result.issues[0].asset_id, "image-2")
            self.assertIn(reason, result.revision_instructions)
        wrong = ImageAsset("image-2", "https://example.com/anya.jpg", "source", "matched", (1,), None, None,
                           "image.jpg", content_sha256="hash-anya")
        older = {"asset_id": "earlier", "search_subject": "TV series"}
        queues, drafts, history, reviewer = (AsyncMock() for _ in range(4))
        queues.claim.return_value = QueueItem("review", QueueName.REVIEW, status=QueueStatus.RUNNING, payload={
            "draft_id": draft.draft_id, "query": "스파이 패밀리", "content_type": "character", "menu_work_ids": [1],
            "review_rejected_images": [older],
        })
        drafts.load_for_review.return_value = DraftReviewContext(draft, {}, {wrong.asset_id: wrong}, {}, "retrieval", {})
        reviewer.review.return_value = result
        with patch("ani_rag.quality.validate_draft", return_value=()):
            asyncio.run(ReviewWorker(queues, drafts, history, reviewer).run_once())
        job = queues.enqueue.await_args.args[0]
        previous, current = job.payload["review_rejected_images"]
        self.assertEqual(previous, older)
        self.assertEqual((current["asset_id"], current["source_url"], current["content_sha256"], current["search_subject"]),
                         ("image-2", wrong.source_url, "hash-anya", "Loid Forger"))
        self.assertNotIn("headline_edits", job.payload)
        tv = self.high_scoring_review(draft, issues=[{**issue, "message": "2번 카드는 TV판인데 극장판 포스터야.",
                                                     "image_asset_id": "middle-image", "search_subject": "TV series"}])
        self.assertTrue(tv.issues[0].blocking)
        soft = self.high_scoring_review(draft, issues=[{"code": "IMAGE_SCENE_MISMATCH", "blocking": True,
                                                        "message": "같은 인물이지만 포즈가 달라."}])
        self.assertIs(route_review(soft, 0), ReviewRoute.READY)

    def test_verified_interview_answer_must_reach_the_reader(self):
        source = {"verified_claims": [{"interview_answer": {
            "speaker": "Actor A", "subject": "the setter", "detail": "his patience during the rally",
        }}]}
        reader = "A는 랠리 중에 보여 준 인내심 때문에 세터를 골랐어."
        draft = replace(self.character_draft(), detail_body=reader)
        delivered = {"answer_ref": "A1", "reader_excerpts": [reader], "faithfully_delivered": True}
        passed = self.high_scoring_review(draft, primary_web_source=source, interview_focus=delivered)
        self.assertIs(route_review(passed, 0), ReviewRoute.READY)
        for focus in ({**delivered, "faithfully_delivered": False}, {**delivered, "reader_excerpts": ["없는 대답"]},
                      {**delivered, "answer_ref": "unknown"}, None):
            result = self.high_scoring_review(draft, primary_web_source=source, interview_focus=focus)
            self.assertIs(route_review(result, 0), ReviewRoute.GENERATION_QUEUE)
            self.assertTrue(any(i.code == "WEB_REWRITE_FROM_EXISTING_SOURCES" and i.blocking for i in result.issues))

    def test_title_count_contradiction_overrides_81_and_keeps_scoped_revision(self):
        original = self.character_draft()
        draft = replace(original, title="한 방 뒤엔 이름 셋", cards=(
            replace(original.cards[0], headline="이름 셋이 받쳐!", body="나츠메 신고, 쿠보타 치카시, ONE, 무라타 유스케."),
            original.cards[1],
        ))
        contradiction = {"quoted_text": "셋", "replacement": "넷", "reason": "본문은 네 사람을 소개해."}
        result = self.high_scoring_review(
            draft, page_score=73, conflicts=[dict(contradiction, page=p) for p in (0, 1)],
        )
        self.assertEqual(result.scorecard["aggregate_score"], 81)
        self.assertIs(route_review(result, 0), ReviewRoute.GENERATION_QUEUE)
        self.assertEqual(result.headline_edits, (HeadlineEdit(0, draft.title, "한 방 뒤엔 이름 넷"),
                                                HeadlineEdit(1, "이름 셋이 받쳐!", "이름 넷이 받쳐!")))
        self.assertTrue(all(issue.blocking for issue in result.issues if issue.code == "TITLE_FACT_CONTRADICTION"))
        self.assertTrue(any("1번 카드" in instruction for instruction in result.revision_instructions))
        combined = self.high_scoring_review(
            draft, conflicts=(dict(contradiction, page=1),), issues=(
                {"code": "IMAGE_SCENE_MISMATCH", "message": "2번 카드의 이미지를 교체해.", "blocking": False},
            ),
        )
        self.assertIs(route_review(combined, 0), ReviewRoute.GENERATION_QUEUE)
        self.assertEqual(combined.headline_edits, ())
        additional = _apply_deterministic_review_gates(result, (QualityIssue("IMAGE_REUSED", "다른 이미지를 골라."),))
        self.assertEqual(additional.headline_edits, ())

    def test_soft_title_promise_feedback_alone_can_still_pass(self):
        result = self.high_scoring_review(self.character_draft(), issues=(
            {"code": "TITLE_PROMISE_MISMATCH", "message": "멋진 제목에 비해 본문 분위기는 차분해.", "blocking": False},
        ))
        self.assertIs(route_review(result, 0), ReviewRoute.READY)
        self.assertEqual(result.headline_edits, ())

    def test_review_worker_forwards_a_scoped_edit_to_generation(self):
        draft = self.character_draft()
        result = self.high_scoring_review(draft, conflicts=({
            "page": 1, "quoted_text": "한 방", "replacement": "두 방",
            "reason": "이 장면의 본문은 두 번째 타격까지 설명해.",
        },))
        queues, drafts, history, reviewer = (AsyncMock() for _ in range(4))
        queues.claim.return_value = QueueItem("review", QueueName.REVIEW, status=QueueStatus.RUNNING, payload={
            "draft_id": draft.draft_id, "query": "사이타마", "content_type": "character", "menu_work_ids": [1],
        })
        drafts.load_for_review.return_value = DraftReviewContext(draft, {}, {}, {}, "retrieval", {})
        reviewer.review.return_value = result
        with patch("ani_rag.quality.validate_draft", return_value=()):
            asyncio.run(ReviewWorker(queues, drafts, history, reviewer).run_once())
        generation_job = queues.enqueue.await_args.args[0]
        self.assertEqual(generation_job.payload["revision_count"], 1)
        self.assertEqual(generation_job.payload["draft_id"], draft.draft_id)
        self.assertEqual(generation_job.payload["headline_edits"][0]["after"], "사이타마의 두 방")

    def test_conflict_with_no_local_edit_uses_full_revision_or_evidence_route(self):
        draft = self.character_draft()
        conflict = {"page": 1, "reason": "본문 근거와 제목의 인물이 달라.", "quoted_text": "다른 제목", "replacement": None}
        result = self.high_scoring_review(draft, conflicts=(conflict,))
        self.assertIs(route_review(result, 0), ReviewRoute.GENERATION_QUEUE)
        self.assertEqual(result.headline_edits, ())
        result = self.high_scoring_review(draft, conflicts=(conflict,), evidence_requests=("역할 근거가 필요해.",))
        self.assertIs(route_review(result, 0), ReviewRoute.COLLECTION_QUEUE)

    def test_specific_evidence_request_overrides_high_score_and_survives_worker_gates(self):
        draft = self.character_draft()
        request = "사이타마의 강함 때문에 지루함을 느낀다는 점을 뒷받침하는 원문을 보태줘."
        for decision in ("pass", "request_evidence"):
            with self.subTest(decision=decision):
                result = self.high_scoring_review(draft, decision=decision, evidence_requests=(request,))
                gated = _apply_deterministic_review_gates(result, validate_carousel_readability(draft))
                self.assertEqual(result.decision, ReviewDecision.REQUEST_EVIDENCE)
                self.assertIn(request, gated.evidence_requests)
                self.assertEqual(route_review(gated, revision_count=0), ReviewRoute.COLLECTION_QUEUE)

    def test_editorial_request_in_reader_copy_routes_to_evidence_even_when_model_misses_it(self):
        draft = self.character_draft()
        placeholder = "캐릭터 소개 근거가 더 필요해"
        cover, ending = draft.cards
        for location, changed in (
            ("title", replace(draft, title=placeholder)),
            ("summary", replace(draft, summary=placeholder)),
            ("detail_body", replace(draft, detail_body=placeholder)),
            ("headline", replace(draft, cards=(replace(cover, headline=placeholder), ending))),
            ("body", replace(draft, cards=(cover, replace(ending, body=placeholder)))),
        ):
            with self.subTest(location=location):
                result = self.high_scoring_review(changed)
                gated = _apply_deterministic_review_gates(result, validate_carousel_readability(changed))
                self.assertEqual(gated.decision, ReviewDecision.REQUEST_EVIDENCE)
                self.assertTrue(gated.evidence_requests)
                self.assertEqual(route_review(gated, revision_count=0), ReviewRoute.COLLECTION_QUEUE)

    def test_character_article_with_no_editorial_request_remains_ready(self):
        draft = self.character_draft()
        result = self.high_scoring_review(draft)
        gated = _apply_deterministic_review_gates(result, validate_carousel_readability(draft))
        self.assertEqual(route_review(gated, revision_count=0), ReviewRoute.READY)

    def test_editorial_copy_with_available_evidence_returns_to_generation(self):
        draft = replace(self.character_draft(), title="캐릭터 소개 근거가 더 필요해")
        instruction = "제공된 원문에 나오는 사이타마의 행동으로 제목을 다시 써줘."
        result = self.high_scoring_review(
            draft, decision="revise", revision_instructions=(instruction,),
            issues=({"code": "EDITORIAL_COPY_INSTEAD_OF_ARTICLE", "blocking": True,
                     "message": "제공된 근거로 소개할 수 있지만 작성자 메모가 제목에 남았어."},),
        )
        self.assertEqual(result.decision, ReviewDecision.REVISE)
        issues = validate_carousel_readability(draft)
        self.assertIn("EDITORIAL_EVIDENCE_REQUEST", {issue.code for issue in issues})
        gated = _apply_deterministic_review_gates(result, issues)
        self.assertFalse(gated.evidence_requests)
        self.assertIn(instruction, gated.revision_instructions)
        self.assertEqual(route_review(gated, revision_count=0), ReviewRoute.GENERATION_QUEUE)

    def test_deterministic_blocker_overrides_model_pass_to_revision(self):
        result = ReviewResult(
            draft_id="draft-test",
            decision=ReviewDecision.PASS,
            issues=(),
            reviewer_id="test-reviewer",
        )
        gated = _apply_deterministic_review_gates(result, (
            QualityIssue("IMAGE_REUSED", "The carousel reuses an image."),
        ))
        self.assertEqual(gated.decision, ReviewDecision.REVISE)
        self.assertTrue(gated.issues[0].blocking)
        self.assertEqual(route_review(gated, revision_count=0), ReviewRoute.GENERATION_QUEUE)

    def test_third_revision_rejection_is_archived(self):
        result = ReviewResult(
            draft_id="draft-test",
            decision=ReviewDecision.REVISE,
            issues=(QualityIssue("COPY_REVIEW", "Revise the copy."),),
            revision_instructions=("Revise the copy.",),
            reviewer_id="test-reviewer",
        )
        self.assertEqual(route_review(result, revision_count=2), ReviewRoute.GENERATION_QUEUE)
        self.assertEqual(route_review(result, revision_count=3), ReviewRoute.ARCHIVE)

    def test_review_worker_routes_deterministic_image_reuse_to_revision(self):
        draft = self.make_draft((
            CardDraft("cover", "cover", "표지", "본문", (), "same-image"),
            CardDraft("ending", "ending", "마무리", "본문", (), "same-image"),
        ))
        context = DraftReviewContext(
            draft=draft, evidence={}, images={}, retrieved_chunks={},
            retrieval_id="retrieval-test", rendered_cards={},
        )
        review_job = QueueItem(
            job_id="review-job", queue=QueueName.REVIEW, status=QueueStatus.RUNNING,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            payload={
                "draft_id": "draft-test", "query": "test", "content_type": "guide",
                "menu_work_ids": (1,), "source_ids": (), "seed_evidence_ids": (),
                "revision_count": 0,
            },
        )
        pass_result = ReviewResult(
            draft_id="draft-test", decision=ReviewDecision.PASS,
            issues=(), reviewer_id="test-reviewer",
        )

        class Queues:
            def __init__(self):
                self.saved = []
                self.enqueued = []

            async def claim(self, queue, *, now, handler_kind=None):
                return review_job

            async def save(self, item):
                self.saved.append(item)

            async def enqueue(self, item):
                self.enqueued.append(item)
                return item

        class Drafts:
            def __init__(self):
                self.status = None

            async def load_for_review(self, draft_id):
                return context

            async def set_status(self, draft_id, status):
                self.status = status

        class History:
            async def save_result(self, result, revision_count, reviewed_at):
                self.result = result

        class Reviewer:
            async def review(self, review_input):
                return pass_result

        queues, drafts, history = Queues(), Drafts(), History()
        asyncio.run(ReviewWorker(queues, drafts, history, Reviewer()).run_once())

        self.assertEqual(drafts.status, "revision_requested")
        self.assertEqual(len(queues.enqueued), 1)
        self.assertEqual(queues.enqueued[0].queue, QueueName.GENERATION)
        self.assertEqual(queues.saved[0].status, QueueStatus.SUCCEEDED)
        self.assertEqual(history.result.decision, ReviewDecision.REVISE)


if __name__ == "__main__":
    unittest.main()
