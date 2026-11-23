"""Opt-in SQL/transaction checks against a guarded disposable local PostgreSQL."""

import pytest
from sqlalchemy import text

import test_admin_support as cases
from app.models import Content, ContentReport, Inquiry, Topic, UserAccount
from app.schemas.admin_support import AdminSupportQuery
from app.services import admin_support_service as service
from test_admin_postgres_integration import admin_pg, run_async, sessions  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    cases.test_union_is_stably_paged_and_empty_page_keeps_real_counts,
    cases.test_counts_use_period_kind_search_before_status_and_never_list_private_body,
    cases.test_period_is_half_open_with_all_supported_windows,
    cases.test_search_wildcards_are_literal_and_large_ids_are_strings,
    cases.test_withdrawn_inquiries_are_hidden_while_anonymous_reports_remain,
    cases.test_all_raw_statuses_and_inquiry_answer_unavailability_are_explicit,
    cases.test_missing_detail_and_empty_list_are_distinct,
], ids=lambda fn: fn.__name__.removeprefix("test_"))
async def test_real_postgres_support_sql(admin_pg, scenario):
    meta = cases.support_tables()
    meta.create_all(admin_pg.engine)
    with admin_pg.engine.connect() as connection:
        await scenario(cases.ReadSession(connection, meta))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["submit", "resolve", "withdraw"])
async def test_page_counts_and_stats_share_one_snapshot_during_commit(admin_pg, change):
    meta = cases.support_tables()
    meta.create_all(admin_pg.engine)
    with admin_pg.engine.begin() as connection:
        seed = cases.ReadSession(connection, meta)
        seed.user(); seed.content()
        if change == "resolve": seed.report()
        if change == "withdraw": seed.inquiry()
    class CommitAfterRead(cases.ReadSession):
        async def execute(self, statement):
            result = await super().execute(statement)
            if len(self.statements) == 1:
                with admin_pg.engine.begin() as writer:
                    if change == "submit": cases.ReadSession(writer, meta).report()
                    elif change == "resolve": writer.execute(text("UPDATE content_report SET status='resolved'"))
                    else: writer.execute(text("UPDATE user_account SET account_status='withdrawn'"))
            return result
    with admin_pg.engine.connect() as connection:
        assert connection.scalar(text("SHOW transaction_isolation")) == "read committed"
        db = CommitAfterRead(connection, meta)
        query = AdminSupportQuery(status="waiting")
        first = await service.list_support(db, query, now=cases.NOW)
        expected_before = 0 if change == "submit" else 1
        assert first.total == first.statistics.waiting == len(first.items) == expected_before
        assert len(db.statements) == 1
        after = await service.list_support(db, query, now=cases.NOW)
        assert after.total == after.statistics.waiting == len(after.items) == 1-expected_before


def test_migrated_schema_real_async_queries_preserve_timezone_bigint_and_null_answer(admin_pg):
    admin_pg.migrate()
    async def verify():
        async with sessions(admin_pg) as factory, factory() as db:
            db.add(UserAccount(user_id=1, nickname="합성", auth_provider="kakao", provider_user_id="support-read-test"))
            db.add(Topic(topic_id=1, topic_code="anime", topic_name="애니", is_active=True))
            await db.flush()
            db.add(Content(content_id=cases.BIG_ID, topic_id=1, title="실제 DB 타입", body=None, production_type="hybrid"))
            await db.flush()
            db.add(ContentReport(report_id=cases.BIG_ID, user_id=1, content_id=cases.BIG_ID, reason_code="rights",
                detail="문자열 본문", created_at=cases.AT, updated_at=cases.AT))
            db.add(Inquiry(inquiry_id=1, user_id=1, inquiry_type="bug", detail="문의 본문", status="answered",
                created_at=cases.AT, updated_at=cases.AT))
            await db.commit()
            page = await service.list_support(db, AdminSupportQuery(), now=cases.NOW)
            assert page.total == 2 and page.statistics.all == 2
            detail = await service.get_report(db, cases.BIG_ID)
            assert detail.record_id == detail.content.content_id == str(cases.BIG_ID)
            assert detail.received_at.tzinfo is not None and detail.updated_at.microsecond == cases.AT.microsecond
            assert detail.content.production_type == "hybrid" and not detail.content.ai_generated
            assert detail.content.body is None
            inquiry = await service.get_inquiry(db, 1)
            assert inquiry.raw_status == "answered" and inquiry.answer is None and inquiry.answer_availability == "unavailable"
    run_async(verify())
