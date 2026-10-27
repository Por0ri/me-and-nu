import json
from pathlib import Path
from types import SimpleNamespace

from ani_menu_agent.extraction import (
    FactCandidate,
    PageFactExtraction,
    _date_in_quote,
    _evidence_backed_facts,
    _infer_event_location_from_facts,
    _verified_event_dates,
    _verified_event_dates_from_facts,
    extract_page_capture,
)
from ani_menu_agent.models import Fact
from ani_menu_agent.sources.models import Source, SourceRegistry


def test_fact_type_schema_discourages_unclassified_general_facts() -> None:
    description = FactCandidate.model_fields["fact_type"].description or ""

    assert "Use general only when" in description
    assert "event_schedule" in description


def test_japanese_date_range_inherits_year_for_end_date() -> None:
    assert _date_in_quote("開催期間：2026年9月25日（金）11:00～10月23日（金）10:59") == [
        "2026-09-25",
        "2026-10-23",
    ]


def test_event_date_parser_handles_korean_and_abbreviated_range_endpoints() -> None:
    assert _date_in_quote("행사 기간: 2026년 10월 23~27일") == [
        "2026-10-23",
        "2026-10-27",
    ]
    assert _date_in_quote("期間：2026/10/23～27日") == [
        "2026-10-23",
        "2026-10-27",
    ]


def test_event_date_parser_uses_source_year_when_quote_omits_it() -> None:
    assert _date_in_quote("開催日：10月17日（土）", default_year=2026) == ["2026-10-17"]


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


def test_single_day_event_date_is_stored_as_both_period_endpoints() -> None:
    extracted = PageFactExtraction(
        topic="하루 행사",
        event_start="2026-10-20",
        event_start_evidence_quote="開催日：2026年10月20日",
        facts=[
            FactCandidate(
                statement="10월 20일 하루 개최된다.",
                evidence_quote="開催日：2026年10月20日",
                fact_type="event_date",
            )
        ],
    )

    assert _verified_event_dates(extracted) == ("2026-10-20", "2026-10-20")


def test_single_day_event_date_can_use_source_publication_year() -> None:
    extracted = PageFactExtraction(
        topic="上映イベント",
        event_start="2026-10-17",
        event_start_evidence_quote="開催日：10月17日（土）",
        facts=[
            FactCandidate(
                statement="上映イベントは10月17日に開かれる。",
                evidence_quote="開催日：10月17日（土）",
                fact_type="event_schedule",
            )
        ],
    )

    assert _verified_event_dates(extracted, default_year=2026) == (
        "2026-10-17",
        "2026-10-17",
    )
    assert _verified_event_dates_from_facts(
        _evidence_backed_facts(
            extracted,
            "official",
            "開催日：10月17日（土）",
            "date-context",
        ),
        default_year=2026,
    ) == ("2026-10-17", "2026-10-17")


def test_single_day_goods_release_is_stored_as_both_period_endpoints() -> None:
    fact = Fact(
        id="release",
        statement="상품은 10월 20일 발매된다.",
        source_ids=["official"],
        verified=True,
        fact_type="product_release",
        details={"source_quote": "発売日：2026年10月20日"},
    )

    assert _verified_event_dates_from_facts([fact]) == ("2026-10-20", "2026-10-20")


def test_page_fact_schema_tolerates_long_official_lists() -> None:
    facts = [
        FactCandidate(statement=f"공식 목록 항목 {index}", evidence_quote="공식 목록")
        for index in range(21)
    ]

    assert len(PageFactExtraction(topic="공식 목록", facts=facts).facts) == 21


