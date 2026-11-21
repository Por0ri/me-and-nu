"""Execute aggregate SQL against isolated records, including adverse cohorts."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import BigInteger, Column, DateTime, Integer, JSON, MetaData, String, Table, create_engine
from sqlalchemy.dialects.postgresql import JSONB

from app.services import admin_analytics_service as service

UTC = timezone.utc
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
WINDOW = service.analytics_window("7d", now=NOW)
START = WINDOW.start_at
AT = NOW - timedelta(days=1)


def aggregate_tables():
    """Only columns read by aggregates; real SQL, not a mocked query result."""
    meta = MetaData()
    stamp = DateTime(timezone=True)
    Table("user_account", meta,
          Column("user_id", BigInteger, primary_key=True), Column("joined_at", stamp),
          Column("onboarding_completed_at", stamp), Column("account_status", String))
    Table("user_event", meta,
          Column("user_event_id", BigInteger, primary_key=True), Column("user_id", BigInteger),
          Column("content_id", BigInteger), Column("event_type", String),
          Column("event_value", JSON().with_variant(JSONB, "postgresql")),
          Column("occurred_at", stamp), Column("received_at", stamp), Column("data_source", String))
    Table("content", meta, Column("content_id", BigInteger, primary_key=True), Column("title", String))
    Table("search_log", meta,
          Column("search_log_id", BigInteger, primary_key=True), Column("search_text", String),
          Column("result_count", Integer), Column("searched_at", stamp), Column("data_source", String))
    Table("tap", meta, Column("tap_id", BigInteger, primary_key=True), Column("user_id", BigInteger),
          Column("deleted_at", stamp))
    Table("onboarding_answers", meta,
          Column("tap_id", BigInteger, primary_key=True), Column("schema_version", Integer),
          Column("q1_choice", String), Column("consumption_type", String), Column("created_at", stamp))
    Table("content_report", meta,
          Column("report_id", BigInteger, primary_key=True), Column("status", String),
          Column("reason_code", String), Column("created_at", stamp), Column("content_id", BigInteger),
          Column("detail", String), Column("user_id", BigInteger))
    Table("inquiry", meta,
          Column("inquiry_id", BigInteger, primary_key=True), Column("status", String),
          Column("inquiry_type", String), Column("created_at", stamp), Column("detail", String),
          Column("user_id", BigInteger))
    Table("agent_run", meta,
          Column("agent_run_id", BigInteger, primary_key=True), Column("status", String),
          Column("created_at", stamp))
    return meta


class ReadSession:
    def __init__(self, connection, meta):
        self.connection, self.meta, self.statements = connection, meta, []

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.connection.scalar(statement)

    async def execute(self, statement):
        self.statements.append(statement)
        return self.connection.execute(statement)

    def insert(self, table, **values):
        # SQLite does not preserve offsets; persist the same UTC instant.
        values = {key: value.astimezone(UTC) if isinstance(value, datetime) else value
                  for key, value in values.items()}
        self.connection.execute(self.meta.tables[table].insert().values(**values))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    meta = aggregate_tables()
    meta.create_all(engine)
    with engine.connect() as connection:
        yield ReadSession(connection, meta)
    engine.dispose()


def account(db, user_id, *, joined=START, completed=START, status="active"):
    db.insert("user_account", user_id=user_id, joined_at=joined,
              onboarding_completed_at=completed, account_status=status)


def event(db, event_id, user=1, *, content=1, kind="open", at=AT,
          received=None, source="production", surface="detail"):
    db.insert("user_event", user_event_id=event_id, user_id=user, content_id=content,
              event_type=kind, occurred_at=at, received_at=received or at,
              data_source=source, event_value={"surface": surface, "session_id": "same-browser-session"})


@pytest.mark.parametrize("period,days", [("7d", 7), ("30d", 30), ("3m", 90), ("6m", 180)])
def test_window_preserves_instant_and_explicit_rolling_period(period, days):
    kst = NOW.astimezone(timezone(timedelta(hours=9)))
    window = service.analytics_window(period, now=kst)
    assert window.end_at == NOW
    assert window.end_at.utcoffset() == timedelta(0)
    assert window.end_at - window.start_at == timedelta(days=days)
    with pytest.raises(ValueError, match="timezone"):
        service.analytics_window(period, now=NOW.replace(tzinfo=None))


@pytest.mark.asyncio
async def test_usage_source_half_open_window_and_repeated_open_segments(db):
    account(db, 1)
    event(db, 1, at=START)
    event(db, 2, at=START + timedelta(microseconds=1))
    event(db, 3, user=2, kind="impression", surface="home_feed")
    event(db, 4, user=3, kind="like")
    event(db, 5, user=4, source="local_demo")
    event(db, 6, user=5, source="automated_test")
    event(db, 7, user=6, at=START - timedelta(microseconds=1))
    event(db, 8, user=7, at=NOW)
    event(db, 9, user=8, received=NOW)
    event(db, 10, user=9, kind="impression", surface="saved")
    result = await service.usage_metrics(db, WINDOW, "production")
    assert result.active_users.value == 2
    assert result.content_read_segments.value == 2
    assert result.content_read_segments.reason == "open_segments_not_pageviews"
    assert result.active_users.availability == "partial"
    assert (await service.usage_metrics(db, WINDOW, "local_demo")).active_users.value == 1


@pytest.mark.asyncio
async def test_feed_ratio_same_completion_cohort_excludes_early_wrong_surface_and_source(db):
    for user in range(1, 5):
        account(db, user, completed=AT)
    account(db, 5, completed=START - timedelta(seconds=1))
    account(db, 6, completed=NOW)
    event(db, 1, user=1, kind="impression", at=AT, surface="home_feed")
    event(db, 2, user=1, kind="impression", at=AT, surface="home_feed")
    event(db, 3, user=2, kind="impression", at=AT - timedelta(seconds=1), surface="home_feed")
    event(db, 4, user=3, kind="impression", at=AT, surface="saved")
    event(db, 5, user=4, kind="impression", at=AT, surface="home_feed", source="local_demo")
    event(db, 6, user=5, kind="impression", at=AT, surface="home_feed")
    rate = (await service.usage_metrics(db, WINDOW, "production")).signup_feed_observation_rate
    assert (rate.numerator, rate.denominator, rate.value) == (1, 4, 25)
    assert rate.availability == "partial"


@pytest.mark.asyncio
async def test_empty_denominators_are_unknown_but_observed_counts_are_zero(db):
    usage = await service.usage_metrics(db, WINDOW, "production")
    assert usage.content_read_segments.value == usage.active_users.value == 0
    rate = usage.signup_feed_observation_rate
    assert rate.value is None and rate.reason == "empty_denominator"
    assert rate.numerator == rate.denominator == 0
    returning = await service.returning_users(db, WINDOW, "production")
    assert returning.value is None and returning.reason == "empty_denominator"
    prefs = await service.preferences(db, WINDOW)
    assert prefs.availability == "unavailable" and prefs.sample_size == 0 and prefs.items == []


@pytest.mark.asyncio
async def test_funnel_counts_same_registered_cohort_and_requires_onboarding_order(db):
    account(db, 1, joined=START, completed=AT)
    account(db, 2, completed=None)
    account(db, 3, joined=AT, completed=START)  # Invalid historical ordering.
    account(db, 4, joined=START - timedelta(seconds=1), completed=AT)
    account(db, 5, completed=NOW)
    event(db, 1, user=1, kind="impression", surface="home_feed")
    event(db, 2, user=1, kind="impression", at=AT - timedelta(seconds=1), surface="home_feed")
    event(db, 3, user=3, kind="impression", surface="home_feed")
    event(db, 4, user=4, kind="impression", surface="home_feed")
    steps = {item.key: item.metric for item in await service.funnel(db, WINDOW, "production")}
    assert steps["signupStarted"].value is None
    assert [steps[key].value for key in ("accountCreated", "onboardingCompleted", "feedObserved")] == [4, 1, 1]


@pytest.mark.asyncio
async def test_current_preferences_map_both_versions_and_exclude_inactive_records(db):
    choices = [(1, "ost"), (2, "relationships"), (2, "goods"), (1, "world")]
    for user, (version, choice) in enumerate(choices, 1):
        account(db, user, status="warned" if user == 4 else "active")
        db.insert("tap", tap_id=user, user_id=user, deleted_at=None)
        db.insert("onboarding_answers", tap_id=user, schema_version=version,
                  q1_choice=choice if version == 1 else None,
                  consumption_type=choice if version == 2 else None, created_at=AT)
    for user, status, deleted in [(5, "withdrawn", None), (6, "suspended", None),
                                  (7, "banned", None), (8, "active", AT)]:
        account(db, user, status=status)
        db.insert("tap", tap_id=user, user_id=user, deleted_at=deleted)
        db.insert("onboarding_answers", tap_id=user, schema_version=2,
                  q1_choice=None, consumption_type="ost", created_at=AT)
    result = await service.preferences(db, WINDOW)
    assert result.sample_size == 4
    assert {item.key: (item.count, item.percentage) for item in result.items} == {
        key: (1, 25) for key in ("ost", "relationships", "goods", "other")}
    assert result.reason == "current_answers_not_historical_snapshot"


@pytest.mark.asyncio
async def test_returning_rate_intersects_distinct_users_from_adjacent_windows(db):
    before = START - timedelta(days=1)
    event(db, 1, user=1, at=before)
    event(db, 2, user=1, at=before)
    event(db, 3, user=2, at=before, received=AT)  # Late previous-window delivery is known now.
    event(db, 4, user=3, at=before, source="local_demo")
    event(db, 5, user=4, at=START - timedelta(days=7, microseconds=1))
    event(db, 6, user=1)
    event(db, 7, user=1)
    event(db, 8, user=3)
    event(db, 9, user=5, at=START)  # Current boundary, not previous denominator.
    result = await service.returning_users(db, WINDOW, "production")
    assert (result.numerator, result.denominator, result.value) == (1, 2, 50)
    assert result.reason == "observed_window_return_rate"


@pytest.mark.asyncio
async def test_top_five_stable_ties_and_search_zero_results(db):
    for content in reversed(range(1, 8)):
        db.insert("content", content_id=content, title=f"content-{content}")
        event(db, content, content=content)
    event(db, 8, content=6)
    event(db, 9, content=7, source="local_demo")
    result = await service.top_contents(db, WINDOW, "production")
    assert [item.content_id for item in result.items] == [6, 1, 2, 3, 4]
    assert [item.count for item in result.items] == [2, 1, 1, 1, 1]
    for row, phrase in enumerate(["gamma", "beta", "alpha", "delta", "epsilon", "zeta", "beta"], 1):
        db.insert("search_log", search_log_id=row, search_text=phrase, result_count=0 if phrase != "alpha" else 2,
                  searched_at=AT, data_source="production")
    db.insert("search_log", search_log_id=8, search_text="excluded", result_count=0,
              searched_at=AT, data_source="local_demo")
    db.insert("search_log", search_log_id=9, search_text="end-excluded", result_count=0,
              searched_at=NOW, data_source="production")
    popular = await service.search_ranking(db, WINDOW, "production")
    zero = await service.search_ranking(db, WINDOW, "production", empty_only=True)
    assert [item.label for item in popular.items] == ["beta", "alpha", "delta", "epsilon", "gamma"]
    assert [item.label for item in zero.items] == ["beta", "delta", "epsilon", "gamma", "zeta"]
    assert popular.items[0].count == zero.items[0].count == 2


@pytest.mark.asyncio
async def test_attention_old_unresolved_records_pagination_and_no_private_text(db):
    for report, status in enumerate(["received", "reviewing", "resolved", "rejected"], 1):
        db.insert("content_report", report_id=report, status=status, reason_code="wrong_info",
                  created_at=START - timedelta(days=365), content_id=100 + report,
                  user_id=700, detail="SECRET report and email@example.com")
    for inquiry, status in enumerate(["received", "answered", "closed"], 1):
        db.insert("inquiry", inquiry_id=inquiry, status=status, inquiry_type="service", created_at=AT,
                  user_id=701, detail="SECRET inquiry and 010-1234-5678")
    db.insert("inquiry", inquiry_id=4, status="received", inquiry_type="bug", created_at=NOW,
              user_id=702, detail="not yet")
    first = await service.attention(db, now=NOW, page=1, page_size=2)
    second = await service.attention(db, now=NOW, page=2, page_size=2)
    assert (first.known_total, first.reports, first.inquiries) == (3, 2, 1)
    assert [(item.kind, item.record_id) for item in first.items + second.items] == [
        ("report", 1), ("report", 2), ("inquiry", 1)]
    assert first.availability == "partial" and first.scheduled_notices.value is None
    assert all(item.created_at.tzinfo is not None for item in first.items)
    payload = first.model_dump_json(by_alias=True) + second.model_dump_json(by_alias=True)
    assert all(secret not in payload for secret in ["SECRET", "email@", "010-", "userId", "detail"])
    filtered = await service.attention(db, now=NOW, kind="inquiry")
    assert filtered.known_total == 1 and filtered.availability == "available"
    missing = await service.attention(db, now=NOW, kind="notice")
    assert missing.availability == "unavailable" and missing.items == []
    assert (await service.attention(db, now=NOW, page=99)).items == []


@pytest.mark.asyncio
async def test_generation_24h_statuses_are_explicitly_partial(db):
    for run, status in enumerate(["succeeded", "queued", "running", "failed", "cancelled"], 1):
        db.insert("agent_run", agent_run_id=run, status=status, created_at=NOW - timedelta(hours=24))
    db.insert("agent_run", agent_run_id=6, status="failed", created_at=NOW)
    db.insert("agent_run", agent_run_id=7, status="failed", created_at=NOW - timedelta(hours=24, microseconds=1))
    result = await service.generation(db, WINDOW)
    assert (result.succeeded, result.in_progress, result.failed, result.cancelled) == (1, 2, 1, 1)
    assert result.availability == "partial"
    assert result.reason == "importer_does_not_record_all_live_generation_attempts"
    assert result.window_end_at - result.window_start_at == timedelta(hours=24)


@pytest.mark.asyncio
async def test_overview_unknowns_and_source_scope_survive_serialization(db):
    result = await service.overview(db, WINDOW, "local_demo")
    payload = result.model_dump(mode="json", by_alias=True)
    assert payload["meta"]["eventDataSource"] == "local_demo"
    assert payload["meta"]["scope"] == "connected_database"
    assert "shared_database_not_environment_isolated" in payload["meta"]["limitations"]
    assert payload["deviceDistribution"]["availability"] == "unavailable"
    assert payload["averageSessionDuration"]["value"] is None
    assert payload["averageSessionDuration"]["reason"] == "session_boundaries_not_collected"
