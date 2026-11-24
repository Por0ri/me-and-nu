"""Checking's publication gates remain intact with the expanded PR review policy."""

import json
from pathlib import Path
from types import SimpleNamespace
from datetime import date

import pytest

from ani_menu_agent import generation, nodes
from ani_menu_agent.agents.content_generation import detail_writer
from ani_menu_agent.agents.reviewer import agent as reviewer
from ani_menu_agent.layout_engine import build_cards
from ani_menu_agent.models import ContentPackage, CopyCard, LanguageReview
from ani_menu_agent.policy import POLICY
from ani_menu_agent.period_copy import reported_terms
from ani_menu_agent.quality import run_quality_checks, score_quality
from ani_menu_agent.storage import AgentRepository


@pytest.fixture
def package():
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"
    package = ContentPackage.model_validate_json(sample.read_text(encoding="utf-8"))
    for source in package.sources:
        source.published_at = date.today().isoformat()
    package.sources[0].title = "진격의 거인 공식 행사 공지"
    package.menu_work_ids = [1]
    copies = [
        CopyCard.model_validate(card.model_dump(exclude={"layout"})) for card in package.cards
    ]
    package.cards = build_cards(copies, package.images)
    package.detail_body = " ".join(fact.statement for fact in package.facts) * 5
    package.detail_fact_ids = [fact.id for fact in package.facts]
    package.detail_source_ids = [source.id for source in package.sources]
    for card in package.cards:
        card.rendered_image_key = f"test-render-{card.page}"
    # Use the actual deterministic checks: unrelated fixture defects must not
    # accidentally make a publication-gate test pass before reaching its target.
    assert score_quality(package, run_quality_checks(package))["hard_blockers"] == []
    return package.model_dump(mode="json")


@pytest.fixture
def review_data(package):
    return {
        "content_type_fit": 5,
        "cards": [
            {
                "page": card["page"],
                "korean_naturalness": 5,
                "translation_naturalness": 5,
                "reader_clarity": 5,
                "fan_engagement": 5,
                "card_flow": 5,
                "safety_respect": 5,
                "safety_blocker": False,
                "safety_category": None,
                "image_relevance": 5,
                "image_composition": 5,
                "visual_feedback": "Synthetic image matches the cited fact.",
            }
            for card in package["cards"]
        ],
        "detail_body_review": {
            "naturalness": 5, "factual_support": 5, "coherence": 5, "completeness": 5,
        },
    }


@pytest.fixture
def writes(monkeypatch):
    persisted = []

    def unexpected_llm(**_kwargs):
        pytest.fail("This gate must finish before any LLM call")

    monkeypatch.setattr(
        reviewer, "AgentRepository",
        lambda: SimpleNamespace(
            persist_generated_content=lambda _package, status, report, **_kwargs: persisted.append(
                (status, report)
            ),
        ),
    )
    monkeypatch.setattr(reviewer, "ChatOpenAI", unexpected_llm)
    monkeypatch.setattr(reviewer, "get_settings", lambda: SimpleNamespace(openai_api_key=None))
    return persisted


def mock_independent_review(monkeypatch, package, data):
    def invoke(_messages):
        return {
            "parsed": LanguageReview.model_validate(data) if data is not None else None,
            "raw": SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 50}),
        }

    monkeypatch.setattr(
        reviewer, "ChatOpenAI",
        lambda **_kwargs: SimpleNamespace(
            with_structured_output=lambda *_args, **_kwargs: SimpleNamespace(invoke=invoke)
        ),
    )
    monkeypatch.setattr(
        reviewer, "get_settings",
        lambda: SimpleNamespace(openai_api_key="synthetic-test-key", model_name="test-model"),
    )
    monkeypatch.setattr(
        reviewer, "_review_message_content",
        lambda _package: ([], [card["page"] for card in package["cards"]], []),
    )


@pytest.mark.parametrize("legacy_profile", [None, "exploratory", "integration"])
def test_fixture_approval_never_writes_or_publishes(
    monkeypatch, package, review_data, writes, legacy_profile,
):
    if legacy_profile:
        monkeypatch.setenv("ANI_MENU_REVIEW_PROFILE", legacy_profile)
    result = reviewer.review_content(
        {"package": package, "trace": [], "review_fixture": review_data}
    )
    assert result["status"] == "offline_review_only"
    assert result["editorial_decision"] == "offline_fixture_passed"
    assert nodes.route_review(result) == "end"
    assert writes == []