def test_fact_details_accept_numeric_price_lists() -> None:
    fact = FactCandidate(
        statement="Price range is 550 to 1980 yen.",
        evidence_quote="Price: 550–1,980 yen",
        details={"prices_yen": [550, 1980]},
    )

    assert fact.details["prices_yen"] == [550, 1980]


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
                "source_page_title": "Frieren Collaboration Cafe Goods",
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
        event_location="渋谷公園通り店",
        event_country="Japan",
        event_location_evidence_quote="ORBcafe 渋谷公園通り店",
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
    monkeypatch.setattr(
        "ani_menu_agent.extraction.load_source_registry",
        lambda: SourceRegistry(
            version=1,
            sources=[
                Source(
                    id="orb_cafe_official",
                    name="Test official source",
                    url="https://orb-cafe.com/",
                    category="official_event",
                    role="primary",
                    collection="html",
                    status="active",
                    region="JP",
                )
            ],
        ),
    )

    package, usage = extract_page_capture(path)
    assert package.event_start == "2026-10-10"
    assert package.event_end == "2026-11-09"
    assert package.event_location == "渋谷公園通り店"
    assert package.event_country == "JP"
    assert package.event_region == "overseas"
    assert package.facts[0].verified is True
    assert package.images[0].official is True
    assert package.images[0].source_description == (
        "프리렌 키 비주얼 Frieren Collaboration Cafe Goods"
    )
    assert package.term_glossary[0].korean == "장송의 프리렌"
    assert package.images[0].related_fact_ids == []
    assert usage["model"] == "test-model"
    assert usage["stage"] == "fact_extraction"
    assert usage["input_tokens"] == 20
    assert usage["cache_write_tokens"] == 3
    assert usage["output_tokens"] == 10
    assert usage["estimated_cost_usd"] == 0.000007075
    assert usage["elapsed_seconds"] >= 0


def test_event_term_category_is_valid() -> None:
    extracted = PageFactExtraction(
        topic="공식 행사",
        facts=[{"statement": "행사 일정이 공개됐다.", "evidence_quote": "공식 발표"}],
        term_glossary=[
            {
                "original": "ジャンプフェスタ",
                "korean": "점프 페스타",
                "category": "event",
            }
        ],
    )

    assert extracted.term_glossary[0].category == "event"


def test_nested_source_details_preserve_cast_name_and_role() -> None:
    cast = [{"name": "우에다 레이나", "role": "메토데"}]
    candidate = FactCandidate(
        statement="우에다 레이나가 메토데 역을 맡았다.",
        evidence_quote="メトーデ役は上田麗奈。",
        fact_type="cast",
        details={"cast": {"list": cast}},
    )
    fact = Fact(
        id="cast-1",
        statement=candidate.statement,
        source_ids=["frieren_official"],
        verified=True,
        fact_type=candidate.fact_type,
        details=candidate.details,
    )

    assert fact.details["cast"]["list"][0] == {"name": "우에다 레이나", "role": "메토데"}


def test_japanese_city_evidence_fills_overseas_event_metadata() -> None:
    facts = [
        Fact(
            id="venue-1",
            statement="시부야 PARCO에서 진행한다.",
            source_ids=["bleach_official"],
            verified=True,
            fact_type="event_schedule",
            details={"location": "도쿄 시부야 PARCO 6층"},
        ),
        Fact(
            id="venue-2",
            statement="신사이바시 PARCO에서도 진행한다.",
            source_ids=["bleach_official"],
            verified=True,
            fact_type="event_schedule",
            details={"location": "오사카 신사이바시 PARCO 6층"},
        ),
    ]

    location, country, region = _infer_event_location_from_facts(
        facts, "東京・渋谷PARCOと大阪・心斎橋PARCOにて開催。"
    )

    assert location == "도쿄 시부야 PARCO 6층 / 오사카 신사이바시 PARCO 6층"
    assert country == "Japan"
    assert region == "overseas"


def test_quote_verified_schedule_facts_supply_missing_period_endpoints() -> None:
    facts = [
        Fact(
            id="venue-period",
            statement="나고야점은 11월 20일부터 12월 20일까지 운영한다.",
            source_ids=["bleach_official"],
            verified=True,
            fact_type="event_schedule",
            details={
                "start": "2026-11-20",
                "end": "2026-12-20",
                "source_quote": "名古屋PARCO店 2026年11月20日(金)～12月20日(火)",
            },
        )
    ]

    assert _verified_event_dates_from_facts(facts) == ("2026-11-20", "2026-12-20")


