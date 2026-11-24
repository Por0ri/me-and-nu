from datetime import date

import pytest

from ani_menu_agent.models import Fact
from ani_menu_agent.source_freshness import (
    candidate_is_stale,
    expiry_evidence,
    extract_source_schedule,
    goods_sale_deadline,
)


TODAY = date(2026, 11, 23)


@pytest.mark.parametrize(
    ("content_type", "text", "expected"),
    [
        ("goods", "販売期間：2026年11月12日から", {"sale_start": "2026-11-12"}),
        ("goods", "販売期間：2026年12月1日～", {"sale_start": "2026-12-01"}),
        ("goods", "2026년 12월 1일부터 판매 시작", {"sale_start": "2026-12-01"}),
        ("goods", "판매 마감: 2026년 12월 17일", {"sale_end": "2026-12-17"}),
        ("event", "2026년 12월 1일부터 팝업 개최", {"event_start": "2026-12-01"}),
        ("event", "개최 기간: 2026년 12월 1일~3일", {"event_start": "2026-12-01", "event_end": "2026-12-03"}),
        ("goods", "予約受付は2026年11月25日から開始。", {"reservation_start": "2026-11-25"}),
        ("goods", "2026年12月1日発売予定", {"release_date": "2026-12-01"}),
        ("goods", "2027年5月発売予定", {"release_date": "2027-05"}),
        ("news", "신작의 첫 방송은 2026년 12월 1일", {"premiere_date": "2026-12-01"}),
        ("news", "신작 애니 2026년 10월부터 방송", {"premiere_date": "2026-10"}),
        ("goods", "판매 시작: 2026년 12월 1일 오후 5시", {"sale_start": "2026-12-01T17:00"}),
        ("goods", "판매 마감: 2026-12-01T15:30:15Z", {"sale_end": "2026-12-01T15:30:15+00:00"}),
        ("news", "제9화는 2026년 10월 11일 방송", {}),
        ("news", "次回の第9話は2026年10月11日放送予定。", {}),
        ("event", "2026년 11월 12일 단 하루 팝업 개최", {"event_start": "2026-11-12", "event_end": "2026-11-12"}),
        ("goods", "発送予定：2026年12月1日。価格は770円。", {}),
    ],
)
def test_schedule_preserves_date_role_and_precision(content_type, text, expected):
    assert extract_source_schedule({"text": text}, content_type) == expected


def test_alyas_sale_times_remain_separate_from_reservations_and_delivery():
    raw = {"text": "予約締切：2026年11月10日。販売期間：2026年11月12日(木)17:00～"
                   "2026年12月17日(木)16:59。発送予定：2027年3月20日。"}
    assert extract_source_schedule(raw, "goods") == {
        "sale_start": "2026-11-12T17:00", "sale_end": "2026-12-17T16:59",
        "reservation_end": "2026-11-10",
    }
    assert expiry_evidence(raw, "goods", today=TODAY)["end_date"] == "2026-12-17"


@pytest.mark.parametrize(("content_type", "field"), [("event", "event_start"), ("goods", "sale_start")])
def test_started_but_no_reported_end_is_eligible_without_claiming_availability(content_type, field):
    candidate = {field: "2026-11-12", "valid_until": "2026-10-01", "expires_at": "2026-10-02"}
    result = expiry_evidence(candidate, content_type, today=TODAY)
    assert (result["status"], result["end_date"]) == ("active", None)
    assert not candidate_is_stale(candidate, content_type, today=TODAY)
    candidate["availability_status"] = "ended"
    assert candidate_is_stale(candidate, content_type, today=TODAY)


def test_ambiguous_windows_do_not_become_a_single_schedule():
    raw = {"main_text": "販売期間：2026年12月1日～10日。別店舗の販売期間：2026年12月3日～19日。"}
    assert extract_source_schedule(raw, "goods") == {}
    assert expiry_evidence(raw, "goods", today=TODAY)["status"] == "unresolved"
    raw = {"text": "会期：2026年12月1日～\n[연결된 상세 원문: 다른 행사]\n開催期間：2026年11月1日～19日"}
    assert extract_source_schedule(raw, "event") == {"event_start": "2026-12-01"}