def test_minimal_review_returns_published_schedule_capture_notes_to_both_writers(package, writes):
    # Reader-facing passages from the previously approved AnimeSchedule draft for #667.
    package["cards"][0].update(title_lines=["3기 소식,", "지금 확정해도 될까?"],
                               body_sentences=["현재 자료만으로는 확정된 소식을 전하기 어려워."])
    package["cards"][1].update(title_lines=["기다림은 잠시,", "근거부터 더 탄탄하게"],
                               body_sentences=["공식 발표나 신뢰할 만한 최신 정보가 확인되면 제대로 전할게."])
    package["detail_body"] = ("지금 확인되는 건 애니 목록 페이지 캡처에 3기 제목과 ‘Ongoing’ 표시가 실려 있다는 정도야. "
                              "자료 자체가 검증되지 않은 캡처라, 이걸 공식 확정 소식으로 받아들이기엔 아직 근거가 부족해.")
    result = reviewer.review_content({"package": package, "minimal_review": True})
    assert result["status"] == "review_returned"
    assert set(result["revision_owners"]) == {"copy_writer", "detail_writer"}
    assert {(i["page"], i["owner"]) for i in result["qa_report"]} == {
        (1, "copy_writer"), (2, "copy_writer"), (None, "detail_writer"),
    }
    assert all(i["code"] == "EDITORIAL_NOTE_IN_READER_COPY" for i in result["qa_report"])
    assert nodes.route_review(result) == "revision_checkpoint"
    assert nodes.route_review({**result, "review_failures": POLICY.max_review_failures}) == "archive_rejected"
    assert writes[-1][0] == "review_returned"


@pytest.fixture
def reported_goods(package):
    package["content_type"] = "goods"
    package["facts"].append({
        "id": "sales-period", "fact_type": "sales_period", "verified": False,
        "statement": "판매 종료일: 2026-12-17", "source_ids": [package["sources"][0]["id"]],
        "details": {"sale_end": "2026-12-17", "source_quote":
            "販売期間：2026年11月12日(木)17:00～2026年12月17日(木)16:59\n"
            "販売価格：1回 770円(税込)※別途発送手数料がかかります。"},
    })
    return package


def test_reported_sale_terms_returned_and_saved_with_actionable_evidence(monkeypatch, tmp_path, reported_goods, writes):
    repo = AgentRepository(path=tmp_path / "review.sqlite3")
    monkeypatch.setattr(reviewer, "AgentRepository", lambda: repo)
    reported_goods["detail_body"] = "다만 판매 기간과 진행 여부는 여기서 단정하지 않을게."
    reported_goods["cards"][1]["body_sentences"] = ["가격은 아직 공개되지 않았어."]
    result = reviewer.review_content({"package": reported_goods, "minimal_review": True})
    assert nodes.route_review(result) == "revision_checkpoint"
    messages = {issue["owner"]: issue["message"] for issue in result["qa_report"]}
    assert "1回 770円(税込)" in messages["copy_writer"]
    assert "11月12日(木)17:00～2026年12月17日(木)16:59" in messages["detail_writer"]
    assert all("[sales-period]" in message for message in messages.values())
    with repo.connect() as conn:
        row = conn.execute("SELECT status, quality_score FROM content_drafts WHERE request_id=?",
                           (reported_goods["request_id"],)).fetchone()
        assert tuple(row) == ("review_returned", result["quality_report"]["total"])
    # The full reviewer enforces the same contradiction even with a perfect language score.
    full_review = reviewer.review_content({"package": reported_goods, "review_fixture": {}})
    assert {"COPY_REPORTED_TERMS_DENIED", "DETAIL_REPORTED_TERMS_DENIED"}.issubset(
        full_review["quality_report"]["hard_blockers"]
    )


