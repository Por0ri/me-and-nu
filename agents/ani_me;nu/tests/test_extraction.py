import json
from pathlib import Path
from types import SimpleNamespace

from ani_menu_agent.extraction import (
    FactCandidate,
    PageFactExtraction,
    _date_in_quote,
    _evidence_backed_facts,
    _verified_event_dates,
    extract_page_capture,
)


def test_japanese_date_range_inherits_year_for_end_date() -> None:
    assert _date_in_quote("開催期間：2026年9月25日（金）11:00～10月23日（金）10:59") == [
        "2026-09-25",
        "2026-10-23",
    ]


def test_only_facts_with_verbatim_source_evidence_are_verified() -> None:
    extracted = PageFactExtraction(
        topic="프리렌 공식 카페",
        facts=[
            FactCandidate(
                statement="행사는 11월 9일까지 열린다.",
                evidence_quote="開催期間2026年10月10日(土)～2026年11月09日(月)",
            ),
            FactCandidate(
                statement="서울에서도 열린다.",
                evidence_quote="東京と大阪で開催",
            ),
        ],
    )
    facts = _evidence_backed_facts(
        extracted,
        "official_source",
        "開催期間2026年10月10日(土)～2026年11月09日(月)",
        "trial",
    )
    assert len(facts) == 1
    assert facts[0].verified is True
    assert facts[0].details["source_quote"] == extracted.facts[0].evidence_quote


def test_event_dates_require_matching_verbatim_date_evidence() -> None:
    assert _date_in_quote("開催期間2026年10月10日(土)～2026年11月09日(月)") == [
        "2026-10-10",
        "2026-11-09",
    ]
    extracted = PageFactExtraction(
        topic="행사",
        event_end="2026-11-09",
        event_end_evidence_quote="期間は2026年11月8日まで",
        facts=[
            FactCandidate(
                statement="행사가 열린다.",
                evidence_quote="イベントを開催",
            )
        ],
    )
    assert _verified_event_dates(extracted) == (None, None)
    assert _evidence_backed_facts(
        extracted,
        "source",
        "イベントを開催",
        "trial",
    )


def test_capture_extraction_builds_package_without_live_api(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_quote = "開催期間2026年10月10日(土)～2026年11月09日(月)"
    capture = {
        "source_id": "orb_cafe_official",
        "title": "葬送のフリーレン x ORBcafe",
        "url": "https://orb-cafe.com/info/1420/",
        "fetched_at": "2026-10-12T00:00:00+00:00",
        "text": f"{source_quote}\nORBcafe 渋谷公園通り店 ／ ORBcafe 大阪北堀江店",
        "images": [
            {
                "id": "official-image-1",
                "url": "https://orb-cafe.com/wp-content/uploads/2026/09/frieren.webp",
                "alt_text": "프리렌 키 비주얼",
            }
        ],
    }
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    extracted = PageFactExtraction(
        topic="프리렌 공식 콜라보 카페",
        term_glossary=[
            {
                "original": "葬送のフリーレン",
                "korean": "장송의 프리렌",
                "category": "work_title",
            }
        ],
        event_start="2026-10-10",
        event_start_evidence_quote=source_quote,
        event_end="2026-11-09",
        event_end_evidence_quote=source_quote,
        facts=[FactCandidate(statement="행사는 11월 9일까지 열린다.", evidence_quote=source_quote)],
    )

    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return self

        def invoke(self, _prompt):
            return {
                "parsed": extracted,
                "raw": SimpleNamespace(
                    usage_metadata={
                        "input_tokens": 20,
                        "output_tokens": 10,
                        "input_token_details": {"cache_write": 3},
                    }
                ),
            }

    monkeypatch.setattr("ani_menu_agent.extraction.ChatOpenAI", lambda **_kwargs: FakeModel())
    monkeypatch.setattr(
        "ani_menu_agent.extraction.get_settings",
        lambda: SimpleNamespace(
            openai_api_key="test-key",
            model_name="test-model",
            region_policy="kr_first",
        ),
    )

    package, usage = extract_page_capture(path)
    assert package.event_start == "2026-10-10"
    assert package.event_end == "2026-11-09"
    assert package.facts[0].verified is True
    assert package.images[0].official is True
    assert package.images[0].source_description == "프리렌 키 비주얼"
    assert package.term_glossary[0].korean == "장송의 프리렌"
    assert package.images[0].related_fact_ids == []
    assert usage["model"] == "test-model"
    assert usage["stage"] == "fact_extraction"
    assert usage["input_tokens"] == 20
    assert usage["cache_write_tokens"] == 3
    assert usage["output_tokens"] == 10
    assert usage["estimated_cost_usd"] == 0.000007075
    assert usage["elapsed_seconds"] >= 0
