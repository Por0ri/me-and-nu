"""Run actual member statements on disposable PostgreSQL and migrated schema."""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import test_admin_members as cases
from test_admin_postgres_integration import admin_pg, run_async, sessions  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    cases.test_pagination_ties_bigint_ids_and_empty_page_share_single_snapshot,
    cases.test_search_is_literal_case_insensitive_and_filters_actual_enums,
    cases.test_withdrawal_redacts_every_personal_field_and_cannot_be_probed_by_search,
    cases.test_detail_uses_current_saved_liked_state_and_actual_onboarding_labels,
    cases.test_missing_member_is_404_and_uncollected_onboarding_is_null,
], ids=lambda scenario: scenario.__name__.removeprefix("test_"))
async def test_postgres_member_contract(admin_pg, scenario):
    meta = cases.member_tables()
    meta.create_all(admin_pg.engine)
    with admin_pg.engine.connect() as connection:
        await scenario(cases.ReadSession(connection, meta))


@pytest.mark.asyncio
async def test_count_and_page_remain_consistent_during_concurrent_commit(admin_pg):
    meta = cases.member_tables()
    meta.create_all(admin_pg.engine)
    with admin_pg.engine.begin() as writer:
        cases.member(cases.ReadSession(writer, meta), 1)

    class ConcurrentReader(cases.ReadSession):
        async def execute(self, statement):
            result = await super().execute(statement)
            if len(self.statements) == 1:
                with admin_pg.engine.begin() as writer:
                    cases.member(cases.ReadSession(writer, meta), 2)
            return result

    with admin_pg.engine.connect() as connection:
        assert connection.scalar(text("SHOW transaction_isolation")) == "read committed"
        db = ConcurrentReader(connection, meta)
        first = await cases.service.list_members(db, cases.AdminMemberQuery())
        assert first.total == len(first.items) == 1
        assert len(db.statements) == 1
        second = await cases.service.list_members(db, cases.AdminMemberQuery())
        assert second.total == len(second.items) == 2


def test_member_reads_against_actual_migration_and_async_session(admin_pg):
    from app.models import Topic, Tap, UserAccount
    admin_pg.migrate()
    with Session(admin_pg.engine) as db, db.begin():
        db.add(Topic(topic_id=1, topic_code="anime", topic_name="애니메이션", is_active=True))
        db.add(UserAccount(user_id=cases.BIG_ID, nickname="실제 schema", auth_provider="kakao", provider_user_id="private-provider-id",
                           email="private@example.test", joined_at=cases.NOW, last_login_at=cases.NOW))
        db.flush()
        db.add(Tap(user_id=cases.BIG_ID, topic_id=1))

    async def verify():
        async with sessions(admin_pg) as factory, factory() as db:
            listing = await cases.service.list_members(db, cases.AdminMemberQuery(), "private@example.test")
            detail = await cases.service.get_member(db, cases.BIG_ID)
            assert listing.total == 1 and detail.id == str(cases.BIG_ID)
            assert detail.domain == "애니메이션" and detail.masked_email == "pr***@example.test"
            assert detail.last_login_at == cases.NOW and detail.last_login_at.utcoffset() is not None
            assert detail.saved_count == detail.liked_count == 0
            assert detail.onboarding is detail.history is detail.notes is None
            assert "private-provider-id" not in detail.model_dump_json()
    run_async(verify())
