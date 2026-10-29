"""Checking's publication gates remain intact with the expanded PR review policy."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from ani_menu_agent import nodes
from ani_menu_agent.agents.reviewer import agent as reviewer
from ani_menu_agent.layout_engine import build_cards
from ani_menu_agent.models import ContentPackage, CopyCard, LanguageReview
from ani_menu_agent.policy import POLICY
from ani_menu_agent.quality import run_quality_checks, score_quality


@pytest.fixture
def package():
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"
    package = ContentPackage.model_validate_json(sample.read_text(encoding="utf-8"))
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
            persist_generated_content=lambda _package, status, report: persisted.append(
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


def test_unverified_fact_returns_before_llm_and_cannot_publish(package, writes):
    package["facts"][0]["verified"] = False
    result = reviewer.review_content({"package": package, "trace": []})
    assert "FACT_UNVERIFIED" in result["quality_report"]["hard_blockers"]
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
