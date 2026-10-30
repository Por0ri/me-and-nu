from pathlib import Path

from ani_menu_agent.agents.reviewer.agent import _review_message_content
from ani_menu_agent.agents.reviewer.prompt import REVIEWER_PROMPT
from ani_menu_agent.models import ContentPackage


def test_reviewer_prompt_explains_expected_image_whitespace() -> None:
    assert "text_overlay_box" in REVIEWER_PROMPT
    assert "원본 이미지는 잘라내지 않고 전체를 보여주는 규칙" in REVIEWER_PROMPT
    assert "원본 비율에서 생기는 여백은 정상" in REVIEWER_PROMPT
    assert "실제 잘림·깨짐·가림이 보일 때에만 이미지 교체" in REVIEWER_PROMPT
    assert "모든 카드에서 HTML 카피는 이미지 아래쪽" in REVIEWER_PROMPT


def test_first_review_text_part_explains_expected_image_whitespace() -> None:
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"
    package = ContentPackage.model_validate_json(sample.read_text(encoding="utf-8"))
    package.cards = []

    parts, attached_pages, missing_pages = _review_message_content(package)

    assert parts[0]["type"] == "text"
    assert "원본 비율 때문에 생긴 여백은 정상" in parts[0]["text"]
    assert "실제 저장 PNG에는 글자가 없고" in parts[0]["text"]
    assert "카피를 이미지 원본에 포함된 글자로 오인하지 말고" in parts[0]["text"]
    assert attached_pages == []
    assert missing_pages == []
