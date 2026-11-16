"""A documented preorder start is distinct from simply displaying a publication date."""

import json
from pathlib import Path

import pytest

from ani_menu_agent.models import ContentPackage, Fact
from ani_menu_agent.quality import check_sources


@pytest.fixture
def package():
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample_content.json"
    result = ContentPackage.model_validate(json.loads(sample.read_text(encoding="utf-8")))
    result.content_type = "goods"
    result.event_start = None
    result.event_end = None
    result.sources = result.sources[:1]
    result.sources[0].published_at = "2026-11-13"
    result.facts = [Fact(
        id="preorder-start", statement="2026년 11월 13일부터 온라인 예약 접수를 시작했다.",
        source_ids=[result.sources[0].id], verified=True, time_sensitive=True,
        fact_type="product_sales", details={
            "preorder_start": "2026-11-13",
            "source_quote": "グッズは本日2026年11月13日(金)より、アニメイトやムービック通販などのオンラインで予約受付が開始しています。",
        },
    )]
    result.cards = result.cards[:1]
    result.cards[0].title_lines = ["온라인 예약은", "2026년 11월 13일 시작"]
    result.cards[0].body_sentences = ["발매는 2027년 2월 중순경 예정이야."]
    result.cards[0].fact_ids = ["preorder-start"]
    return result


def publication_blockers(package):
    return [issue for issue in check_sources(package) if issue.code == "SOURCE_PUBLICATION_DATE_SHOWN"]


def test_documented_one_piece_preorder_start_can_match_publication_date(package):
    assert publication_blockers(package) == []
    assert package.event_start is None and package.event_end is None


@pytest.mark.parametrize("defect", [
    "unverified", "wrong_source", "not_cited", "missing_start", "different_start",
    "wrong_fact_type", "no_quote", "quote_without_date", "quote_other_date",
    "publication_only_quote", "publication_and_undated_start", "separate_sentences", "conflicting_dates", "postponed",
    "negative_start", "publication_copy", "preorder_end_copy",
])
def test_incomplete_or_conflicting_preorder_evidence_keeps_publication_blocker(package, defect):
    fact = package.facts[0]
    if defect == "unverified":
        fact.verified = False
    elif defect == "wrong_source":
        fact.source_ids = ["other-source"]
    elif defect == "not_cited":
        package.cards[0].fact_ids = []
    elif defect == "missing_start":
        fact.details.pop("preorder_start")
    elif defect == "different_start":
        fact.details["preorder_start"] = "2026-11-14"
    elif defect == "wrong_fact_type":
        fact.fact_type = "product"
    elif defect == "no_quote":
        fact.details.pop("source_quote")
    elif defect == "quote_without_date":
        fact.details["source_quote"] = "オンラインで予約受付が開始しています。"
    elif defect == "quote_other_date":
        fact.details["source_quote"] = "2026年11月14日より予約受付が開始しています。"
    elif defect == "publication_only_quote":
        fact.details["source_quote"] = "2026年11月13日公開の記事です。"
    elif defect == "publication_and_undated_start":
        fact.details["source_quote"] = "2026年11月13日公開、予約受付は既に開始しています。"
    elif defect == "separate_sentences":
        fact.details["source_quote"] = "2026年11月13日公開。予約受付が開始しています。"
    elif defect == "conflicting_dates":
        fact.details["source_quote"] = "2026年11月13日公開、予約受付は2026年11月14日より開始しています。"
    elif defect == "postponed":
        fact.details["source_quote"] = "2026年11月13日に予約受付が開始する予定でしたが延期しました。"
    elif defect == "negative_start":
        fact.details["source_quote"] = "2026年11月13日に予約受付は開始しません。"
    elif defect == "publication_copy":
        package.cards[0].title_lines = ["기사 게시일", "2026년 11월 13일"]
        package.cards[0].body_sentences = ["온라인 예약 접수도 시작됐어."]
    elif defect == "preorder_end_copy":
        package.cards[0].title_lines = ["온라인 예약 마감", "2026년 11월 13일"]
    assert publication_blockers(package)


def test_existing_sales_start_fact_handling_remains_unchanged(package):
    package.facts[0].fact_type = "sales_start"
    package.facts[0].details.pop("preorder_start")
    assert publication_blockers(package) == []


@pytest.mark.parametrize("title", [
    "온라인 예약 마감", "온라인 예약 종료", "온라인 예약 취소", "온라인 예약 연기",
])
def test_undated_start_in_body_does_not_excuse_a_conflicting_dated_title(package, title):
    package.cards[0].title_lines = [title, "2026년 11월 13일"]
    package.cards[0].body_sentences = ["온라인 예약 접수는 이미 시작됐어."]
    assert publication_blockers(package)


def test_negative_start_copy_is_not_treated_as_a_documented_start(package):
    package.cards[0].title_lines = ["2026년 11월 13일", "온라인 예약은 시작되지 않았어"]
    assert publication_blockers(package)
