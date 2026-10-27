import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ani_menu_agent import nodes
from ani_menu_agent.agents.reviewer import agent as reviewer
from ani_menu_agent.models import LanguageReview

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"


@pytest.fixture(params=[None, "standard", "strict", "integration", "exploratory"])
def review_profile(request, monkeypatch):
    if request.param is None:
        monkeypatch.delenv("ANI_MENU_REVIEW_PROFILE", raising=False)
    else:
        monkeypatch.setenv("ANI_MENU_REVIEW_PROFILE", request.param)


@pytest.fixture
def package():
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    # Meet the retired integration mode's structural checks so they cannot hide
    # an approval bypass behind a missing work ID or image.
    payload["menu_work_ids"] = [1]
    return payload


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
                "visual_feedback": "Image matches the card.",
            }
            for card in package["cards"]
        ],
    }


@pytest.fixture
def persisted(monkeypatch):
    writes = []

    def persist(_package, status, report):
        writes.append((status, report))

    def unexpected_llm(**_kwargs):
        pytest.fail("This review must finish without an LLM request")

    monkeypatch.setattr(
        reviewer, "AgentRepository",
        lambda: SimpleNamespace(persist_generated_content=persist),
    )
    monkeypatch.setattr(reviewer, "ChatOpenAI", unexpected_llm)
    monkeypatch.setattr(
        reviewer, "get_settings", lambda: SimpleNamespace(openai_api_key=None),
    )
    return writes


def mock_language_review(monkeypatch, package, review_data):
    def invoke(_messages):
        return {"parsed": LanguageReview.model_validate(review_data), "raw": None}

    monkeypatch.setattr(
        reviewer, "ChatOpenAI",
        lambda **_kwargs: SimpleNamespace(
            with_structured_output=lambda *_args, **_kwargs: SimpleNamespace(invoke=invoke)
        ),
    )
    monkeypatch.setattr(
        reviewer, "get_settings",
        lambda: SimpleNamespace(openai_api_key="test-key", model_name="test-model"),
    )
    monkeypatch.setattr(
        reviewer, "_review_message_content",
        lambda _package: ([], [card["page"] for card in package["cards"]], []),
    )


def test_unverified_facts_cannot_reach_feed(review_profile, package, persisted):
    package["facts"][0]["verified"] = False

    result = reviewer.review_content({"package": package, "trace": []})

    assert result["status"] == "needs_crawler_update"
    assert "FACT_UNVERIFIED" in result["quality_report"]["hard_blockers"]
    assert nodes.route_review(result) == "crawler_update_required"
    assert [status for status, _ in persisted] == ["needs_crawler_update"]


def test_missing_api_key_keeps_valid_package_awaiting_review(
    review_profile, package, persisted,
):
    result = reviewer.review_content({"package": package, "trace": []})

    assert result["status"] == "review_api_required"
    assert result["editorial_decision"] == "review_required"
    assert nodes.route_review(result) == "end"
    assert [status for status, _ in persisted] == ["review_api_required"]


def test_passing_fixture_cannot_publish_or_write_content(
    review_profile, package, review_data, persisted,
):
    result = reviewer.review_content(
        {"package": package, "trace": [], "review_fixture": review_data}
    )

    assert result["status"] == "offline_review_only"
    assert result["editorial_decision"] == "offline_fixture_passed"
    assert nodes.route_review(result) == "end"
    assert persisted == []


@pytest.mark.parametrize("problem", ["safety", "language"])
def test_independent_review_rejection_cannot_reach_feed(
    review_profile, package, review_data, persisted, monkeypatch, problem,
):
    if problem == "safety":
        review_data["cards"][0].update(safety_blocker=True, safety_category="other")
        expected_code = "SAFETY_HARMFUL_CONTENT"
    else:
        review_data["cards"][0]["korean_naturalness"] = 1
        expected_code = "LANGUAGE_KOREAN_NATURALNESS"
    mock_language_review(monkeypatch, package, review_data)

    result = reviewer.review_content({"package": package, "trace": []})

    assert result["status"] == "review_returned"
    assert expected_code in {issue["code"] for issue in result["qa_report"]}
    assert nodes.route_review(result) == "revision_checkpoint"
    assert [status for status, _ in persisted] == ["review_returned"]


def test_successful_independent_review_still_reaches_feed(
    review_profile, package, review_data, persisted, monkeypatch,
):
    mock_language_review(monkeypatch, package, review_data)

    result = reviewer.review_content({"package": package, "trace": []})

    assert result["status"] == "review_passed"
    assert result["review_mode"] == "llm"
    assert nodes.route_review(result) == "feed_queue"
    assert [status for status, _ in persisted] == ["review_passed"]


def test_generation_preflight_requires_approved_sources(
    review_profile, package, monkeypatch,
):
    package["sources"][0].update(role="discovery", is_official=False)
    monkeypatch.setattr(nodes, "attach_menu_work_ids", lambda _package: None)

    result = nodes.rights_preflight(
        {"package": package, "trace": [], "allow_llm": True}
    )

    assert result["status"] == "needs_crawler_update"
    assert result["qa_report"][0]["code"] == "SOURCE_APPROVAL_REQUIRED"
    assert nodes.route_rights_preflight(result) == "crawler_update_required"
