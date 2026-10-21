import json
from copy import deepcopy
from pathlib import Path

from ani_menu_agent.duplicate_detection import duplicate_reason
from ani_menu_agent.models import ContentPackage

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"


def test_repeated_source_url_is_blocked_even_with_tracking_query() -> None:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    payload["menu_work_ids"] = [37]
    package = ContentPackage.model_validate(payload)
    existing = package.model_dump(mode="json")
    existing["sources"][0]["url"] += "?utm_source=campaign"

    assert duplicate_reason(package, existing) == "same_source_url"


def test_publisher_date_filter_does_not_hide_same_article_url() -> None:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    package = ContentPackage.model_validate(payload)
    existing = package.model_dump(mode="json")
    existing["sources"][0]["url"] += "?pubDate=20261014"

    assert duplicate_reason(package, existing) == "same_source_url"


def test_same_work_core_facts_are_blocked_across_different_urls() -> None:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    payload["menu_work_ids"] = [37]
    payload["sources"][0]["title"] = "한정 행사 일정과 예매 안내"
    payload["facts"] = [
        {
            "id": "f1",
            "statement": "일본 도쿄 행사장은 2026년 11월 8일 공식 개장한다.",
            "source_ids": ["official_event"],
            "verified": True,
        },
        {
            "id": "f2",
            "statement": "한정 입장권은 온라인 예매로만 구매할 수 있다.",
            "source_ids": ["official_event"],
            "verified": True,
        },
    ]
    package = ContentPackage.model_validate(payload)
    existing = deepcopy(package.model_dump(mode="json"))
    existing["request_id"] = "another-page"
    existing["sources"][0]["url"] = "https://example.com/official/another-page"
    existing["sources"][0]["title"] = "행사장 개장 일정과 티켓 신청"

    assert duplicate_reason(package, existing) == "same_work_and_overlapping_core_facts"


def test_different_story_for_same_work_is_not_blocked() -> None:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    payload["menu_work_ids"] = [37]
    package = ContentPackage.model_validate(payload)
    existing = deepcopy(package.model_dump(mode="json"))
    existing["sources"][0]["url"] = "https://example.com/official/different-story"
    existing["sources"][0]["title"] = "새 극장판 제작 결정"
    existing["facts"] = [
        {
            "id": "f-new",
            "statement": "새로운 장편 극장판 제작을 공식 발표했다.",
            "source_ids": ["official_event"],
            "verified": True,
        }
    ]

    assert duplicate_reason(package, existing) is None
