from pathlib import Path

from ani_menu_agent.agents.reviewer.agent import _review_message_content
from ani_menu_agent.agents.reviewer.prompt import REVIEWER_PROMPT
from ani_menu_agent.models import ContentPackage


def test_reviewer_prompt_explains_expected_image_whitespace() -> None:
    assert "text_overlay_box" in REVIEWER_PROMPT
    assert "크롭·확대·이미지 이동을 요구하지 않는다" in REVIEWER_PROMPT


def test_first_review_text_part_explains_expected_image_whitespace() -> None:
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"
    package = ContentPackage.model_validate_json(sample.read_text(encoding="utf-8"))
    package.cards = []

    parts, attached_pages, missing_pages = _review_message_content(package)

    assert parts[0]["type"] == "text"
    assert "text_overlay_box 자리의 빈 공간" in parts[0]["text"]
    assert attached_pages == []
    assert missing_pages == []
