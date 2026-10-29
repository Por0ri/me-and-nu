from pathlib import Path

from ani_menu_agent.markdown_export import render_detail_markdown, render_markdown
from ani_menu_agent.models import ContentPackage

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "generation_input.json"


def test_detail_export_ends_with_sources_then_ai_disclosure() -> None:
    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))

    rendered = render_markdown(package)

    source_section = rendered.rfind("## 출처")
    disclosure = "이 콘텐츠는 원문과 확인된 자료를 바탕으로 AI를 활용해 생성·정리했습니다."
    disclosure_position = rendered.rfind(disclosure)
    assert source_section >= 0
    assert disclosure_position > source_section
    assert rendered.rstrip().endswith(disclosure)
    assert "ai_disclosure" not in package.model_dump()


def test_detail_article_is_followed_by_its_sources_and_disclosure() -> None:
    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    package.detail_body = "자세히 보기에서 읽을 수 있는 상세 설명입니다. " * 12
    package.detail_fact_ids = ["f1"]
    package.detail_source_ids = ["official_event"]

    rendered = render_detail_markdown(package)

    assert rendered.index(package.detail_body[:20]) < rendered.index("## 출처")
    assert rendered.index("## 출처") < rendered.index("https://example.com/official/event")
    assert rendered.rstrip().endswith(
        "이 콘텐츠는 원문과 확인된 자료를 바탕으로 AI를 활용해 생성·정리했습니다."
    )
    assert "ai_disclosure" not in package.model_dump()