@pytest.mark.parametrize(("kind", "end"), [("goods", "sale_end"), ("event", "event_end")])
def test_collection_and_generation_honor_announced_closing_minute(kind, end):
    from datetime import datetime

    candidate = {end: "2026-12-17T16:59+09:00"}
    before = datetime.fromisoformat("2026-12-17T07:58:59+00:00")
    cutoff = datetime.fromisoformat("2026-12-17T07:59:00+00:00")
    assert expiry_evidence(candidate, kind, now=before)["status"] == "active"
    assert expiry_evidence(candidate, kind, now=cutoff)["status"] == "expired"


def test_bad_legacy_end_cannot_override_its_actual_open_range():
    bad = Fact(id="req-sales-period", fact_type="sales_period", statement="판매 종료일: 2026-11-12",
               source_ids=["article"], verified=False, time_sensitive=True,
               details={"sale_end": "2026-11-12", "source_quote": "販売期間：2026年11月12日から"})
    candidate = {"facts": [bad.model_dump()]}
    assert extract_source_schedule(candidate, "goods") == {"sale_start": "2026-11-12"}
    assert expiry_evidence(candidate, "goods", today=TODAY)["status"] == "active"
    assert goods_sale_deadline([bad]) is None


def test_only_primary_article_evidence_changes_schedule():
    candidate = {"sources": [{"id": "article"}, {"id": "other"}], "source_schedule": {"sale_start": "2026-12-01"},
                 "facts": [{"source_ids": ["other"], "details": {"sale_end": "2026-11-01", "availability_status": "ended"}}]}
    assert extract_source_schedule(candidate, "goods") == {"sale_start": "2026-12-01"}
    assert expiry_evidence(candidate, "goods", today=TODAY)["status"] == "active"


def test_goods_campaign_uses_overall_range_without_bonus_clock_or_game_release():
    # Inside Games, 2026-11-24: the bonus has a start hour, the campaign does not.
    candidate = {
        "published_at": "2026-11-24T00:10:03+09:00",
        "text": (
            "コラボキャンペーンを11月24日より開催します。\n"
            "12月3日発売予定のゲームとコラボ。\n"
            "11月24日10時～12月7日までの期間中、対象商品購入で特典を配布。\n"
            "コラボキャンペーンは、全国の店舗にて11月24日～12月7日まで開催。"
        ),
    }
    expected = {"sale_start": "2026-11-24", "sale_end": "2026-12-07"}
    assert extract_source_schedule(candidate, "goods") == expected
    assert expiry_evidence(candidate, "goods", today=date(2026, 12, 7))["status"] == "active"
    expired = expiry_evidence(candidate, "goods", today=date(2026, 12, 8))
    assert expired["status"] == "expired"
    assert "全国の店舗にて11月24日～12月7日" in expired["evidence"]
    candidate["text"] = candidate["text"].replace("12月3日発売", "2026年12月3日発売")
    assert extract_source_schedule(candidate, "goods") == expected


