"""Conservative, source-precision-preserving goods release period admission."""
from datetime import datetime

import pytest

from ani_menu_agent import source_freshness as freshness


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 16, 12, tzinfo=tz)

    monkeypatch.setattr(freshness, "datetime", Clock)


def evidence(text, **overrides):
    return freshness.expiry_evidence({"text": text, "published_at": "2026-11-13", **overrides}, "goods")


def test_one_piece_release_preserves_middle_of_month_without_inventing_a_day():
    result = evidence(
        "本日2026年11月13日(金)より予約受付が開始しています。\n"
        "発売日：\n2027年2月中旬頃発売予定\n※発売日は変更となる可能性があります。"
    )
    assert result == {
        "status": "active", "end_date": None,
        "evidence": "upcoming product release period: 2027年2月中旬頃発売予定",
        "availability_status": "upcoming_release", "release_date": None,
        "release_precision": "mid_month", "release_window_start": "2027-02-11",
        "release_window_end": "2027-02-20", "release_text": "2027年2月中旬頃発売予定",
    }
    assert "sale_end" not in result


@pytest.mark.parametrize(("phrase", "precision", "start", "end"), [
    ("2027年2月発売予定", "month", "2027-02-01", "2027-02-28"),
    ("2027年2月上旬発売予定", "early_month", "2027-02-01", "2027-02-10"),
    ("発売日：2027年2月中旬", "mid_month", "2027-02-11", "2027-02-20"),
    ("2028年2月下旬ごろ発売予定", "late_month", "2028-02-21", "2028-02-29"),
    ("2027年4月下旬発売予定", "late_month", "2027-04-21", "2027-04-30"),
])
def test_period_bounds_are_metadata_not_release_dates(phrase, precision, start, end):
    result = evidence(phrase)
    assert result["status"] == "active"
    assert result["release_precision"] == precision
    assert result["release_window_start"] == start
    assert result["release_window_end"] == end
    assert result["release_date"] is None
    assert result["end_date"] is None


@pytest.mark.parametrize("phrase", [
    "2026年11月発売予定", "2026年11月中旬発売予定", "2026年11月上旬発売予定",
    "2026年10月下旬発売予定", "2027年0月中旬発売予定", "2027年13月中旬発売予定",
    "2月中旬発売予定", "2027年2月中旬取材", "2027年2月中旬予約開始",
    "2027年2月末発売予定", "2027年2月頃以降発売予定", "発売予定：2027年2月以降",
    "2027年2月中旬発売予定ではありません", "2027年2月中旬発売延期",
])
def test_unsupported_or_not_fully_future_period_is_held(phrase):
    result = evidence(phrase)
    assert result["status"] == "unresolved"
    assert result.get("release_date") is None
    assert result["end_date"] is None


def test_window_start_today_is_not_evidence_of_upcoming_release(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2027, 2, 11, 0, tzinfo=tz)

    monkeypatch.setattr(freshness, "datetime", Clock)
    assert evidence("2027年2月中旬発売予定")["status"] == "unresolved"


def test_window_before_publication_is_not_reinterpreted_as_next_year():
    assert evidence("2027年2月中旬発売予定", published_at="2027-03-01")["status"] == "unresolved"


@pytest.mark.parametrize("overrides", [
    {"sale_end": "2026-11-15"}, {"availability_status": "sold_out"},
    {"facts": [{"fact_type": "product", "details": {"availability_status": "discontinued"}}]},
])
def test_existing_expiry_or_sold_out_evidence_still_wins(overrides):
    assert evidence("2027年2月中旬発売予定", **overrides)["status"] == "expired"


def test_explicit_expired_period_in_prose_still_wins():
    result = evidence("予約期間：2026年11月1日～2026年11月15日\n2027年2月中旬発売予定")
    assert result["status"] == "expired"
    assert result["end_date"] == "2026-11-15"


