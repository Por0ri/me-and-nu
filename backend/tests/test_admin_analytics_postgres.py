"""Opt-in PostgreSQL execution of the same aggregate contract scenarios.

Uses only ADMIN_POSTGRES_TEST_URL accepted by the existing disposable-local-DB
fixture; never reads application DATABASE_URL. Each case owns a random schema.
Minimal typed tables exercise actual JSONB, timestamptz, union and aggregate SQL.
"""

import pytest

import test_admin_analytics as cases
from test_admin_postgres_integration import admin_pg, run_async, sessions  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    cases.test_usage_source_half_open_window_and_repeated_open_segments,
    cases.test_feed_ratio_same_completion_cohort_excludes_early_wrong_surface_and_source,
    cases.test_empty_denominators_are_unknown_but_observed_counts_are_zero,
    cases.test_funnel_counts_same_registered_cohort_and_requires_onboarding_order,
    cases.test_current_preferences_map_both_versions_and_exclude_inactive_records,
    cases.test_returning_rate_intersects_distinct_users_from_adjacent_windows,
    cases.test_top_five_stable_ties_and_search_zero_results,
    cases.test_attention_old_unresolved_records_pagination_and_no_private_text,
    cases.test_generation_24h_statuses_are_explicitly_partial,
    cases.test_overview_unknowns_and_source_scope_survive_serialization,
], ids=lambda scenario: scenario.__name__.removeprefix("test_"))
async def test_postgres_aggregate_contract(admin_pg, scenario):
    meta = cases.aggregate_tables()
    meta.create_all(admin_pg.engine)
    with admin_pg.engine.connect() as connection:
        await scenario(cases.ReadSession(connection, meta))


def test_migrated_schema_with_real_async_session_preserves_zero_and_unknown(admin_pg):
    """Validate actual service-schema wiring, independently of the minimal tables."""
    admin_pg.migrate()

    async def check():
        async with sessions(admin_pg) as factory, factory() as db:
            result = await cases.service.overview(db, cases.WINDOW, "production")
            by_step = {step.key: step.metric for step in result.funnel}
            assert by_step["accountCreated"].availability == "available"
            assert by_step["accountCreated"].value == 0
            assert by_step["onboardingCompleted"].value == 0
            assert result.usage.active_users.value == 0
            assert result.usage.content_read_segments.value == 0
            assert result.usage.signup_feed_observation_rate.value is None
            assert result.usage.signup_feed_observation_rate.reason == "empty_denominator"
            assert result.returning_user_rate.value is None
            assert result.top_contents.items == result.popular_searches.items == []
            assert result.zero_result_searches.items == []
            assert result.preference_distribution.sample_size == 0
            assert result.device_distribution.availability == "unavailable"
            assert result.average_session_duration.value is None

            generated = await cases.service.generation(db, cases.WINDOW)
            assert generated.availability == "partial"
            assert (generated.succeeded, generated.in_progress, generated.failed, generated.cancelled) == (0, 0, 0, 0)
            backlog = await cases.service.attention(db, now=cases.NOW)
            assert backlog.known_total == backlog.reports == backlog.inquiries == 0
            assert backlog.items == [] and backlog.scheduled_notices.value is None
            assert backlog.generated_at.tzinfo is not None

    run_async(check())