@pytest.mark.parametrize("stamp,text,expected", [
    ("2026-12-20", "予約期間：12月28日～1月3日", {"reservation_start": "2026-12-28", "reservation_end": "2027-01-03"}),
    ("2027-01-01", "販売期間：12月28日～1月3日", {"sale_start": "2026-12-28", "sale_end": "2027-01-03"}),
    ("2026-12-20", "販売期間：12月28日～翌月3日", {"sale_start": "2026-12-28", "sale_end": "2027-01-03"}),
    ("2026-12-24", "販売期間：1月2日～5日", {"sale_start": "2027-01-02", "sale_end": "2027-01-05"}),
    ("2024-11-21", "販売期間：11月24日～12月7日", {"sale_start": "2024-11-24", "sale_end": "2024-12-07"}),
    ("2026-11-24", "キャンペーンは11月24日から。特典の販売期間：11月20日～12月1日。", {"sale_start": "2026-11-24"}),
    ("2026-11-24", "キャンペーン期間：12月7日まで", {"sale_end": "2026-12-07"}),
    ("2026-11-24", "キャンペーンは11月24日～12月7日まで予約受付。", {"reservation_start": "2026-11-24", "reservation_end": "2026-12-07"}),
    ("2026-11-24", "昨年のコラボキャンペーンは11月24日～12月7日まで開催。", {"sale_start": "2025-11-24", "sale_end": "2025-12-07"}),
    ("2026-11-24", "販売期間：11月31日～12月7日", {}),
])
def test_yearless_period_uses_publication_day_and_calendar_rollover(stamp, text, expected):
    assert extract_source_schedule({"published_at": stamp, "text": text}, "goods") == expected


def test_yearless_period_needs_the_primary_publication_date():
    text = "コラボキャンペーン期間：11月24日～12月7日"
    for fields in (
        {"fetched_at": "2026-11-24", "title": "2026-11-24 キャンペーン"},
        {"published_at": "6d 2h 26m"},
        {"sources": [{"id": "primary"}, {"id": "linked", "published_at": "2026-11-24"}]},
    ):
        assert extract_source_schedule({"text": text, **fields}, "goods") == {}
    packet = {"sources": [{"id": "primary", "published_at": "2024-11-24"}], "facts": [
        {"source_ids": ["primary"], "details": {"source_quote": text}},
    ]}
    assert extract_source_schedule(packet, "goods") == {
        "sale_start": "2024-11-24", "sale_end": "2024-12-07",
    }
    assert candidate_is_stale(packet, "goods", today=TODAY)


@pytest.mark.parametrize("stamp,headline,period", [
    ("2026-11-24", "", "3月1日～3月10日まで開催されました。"),
    ("2026-11-24", "", "3月1日～3月10日まで開催。"),
    ("2026-12-24", "", "1月12日～1月14日まで開催されました。"),
    ("2027-01-24", "", "12月12日～12月14日まで開催予定。"),
    ("2026-11-24", "ゲームとコラボした当時のキャンペーンを振り返る", "11月24日～12月7日まで開催。"),
])
def test_ambiguous_or_retrospective_yearless_campaign_cannot_become_current(stamp, headline, period):
    candidate = {"published_at": stamp, "title": headline, "text": "コラボキャンペーンは" + period}
    assert extract_source_schedule(candidate, "goods") == {}
    assert expiry_evidence(candidate, "goods", today=date.fromisoformat(stamp))["status"] == "unresolved"


def test_retrospective_with_its_own_year_is_expired():
    candidate = {"published_at": "2026-11-24", "title": "ゲームとのコラボを振り返る",
                 "text": "2025年のコラボキャンペーンは11月24日～12月7日まで開催。"}
    assert extract_source_schedule(candidate, "goods") == {"sale_start": "2025-11-24", "sale_end": "2025-12-07"}
    assert candidate_is_stale(candidate, "goods", today=TODAY)


@pytest.mark.parametrize(("release", "expected"), [("2027年1月1日", "2027-01-01"), ("2027年1月", "2027-01")])
def test_unresolved_campaign_does_not_take_a_related_games_release_date(release, expected):
    game = release + "発売予定のゲーム。"
    candidate = {"published_at": "2026-11-24", "text": "コラボキャンペーンは3月1日～3月10日まで開催されました。" + game}
    assert expiry_evidence(candidate, "goods", today=TODAY)["status"] == "unresolved"
    assert extract_source_schedule(candidate, "goods") == {}
    assert extract_source_schedule({"text": game}, "goods") == {"release_date": expected}