def test_reported_period_allows_missing_other_terms(monkeypatch, tmp_path, reported_goods, writes):
    repo = AgentRepository(path=tmp_path / "pending.sqlite3")
    monkeypatch.setattr(reviewer, "AgentRepository", lambda: repo)
    sale = reported_goods["facts"][-1]
    sale["details"]["source_quote"] = "販売期間：11月12日～12月17日。販売価格：未定、送料770円"
    reported_goods["detail_body"] = "판매 기간은 11월 12일부터 12월 17일까지야. 가격은 미정이야. 재고도 아직 알 수 없어."
    terms = reported_terms(ContentPackage.model_validate(reported_goods))
    assert len(terms) == 1 and terms[0]["term"] == "sale_period" and terms[0]["ends_on"] == "2026-12-17"
    result = reviewer.review_content({"package": reported_goods, "minimal_review": True})
    assert result["status"] == "review_api_required" and result["qa_report"] == []
    with repo.connect() as conn:
        row = conn.execute("SELECT status, quality_score FROM content_drafts WHERE request_id=?",
                           (reported_goods["request_id"],)).fetchone()
        assert tuple(row) == ("review_api_required", result["quality_report"]["total"])
    sale["source_ids"] = ["different-source"]
    assert reported_terms(ContentPackage.model_validate(reported_goods)) == []


@pytest.mark.parametrize("module", [generation, detail_writer])
def test_both_writer_requests_include_the_reported_terms_without_paid_calls(monkeypatch, reported_goods, module):
    submitted = []

    def inspect(messages):
        submitted.append(json.loads(messages[-1][1].rsplit("\n", 1)[-1]))
        raise RuntimeError("request captured")

    model = SimpleNamespace(with_structured_output=lambda *args, **kwargs: SimpleNamespace(invoke=inspect))
    monkeypatch.setattr(module, "ChatOpenAI", lambda **kwargs: model)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(openai_api_key="fake", model_name="fake"))
    monkeypatch.setattr(module, "load_work_catalog", lambda: [])
    with pytest.raises(RuntimeError, match="request captured"):
        if module is detail_writer:
            reported_goods["detail_body"] = ""
            detail_writer.write_detail_body({"package": reported_goods, "allow_llm": True})
        else:
            repo = SimpleNamespace(prior_cover_image_fingerprints=lambda **_: [], recent_copy_examples=lambda **_: [],
                                   active_content_types=lambda: [{"code": "goods"}])
            monkeypatch.setattr(generation, "AgentRepository", lambda: repo)
            monkeypatch.setattr(generation, "attach_menu_work_ids", lambda package: None)
            monkeypatch.setattr(generation, "supported_content_types_for_sources", lambda ids: {"goods"})
            generation.generate_copy_deck(ContentPackage.model_validate(reported_goods), requested_content_type="goods")
    schedule, price = submitted[0]["reported_terms"]
    assert schedule["ends_on"] == "2026-12-17" and "17:00～2026年12月17日(木)16:59" in schedule["excerpt"]
    assert price["term"] == "price" and "1回 770円(税込)" in price["excerpt"]


@pytest.mark.parametrize("detail_note", [True, False])
def test_minimal_review_checks_detail_even_with_full_card_scores(
    monkeypatch, package, writes, review_data, detail_note,
):
    package["detail_body"] = (
        "이번에 가져온 건 이것뿐이야. 읽을거리는 자료가 생기면 그때 써 올게."
        if detail_note else "팝업은 11월 12일에 열려. 입장료는 아직 발표 전이니 방문 전에 원문을 확인해봐!"
    )
    submitted = []

    def structured(schema, **_kwargs):
        def invoke(messages):
            submitted.append(messages[-1][1])
            parsed = schema.model_validate({**review_data, "internal_note_pages": [],
                                            "detail_is_internal_note": detail_note})
            return {"parsed": parsed, "raw": None}
        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(reviewer, "ChatOpenAI", lambda **_: SimpleNamespace(with_structured_output=structured))
    monkeypatch.setattr(reviewer, "get_settings", lambda: SimpleNamespace(openai_api_key="test", model_name="test"))
    result = reviewer.review_content({"package": package, "minimal_review": True})
    assert package["detail_body"] in submitted[0]
    assert result["quality_score"] == 100
    assert nodes.route_review(result) == ("revision_checkpoint" if detail_note else "feed_queue")
    assert result["revision_owners"] == (["detail_writer"] if detail_note else [])


def test_missing_key_waits_for_independent_review(package, writes):
    result = reviewer.review_content({"package": package, "trace": []})
    assert result["status"] == "review_api_required"
    assert result["editorial_decision"] == "review_required"
    assert nodes.route_review(result) == "end"
    assert [status for status, _report in writes] == ["review_api_required"]


