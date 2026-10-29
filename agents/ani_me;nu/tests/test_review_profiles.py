from pathlib import Path

import pytest

from ani_menu_agent.agents.reviewer.agent import (
    LANGUAGE_THRESHOLDS,
    _score_language,
)
from ani_menu_agent.models import ContentPackage, LanguageReview
from ani_menu_agent.policy import POLICY
from ani_menu_agent.quality import SCORE_THRESHOLDS, score_quality
from ani_menu_agent.review_rules import make_issue


@pytest.mark.parametrize("legacy_profile", [None, "exploratory", "integration"])
def test_single_policy_does_not_reenable_legacy_review_bypass(monkeypatch, legacy_profile) -> None:
    if legacy_profile is None:
        monkeypatch.delenv("ANI_MENU_REVIEW_PROFILE", raising=False)
    else:
        monkeypatch.setenv("ANI_MENU_REVIEW_PROFILE", legacy_profile)
    assert set(LANGUAGE_THRESHOLDS.values()) == {2}
    assert SCORE_THRESHOLDS == {
        "pass": 68,
        "card_minimum": 55,
        "safety_minimum_per_card": 4,
    }
    assert POLICY.quality_pass_score == SCORE_THRESHOLDS["pass"]
    assert POLICY.quality_card_minimum_score == SCORE_THRESHOLDS["card_minimum"]


@pytest.mark.parametrize("criterion", list(LANGUAGE_THRESHOLDS))
@pytest.mark.parametrize("value,expected_issue", [(1, True), (2, False)])
def test_language_floor_boundary(criterion, value, expected_issue) -> None:
    card = {name: 5 for name in LANGUAGE_THRESHOLDS}
    card.update(page=1, safety_respect=5, safety_blocker=False, safety_category=None)
    card[criterion] = value
    review = LanguageReview.model_validate(
        {"content_type_fit": 5, "cards": [card, {**card, "page": 2}]}
    )

    _score, safety_score, _card_scores, issues = _score_language(review)

    assert safety_score == 10
    assert (f"LANGUAGE_{criterion.upper()}" in {issue.code for issue in issues}) is expected_issue


@pytest.mark.parametrize(
    "code,decision",
    [
        ("FACT_UNVERIFIED", "return_to_crawler"),
        ("SOURCE_APPROVAL_REQUIRED", "return_to_crawler"),
        ("IMAGE_RIGHTS_REVIEW", "revise"),
        ("SAFETY_HARMFUL_CONTENT", "revise"),
        ("SAFETY_RESPECT_LOW", "revise"),
        ("IMAGE_RENDER_MISSING", "revise"),
        ("IMAGE_VISION_REVIEW_MISSING", "revise"),
        ("DETAIL_BODY_REVIEW_MISSING", "revise"),
    ],
)
def test_lower_editorial_floors_do_not_override_publication_blockers(code, decision) -> None:
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"
    package = ContentPackage.model_validate_json(sample.read_text(encoding="utf-8"))
    issue = make_issue(code=code)
    report = score_quality(
        package,
        [issue],
        language_score=20,
        safety_score=10,
        card_language_scores={card.page: 100 for card in package.cards},
    )
    assert code in report["hard_blockers"]
    assert report["decision"] == decision
