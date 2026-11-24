from datetime import date

from ani_menu_agent.source_freshness import (
    candidate_publication_date,
    event_period_from_text,
    expiry_evidence,
)


def test_upcoming_goods_release_date_is_active_without_sale_end() -> None:
    result = expiry_evidence(
        {
            "title": "Figure release announcement",
            "facts": [
                {
                    "fact_type": "release_date",
                    "statement": "The figure will be released on May 20, 2027.",
                    "details": {
                        "release_date": "2027-05-20",
                        "source_quote": "2027年5月20日発売予定",
                    },
                }
            ],
        },
        "goods",
        today=date(2026, 11, 19),
    )

    assert result["status"] == "active"
    assert result["end_date"] is None
    assert "2027-05-20" in (result["evidence"] or "")
    assert "no source-backed sale end date" in (result["evidence"] or "")


def test_upcoming_reservation_start_in_source_text_is_active_without_sale_end() -> None:
    result = expiry_evidence(
        {
            "title": "New item",
            "text": (
                "\u4e88\u7d04\u53d7\u4ed8\u306f2026\u5e7411\u670825\u65e5"
                "\u304b\u3089\u958b\u59cb\u3002"
            ),
        },
        "goods",
        today=date(2026, 11, 19),
    )

    assert result["status"] == "active"
    assert result["end_date"] is None
    assert "2026-11-25" in (result["evidence"] or "")


def test_upcoming_month_only_goods_release_is_not_treated_as_an_exact_day_or_end() -> None:
    result = expiry_evidence(
        {
            "title": "New figure",
            "text": "2027年5月発売予定",
        },
        "goods",
        today=date(2026, 11, 19),
    )

    assert result["status"] == "active"
    assert result["end_date"] is None
    assert "precision=month" in (result["evidence"] or "")


def test_publication_date_alone_does_not_prove_goods_are_available() -> None:
    result = expiry_evidence(
        {"title": "Figure announcement", "published_at": "2026-11-18"},
        "goods",
        today=date(2026, 11, 19),
    )

    assert result["status"] == "unresolved"
    assert result["end_date"] is None


def test_old_release_date_alone_does_not_prove_sales_are_still_open() -> None:
    result = expiry_evidence(
        {"release_date": "2026-10-01"},
        "goods",
        today=date(2026, 11, 19),
    )

    assert result["status"] == "unresolved"
    assert result["end_date"] is None


def test_explicit_expired_sale_end_wins_over_future_release_date() -> None:
    result = expiry_evidence(
        {
            "sale_end": "2026-11-10",
            "release_date": "2027-05-20",
        },
        "goods",
        today=date(2026, 11, 19),
    )

    assert result["status"] == "expired"
    assert result["end_date"] == "2026-11-10"


def test_event_with_only_a_start_can_be_collected() -> None:
    result = expiry_evidence(
        {"event_start": "2026-11-25"},
        "event",
        today=date(2026, 11, 19),
    )

    assert result["status"] == "active"
    assert result["end_date"] is None


def test_reiwa_campaign_period_keeps_source_quote_and_both_dates() -> None:
    text = (
        "【配布期間】令和８年12月4日(金)〜12月20日(日)\n"
        "【実施期間】令和８年12月４日(金)〜12月20日(日)"
    )
    start, end, quote = event_period_from_text(text)
    assert (start, end) == ("2026-12-04", "2026-12-20")
    assert quote in text
    for reference, expected in [(date(2026, 11, 23), "active"), (date(2026, 12, 21), "expired")]:
        assert expiry_evidence({"text": text}, "event", today=reference)["status"] == expected


def test_japanese_event_dates_before_held_verb_and_year_rollover() -> None:
    text = "11月27日～2027年1月3日に開催される。映画は11月5日まで上映。"
    assert event_period_from_text(text)[:2] == ("2026-11-27", "2027-01-03")
    assert event_period_from_text("会期：2026年12月27日から1月3日")[:2] == (
        "2026-12-27", "2027-01-03"
    )


def test_unrelated_dates_and_ambiguous_periods_remain_unresolved() -> None:
    for text in [
        "先行販売期間：2026年9月20日～11月1日。イベントの開催を決定。",
        "2026年11月23日発表。開催期間は未定。次回予告：2027年2月10日。",
        "会期：2026年12月4日～12月20日。別会場の開催期間：2027年1月4日～1月10日。",
        "開催期間：2026年2月29日～3月1日",
    ]:
        assert expiry_evidence({"text": text}, "event")["status"] == "unresolved"


def test_exact_start_with_approximate_end_preserves_only_the_known_day() -> None:
    from ani_menu_agent.source_freshness import extract_source_schedule

    for text in (
        "開催期間：令和８年12月４日(金)～令和９年１月ごろまで",
        "開催期間：令和８年12月４日(金)～12月末頃まで",
    ):
        candidate = {"text": text}
        assert extract_source_schedule(candidate, "event") == {"event_start": "2026-12-04"}
        assert expiry_evidence(candidate, "event", today=date(2026, 11, 23))["status"] == "active"


def test_publication_date_fallback_uses_a_valid_dated_headline_prefix() -> None:
    assert candidate_publication_date({"title": "2024.08.05 TVer配信決定"}) == date(2024, 8, 5)
    assert candidate_publication_date({"title": "配信日 2026.12.04"}) is None
    assert candidate_publication_date({"title": "2026.02.30 ニュース"}) is None


def test_event_freshness_does_not_borrow_a_linked_detail_or_another_fields_date() -> None:
    candidate = {
        "text": "来場プレゼントは開催期間未定。\n[연결된 상세 원문: 別イベント | https://example.jp/]\n"
                "開催期間：2026年12月4日〜12月20日",
        "main_text": "開催期間：", "title": "2026年12月4日〜12月20日",
    }
    assert expiry_evidence(candidate, "event")["status"] == "unresolved"


def test_archive_announcements_use_original_article_date_in_queue_packets():
    from ani_menu_agent.source_freshness import immediate_news_action_expiry

    today = date(2026, 11, 23)
    for title in ("続篇シーズンが今夏海外先行配信！", "シリーズ第20弾のタイトルが決定！"):
        source = {"title": title, "published_at": "2022-07-01"}
        assert immediate_news_action_expiry(source, today=today)["status"] == "expired"
        packet = {"topic": title, "sources": [source, {"published_at": today.isoformat()}],
                  "feed_valid_until": "2027-02-17"}
        assert immediate_news_action_expiry(packet, today=today)["status"] == "expired"
    assert immediate_news_action_expiry(
        {"title": "制作スタッフが語る舞台裏", "published_at": "2022-07-01"}, today=today,
    )["status"] == "not_applicable"
    assert immediate_news_action_expiry(
        {"title": "シーズンのタイトルが決定！", "published_at": "2026-11-22"}, today=today,
    )["status"] != "expired"
    assert immediate_news_action_expiry(
        {"title": "新作の事前登録が本日開始", "published_at": "2026-11-20",
         "facts": [{"details": {"release_date": "2027-02-01"}}]}, today=today,
    )["status"] == "active"
