from dataclasses import replace
from datetime import datetime, timezone
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from ani_rag.content import CardDraft, ContentDraft, SupportedClaim
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
                            evidence_requests=(), conflicts=(), page_score=95, primary_web_source=None):
        card_score = dict.fromkeys((
            "korean_naturalness", "translation_naturalness", "reader_clarity", "fan_engagement",
            "card_flow", "safety_respect", "image_relevance", "image_composition",
        ), 5)
        model = AsyncMock()
        model.generate_json.return_value = {
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
            draft, {}, {}, {}, primary_web_source=primary_web_source,
        )))

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
