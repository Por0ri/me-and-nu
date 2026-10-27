from pathlib import Path

from ani_menu_agent.agents.reviewer.agent import (
    LANGUAGE_THRESHOLDS,
    _active_language_thresholds,
    _active_score_thresholds,
)
from ani_menu_agent.models import ContentPackage, QualityIssue
from ani_menu_agent.quality import SCORE_THRESHOLDS, score_quality


def test_exploratory_profile_is_opt_in_and_does_not_mutate_defaults(monkeypatch) -> None:
    monkeypatch.delenv("ANI_MENU_REVIEW_PROFILE", raising=False)
    assert _active_language_thresholds() == LANGUAGE_THRESHOLDS
    assert _active_score_thresholds() == SCORE_THRESHOLDS

    monkeypatch.setenv("ANI_MENU_REVIEW_PROFILE", "exploratory")
    assert set(_active_language_thresholds().values()) == {2}
    assert _active_score_thresholds() == {
        "pass": 68,
        "card_minimum": 55,
        "safety_minimum_per_card": SCORE_THRESHOLDS["safety_minimum_per_card"],
    }
    assert LANGUAGE_THRESHOLDS["korean_naturalness"] == 4
    assert SCORE_THRESHOLDS["pass"] == 85


def test_exploratory_profile_keeps_fact_errors_blocking() -> None:
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"
    package = ContentPackage.model_validate_json(sample.read_text(encoding="utf-8"))
    issue = QualityIssue(code="FACT_UNVERIFIED", message="Unverified claim")
    report = score_quality(
        package,
        [issue],
        language_score=20,
        safety_score=10,
        thresholds={"pass": 68, "card_minimum": 55, "safety_minimum_per_card": 4},
    )
    assert "FACT_UNVERIFIED" in report["hard_blockers"]
    assert report["decision"] == "return_to_crawler"