def test_incomplete_fixture_review_still_cannot_write_or_enter_live_routes(
    package, review_data, writes,
):
    review_data["cards"].pop()
    result = reviewer.review_content(
        {"package": package, "trace": [], "review_fixture": review_data}
    )
    assert "REVIEW_CARD_SET_MISMATCH" in result["quality_report"]["hard_blockers"]
    assert result["status"] == "offline_review_only"
    assert result["review_mode"] == "offline_fixture"
    assert nodes.route_review(result) == "end"
    assert writes == []


def test_unverified_fact_alone_still_requires_independent_review(package, writes):
    package["facts"][0]["verified"] = False
    result = reviewer.review_content({"package": package, "trace": []})
    # Checking records verification confidence for the independent reviewer;
    # the flag alone no longer sends an otherwise sourced draft to the crawler.
    assert "FACT_UNVERIFIED" not in result["quality_report"]["hard_blockers"]
    assert result["status"] == "review_api_required"
    assert result["editorial_decision"] == "review_required"
    assert nodes.route_review(result) == "end"
    assert [status for status, _report in writes] == ["review_api_required"]


def test_missing_fact_source_returns_before_llm_and_cannot_publish(package, writes):
    package["facts"][0]["source_ids"] = ["missing-source"]
    result = reviewer.review_content({"package": package, "trace": []})
    assert "FACT_SOURCE_MISSING" in result["quality_report"]["hard_blockers"]
    assert result["status"] == "needs_crawler_update"
    assert nodes.route_review(result) == "crawler_update_required"
    assert [status for status, _report in writes] == ["needs_crawler_update"]


@pytest.mark.parametrize(
    "defect,expected_code",
    [
        ("safety", "SAFETY_HARMFUL_CONTENT"),
        ("card_set", "REVIEW_CARD_SET_MISMATCH"),
        ("visual_fields", "IMAGE_VISION_REVIEW_MISSING"),
        ("render", "IMAGE_RENDER_MISSING"),
        ("detail", "DETAIL_BODY_REVIEW_MISSING"),
        ("invalid_output", "REVIEW_OUTPUT_INVALID"),
    ],
)
def test_incomplete_or_rejected_review_cannot_publish(
    monkeypatch, package, review_data, writes, defect, expected_code,
):
    if defect == "safety":
        review_data["cards"][0].update(safety_blocker=True, safety_category="other")
    elif defect == "card_set":
        review_data["cards"].pop()
    elif defect == "visual_fields":
        review_data["cards"][0].pop("image_relevance")
    elif defect == "detail":
        review_data["detail_body_review"] = None
    mock_independent_review(
        monkeypatch, package, None if defect == "invalid_output" else review_data,
    )
    if defect == "render":
        monkeypatch.setattr(
            reviewer, "_review_message_content",
            lambda _package: ([], [2, 3, 4], [1]),
        )

    result = reviewer.review_content({"package": package, "trace": []})

    assert expected_code in result["quality_report"]["hard_blockers"]
    assert nodes.route_review(result) in {"revision_checkpoint", "crawler_update_required"}
    assert result["editorial_decision"] != "approved_for_feed"
    assert all(status not in {"review_passed", "ready_for_feed"} for status, _report in writes)


def test_complete_independent_review_reaches_feed_with_usage_record(
    monkeypatch, package, review_data, writes,
):
    mock_independent_review(monkeypatch, package, review_data)
    result = reviewer.review_content({"package": package, "trace": []})
    assert result["status"] == "review_passed"
    assert result["review_mode"] == "llm"
    assert nodes.route_review(result) == "feed_queue"
    assert result["usage_records"][0]["stage"] == "review"
    assert result["usage_records"][0]["total_tokens"] == 150
    assert [status for status, _report in writes] == ["review_passed"]


@pytest.mark.parametrize(
    "decision,cost,expected_route",
    [
        ("approved_for_feed", 0.04, "feed_queue"),
        ("approved_for_feed", 0.0401, "archive_rejected"),
        ("revise", 0.03, "revision_checkpoint"),
        ("revise", 0.0301, "archive_rejected"),
    ],
)
def test_publication_and_retry_respect_new_cost_budget(decision, cost, expected_route):
    assert POLICY.max_content_llm_cost_usd == 0.04
    assert POLICY.revision_cost_reserve_usd == 0.01
    state = {
        "review_mode": "llm", "editorial_decision": decision,
        "usage_records": [{"estimated_cost_usd": cost}],
    }
    assert nodes.route_review(state) == expected_route
