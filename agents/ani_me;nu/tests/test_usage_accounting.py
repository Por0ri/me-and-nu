import json
from pathlib import Path
from types import SimpleNamespace

from ani_menu_agent.agents.reviewer.agent import review_content
from ani_menu_agent.cli import _add_usage_summary
from ani_menu_agent.models import LanguageReview
from ani_menu_agent.policy import POLICY

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "generation_input.json"


def test_usage_summary_adds_all_llm_stages() -> None:
    result = {
        "usage_records": [
            {
                "stage": "fact_extraction",
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
                "estimated_cost_usd": 0.00001,
            },
            {
                "stage": "review",
                "input_tokens": 20,
                "output_tokens": 3,
                "total_tokens": 23,
                "estimated_cost_usd": 0.00002,
            },
        ]
    }

    _add_usage_summary(result)

    assert result["usage_summary"] == {
        "llm_calls": 2,
        "input_tokens": 30,
        "cached_input_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 5,
        "total_tokens": 35,
        "estimated_cost_usd": 0.00003,
        "per_content_budget_usd": POLICY.max_content_llm_cost_usd,
        "budget_exceeded": False,
    }


def test_reviewer_returns_its_usage_record(monkeypatch) -> None:
    package = json.loads(SAMPLE.read_text(encoding="utf-8"))
    review = LanguageReview(
        content_type_fit=5,
        content_type_feedback="행사 정보가 행사 유형과 일치합니다.",
        cards=[
            {
                "page": page,
                "korean_naturalness": 5,
                "translation_naturalness": 5,
                "reader_clarity": 5,
                "fan_engagement": 5,
                "card_flow": 5,
                "safety_respect": 5,
                "safety_blocker": False,
                "safety_category": None,
                "feedback": "자연스럽습니다.",
                "image_relevance": 5,
                "image_composition": 5,
                "visual_feedback": "이미지가 주제에 맞고 장면 자막이 없습니다.",
            }
            for page in range(1, 4)
        ],
    )

    class FakeStructuredModel:
        def invoke(self, _messages):
            return {
                "parsed": review,
                "raw": SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5}),
            }

    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return FakeStructuredModel()

    monkeypatch.setattr(
        "ani_menu_agent.agents.reviewer.agent.ChatOpenAI", lambda **_kwargs: FakeModel()
    )
    monkeypatch.setattr(
        "ani_menu_agent.agents.reviewer.agent.get_settings",
        lambda: SimpleNamespace(openai_api_key="test-key", model_name="test-model"),
    )
    monkeypatch.setattr(
        "ani_menu_agent.agents.reviewer.agent.run_quality_checks", lambda _package: []
    )
    monkeypatch.setattr(
        "ani_menu_agent.agents.reviewer.agent.score_quality",
        lambda *_args, **_kwargs: {"hard_blockers": [], "decision": "pass_candidate", "total": 100},
    )
    monkeypatch.setattr(
        "ani_menu_agent.agents.reviewer.agent.AgentRepository.persist_generated_content",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ani_menu_agent.agents.reviewer.agent.AgentRepository.get_image_bytes",
        lambda _self, _key: (b"test-image", "image/png"),
    )

    result = review_content(
        {
            "package": package,
            "trace": [],
            "usage_records": [{"stage": "copy_generation", "estimated_cost_usd": 0.1}],
        }
    )

    assert [record["stage"] for record in result["usage_records"]] == [
        "copy_generation",
        "review",
    ]
    assert result["usage_records"][-1]["input_tokens"] == 10
    assert result["usage_records"][-1]["estimated_cost_usd"] == 0.0000035


def test_usage_summary_marks_only_totals_above_the_package_budget() -> None:
    for total, exceeded in ((POLICY.max_content_llm_cost_usd, False),
                            (POLICY.max_content_llm_cost_usd + 0.00001, True)):
        records = [{"estimated_cost_usd": total}]
        result = {"usage_records": records}
        _add_usage_summary(result)
        assert result["usage_summary"]["budget_exceeded"] is exceeded
        assert result["usage_summary"]["per_content_budget_usd"] == POLICY.max_content_llm_cost_usd
        assert result["usage_records"] == records