def test_conflicting_explicit_end_dates_stay_held():
    result = evidence("2027年2月中旬発売予定", sale_end="2026-11-30", preorder_end="2026-12-01")
    assert result["status"] == "unresolved"


def test_conflicting_approximate_release_periods_stay_held():
    assert evidence("2027年2月中旬発売予定\n2027年3月下旬発売予定")["status"] == "unresolved"


def test_repeated_same_phrase_in_title_and_body_is_not_a_conflict():
    phrase = "2027年2月中旬発売予定"
    assert evidence(phrase, title=phrase)["release_precision"] == "mid_month"


@pytest.mark.parametrize("phrase", [
    "2027年2月中旬発売予定でしたが延期になりました。",
    "2027年2月中旬発売予定は未定です。",
    "2027年2月中旬発売予定ではなく発売時期未定です。",
    "発売日：2027年2月中旬、中止となりました。",
    "2027年2月中旬発売予定でしたが、取り消しになりました。",
])
def test_schedule_negation_in_same_clause_overrides_unchanged_positive_title(phrase):
    result = evidence(phrase, title="2027年2月中旬発売予定")
    assert result["status"] == "unresolved"
    assert result.get("release_date") is None


def test_normal_next_line_release_change_disclaimer_does_not_revoke_schedule():
    result = evidence("2027年2月中旬頃発売予定\n※発売日は変更となる可能性があります。")
    assert result["status"] == "active"
    assert result["release_text"] == "2027年2月中旬頃発売予定"


@pytest.mark.parametrize("change", ["変更済み", "変更しました", "変更となりました", "変更予定"])
def test_definite_change_does_not_leave_previous_period_active(change):
    result = evidence(
        f"2027年2月中旬発売予定でしたが、発売日は2027年3月以降に{change}。",
        title="2027年2月中旬発売予定",
    )
    assert result["status"] == "unresolved"
    assert result.get("release_date") is None


def test_same_sentence_possible_change_disclaimer_does_not_revoke_schedule():
    result = evidence("2027年2月中旬頃発売予定ですが、発売日は変更となる可能性があります。")
    assert result["status"] == "active"
    assert result["release_text"] == "2027年2月中旬頃発売予定"


def test_unrelated_postponement_in_another_sentence_is_not_attached_to_release():
    result = evidence("別の展示イベントは延期です。2027年2月中旬発売予定")
    assert result["status"] == "active"


def test_existing_exact_release_day_is_unchanged():
    result = evidence("2027年2月15日発売予定")
    assert result["status"] == "active"
    assert result["release_date"] == "2027-02-15"
    assert "release_window_start" not in result


def test_existing_structured_availability_does_not_require_a_release_period():
    result = evidence("発売時期未定", availability_status="preorder_open", availability_evidence="予約受付中")
    assert result["status"] == "active"
    assert result["availability_status"] == "preorder_open"


def test_event_admission_is_not_changed():
    assert freshness.expiry_evidence({"text": "2027年2月中旬発売予定"}, "event")["status"] == "unresolved"


def test_source_phrase_survives_package_reload_and_does_not_trust_stored_bounds():
    result = evidence("", facts=[{
        "fact_type": "product_availability", "verified": False,
        "details": {"evidence_kind": "source_release_window", "source_quote": "2027年2月中旬頃発売予定",
                    "release_window_start": "2099-01-01", "release_window_end": "2099-12-31"},
    }])
    assert result["release_window_start"] == "2027-02-11"
    assert result["release_window_end"] == "2027-02-20"
    assert result["release_date"] is None


def test_unmarked_fact_or_unparseable_quote_is_not_release_evidence():
    for details in (
        {"source_quote": "2027年2月中旬発売予定"},
        {"evidence_kind": "source_release_window", "source_quote": "発売未定", "release_window_end": "2099-12-31"},
    ):
        assert evidence("", facts=[{"fact_type": "product_availability", "details": details}])["status"] == "unresolved"