def test_single_installation_date_is_not_extended_by_rain_delay_dates() -> None:
    facts = [
        Fact(
            id="installation-day",
            statement="설치는 2026년 10월 21일 시작 예정이다.",
            source_ids=["official"],
            verified=True,
            time_sensitive=True,
            fact_type="event_date",
            details={"source_quote": "設置開始日 2026年10月21日(水)(予定)"},
        ),
        Fact(
            id="rain-delay",
            statement="비가 오면 10월 26일 또는 28일에 설치한다.",
            source_ids=["official"],
            verified=True,
            time_sensitive=True,
            fact_type="event_schedule",
            details={"source_quote": "※雨天の場合は、10月26日(月)または28日(水)に設置"},
        ),
    ]

    assert _verified_event_dates_from_facts(facts, default_year=2026) == (
        "2026-10-21",
        "2026-10-21",
    )


def test_schedule_quote_dates_are_recovered_without_structured_date_fields() -> None:
    fact = Fact(
        id="schedule-only-quote",
        statement="공식 온라인 행사가 열린다.",
        source_ids=["official"],
        verified=True,
        fact_type="event_schedule",
        details={"source_quote": "2026年10月31日(土) 19:00 開催"},
    )

    assert _verified_event_dates_from_facts([fact]) == ("2026-10-31", "2026-10-31")


def test_quote_backed_los_angeles_venue_is_classified_as_overseas() -> None:
    fact = Fact(
        id="la-location",
        statement="Westfield Century City 2층, 로스앤젤레스",
        source_ids=["one_piece_official"],
        verified=True,
        fact_type="location",
        details={
            "source_quote": (
                "Westfield Century City 2F(10250 Santa Monica Blvd. Los Angeles CA 90067)"
            )
        },
    )

    location, country, region = _infer_event_location_from_facts(
        [fact], fact.details["source_quote"]
    )

    assert location == fact.statement
    assert country == "United States"
    assert region == "overseas"


def test_event_location_fact_type_is_used_for_country_inference() -> None:
    quote = "東京・大阪・愛知・北海道で期間限定オープン"
    fact = Fact(
        id="mha-event-location",
        statement="도쿄·오사카·아이치·홋카이도에서 기간 한정 운영한다.",
        source_ids=["my_hero_academia_official"],
        verified=True,
        fact_type="event_location",
        details={"source_quote": quote},
    )

    location, country, region = _infer_event_location_from_facts([fact], quote)

    assert location == fact.statement
    assert country == "Japan"
    assert region == "overseas"


def test_nationwide_animate_event_quote_is_classified_as_japan() -> None:
    fact = Fact(
        id="animate-nationwide-location",
        statement="전국 애니메이트 매장과 애니메이트 통신판매에서 진행된다.",
        source_ids=["frieren_official"],
        verified=True,
        fact_type="event_location",
        details={"source_quote": "【開催店舗】全国アニメイト、アニメイト通販"},
    )

    location, country, region = _infer_event_location_from_facts(
        [fact], fact.details["source_quote"]
    )

    assert location == fact.statement
    assert country == "Japan"
    assert region == "overseas"


def test_quote_backed_umeda_venue_is_classified_as_japan() -> None:
    fact = Fact(
        id="umeda-sky-building-location",
        statement="행사 장소는 우메다 스카이 빌딩 공중정원 전망대다.",
        source_ids=["my_hero_academia_official"],
        verified=True,
        fact_type="event_location",
        details={"source_quote": "梅田スカイビル・空中庭園展望台"},
    )

    location, country, region = _infer_event_location_from_facts(
        [fact], fact.details["source_quote"]
    )

    assert location == fact.statement
    assert country == "Japan"
    assert region == "overseas"


def test_quote_backed_busan_venue_is_classified_as_domestic() -> None:
    fact = Fact(
        id="busan-location",
        statement="롯데백화점 부산본점 지하 1층",
        source_ids=["one_piece_official"],
        verified=True,
        fact_type="location",
        details={
            "source_quote": (
                "Lotte Department Store Busan Main Branch, Busanjin-gu, Busan, Republic of Korea"
            )
        },
    )

    location, country, region = _infer_event_location_from_facts(
        [fact], fact.details["source_quote"]
    )

    assert location == fact.statement
    assert country == "대한민국"
    assert region == "domestic"
