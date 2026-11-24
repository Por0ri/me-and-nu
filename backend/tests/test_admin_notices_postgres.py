"""Migrated PostgreSQL notice requests: concurrent creates/publishes and atomic audit."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.exceptions import ApiError
from app.models import AdminAccount, AdminActionLog, Notice, UserAccount
from app.schemas.admin_notice import AdminNoticeActionRequest, AdminNoticeCreateRequest, AdminNoticeScope
from app.services import admin_notice_service as service
from app.services.admin_notice_permission_provider import DatabaseAdminNoticePermissionProvider
from test_admin_postgres_integration import STAMP, admin_pg, run_async, sessions  # noqa: F401


def seed(pg):
    pg.migrate()
    with Session(pg.engine) as db, db.begin():
        for uid, role in ((7, "admin"), (8, "operator"), (9, "viewer"), (10, "operator")):
            db.add(UserAccount(user_id=uid, nickname=f"synthetic-{uid}", auth_provider="kakao",
                               provider_user_id=f"notice-operator-{uid}", onboarding_completed_at=STAMP))
        db.flush()
        db.add_all(AdminAccount(admin_account_id=uid + 100, user_id=uid, role=role)
                   for uid, role in ((7, "admin"), (8, "operator"), (9, "viewer"), (10, "operator")))


def create_request(**changes):
    return AdminNoticeCreateRequest(**{"requestId": str(uuid4()), "action": "save_draft", "title": "Synthetic notice",
                                        "body": "SYNTHETIC_NOTICE_PRIVATE_BODY", **changes})


def action_request(detail, **changes):
    return AdminNoticeActionRequest(**{"requestId": str(uuid4()), "action": "publish", "title": "Synthetic notice",
        "body": "SYNTHETIC_NOTICE_PRIVATE_BODY", "expectedStatus": detail.status,
        "expectedUpdatedAt": detail.updated_at.isoformat(), **changes})


async def create(factory, payload, *, uid=8, now=STAMP):
    async with factory() as db:
        return await service.create_notice(db, payload, user_id=uid, permissions=DatabaseAdminNoticePermissionProvider(),
            expected_scope=AdminNoticeScope(actor_user_id=str(uid), admin_account_id=str(uid + 100)), now=now)


async def apply(factory, notice_id, payload, *, uid=8, scope=None, now=STAMP):
    async with factory() as db:
        return await service.apply_notice_action(db, notice_id, payload, user_id=uid,
            permissions=DatabaseAdminNoticePermissionProvider(),
            expected_scope=scope or AdminNoticeScope(actor_user_id=str(uid), admin_account_id=str(uid + 100)), now=now)


def counts(pg):
    with pg.engine.connect() as db:
        return tuple(db.scalar(text("SELECT count(*) FROM " + name))
                     for name in ("notice", "notification", "admin_action_log"))


@pytest.mark.parametrize("kind", ["save_draft", "publish", "schedule"])
def test_concurrent_identical_creates_allocate_one_id_and_one_audit(admin_pg, kind):
    seed(admin_pg)
    request = create_request(action=kind, scheduledAt=(STAMP + timedelta(days=1)).isoformat() if kind == "schedule" else None)
    async def scenario():
        async with sessions(admin_pg) as factory:
            first, second = await asyncio.gather(create(factory, request), create(factory, request))
            assert first.notice_id == second.notice_id and first.audit_log_id == second.audit_log_id
            assert first.updated_at == second.updated_at and first.updated_at.tzinfo
            assert {first.replayed, second.replayed} == {True, False}
            # A scheduled-create retry remains an original success after its
            # scheduled instant; validating 'future' before replay would fail it.
            retry = await create(factory, request, now=STAMP + timedelta(days=2))
            assert retry.replayed
            with pytest.raises(ApiError) as reused:
                await create(factory, request.model_copy(update={"body": "different"}))
            assert reused.value.code == "NOTICE_REQUEST_CONFLICT"
    run_async(scenario())
    assert counts(admin_pg) == (1, 4 if kind == "publish" else 0, 1)


def test_two_operators_cannot_publish_same_draft_twice(admin_pg):
    seed(admin_pg)
    async def scenario():
        async with sessions(admin_pg) as factory:
            draft = await create(factory, create_request())
            first, second = action_request(draft), action_request(draft, body="competing body")
            results = await asyncio.gather(apply(factory, int(draft.notice_id), first),
                                          apply(factory, int(draft.notice_id), second, uid=10), return_exceptions=True)
            successes = [value for value in results if not isinstance(value, Exception)]
            conflicts = [value for value in results if isinstance(value, ApiError)]
            assert len(successes) == len(conflicts) == 1
            assert conflicts[0].status_code == 409 and successes[0].status == "published"
            assert successes[0].updated_at > draft.updated_at
    run_async(scenario())
    assert counts(admin_pg) == (1, 4, 2)


def test_publish_audit_failure_rolls_back_notice_deliveries_and_can_retry(admin_pg):
    seed(admin_pg)
    request = create_request(action="publish")
    with admin_pg.engine.begin() as db:
        db.execute(text("CREATE FUNCTION fail_notice_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END $$"))
        db.execute(text("CREATE TRIGGER fail_notice_audit BEFORE INSERT ON admin_action_log FOR EACH ROW EXECUTE FUNCTION fail_notice_audit()"))
    async def scenario():
        async with sessions(admin_pg) as factory:
            with pytest.raises(ApiError) as failure:
                await create(factory, request)
            assert failure.value.status_code == 503 and counts(admin_pg) == (0, 0, 0)
            with admin_pg.engine.begin() as db: db.execute(text("DROP TRIGGER fail_notice_audit ON admin_action_log"))
            success = await create(factory, request)
            assert success.status == "published" and not success.replayed
            assert (await create(factory, request)).replayed
    run_async(scenario())
    assert counts(admin_pg) == (1, 4, 1)
    with Session(admin_pg.engine) as db:
        audit = db.scalars(select(AdminActionLog)).one()
        assert request.body not in str(audit.before_value) + str(audit.after_value) + audit.rationale


def test_microseconds_grant_and_request_scope_are_rechecked(admin_pg):
    seed(admin_pg)
    request = create_request()
    async def scenario():
        async with sessions(admin_pg) as factory:
            draft = await create(factory, request)
            with pytest.raises(ApiError) as viewer:
                await create(factory, create_request(), uid=9)
            assert viewer.value.status_code == 403
            with pytest.raises(ApiError) as stale:
                await apply(factory, int(draft.notice_id), action_request(draft,
                    expectedUpdatedAt=(draft.updated_at - timedelta(microseconds=1)).isoformat()))
            assert stale.value.status_code == 409
            with admin_pg.engine.begin() as db:
                db.execute(text("UPDATE admin_account SET revoked_at=now() WHERE admin_account_id=108"))
            with pytest.raises(ApiError) as revoked:
                await create(factory, request)
            assert revoked.value.status_code == 403
            with admin_pg.engine.begin() as db:
                db.execute(text("INSERT INTO admin_account(admin_account_id,user_id,role) VALUES(208,8,'operator')"))
            with pytest.raises(ApiError) as new_scope:
                await create(factory, request)
            assert new_scope.value.code == "NOTICE_SCOPE_CHANGED"
    run_async(scenario())
    assert counts(admin_pg) == (1, 0, 1)


def test_schedule_cancel_roundtrip_and_cross_target_request_conflict(admin_pg):
    seed(admin_pg)
    async def scenario():
        async with sessions(admin_pg) as factory:
            first = await create(factory, create_request(action="schedule", scheduledAt=(STAMP + timedelta(days=1)).isoformat()))
            second = await create(factory, create_request(action="schedule", scheduledAt=(STAMP + timedelta(days=1)).isoformat()))
            cancel = action_request(first, action="cancel_schedule", title=None, body=None)
            cancelled = await apply(factory, int(first.notice_id), cancel)
            assert cancelled.status == "draft" and cancelled.scheduled_at is None
            assert (await apply(factory, int(first.notice_id), cancel)).replayed
            with pytest.raises(ApiError) as other:
                await apply(factory, int(second.notice_id), cancel)
            assert other.value.code == "NOTICE_REQUEST_CONFLICT"
            with Session(admin_pg.engine) as db:
                assert db.get(Notice, int(second.notice_id)).status == "scheduled"
    run_async(scenario())
    assert counts(admin_pg) == (2, 0, 3)


def test_api_lock_timeout_rolls_back_and_same_request_retries(admin_pg):
    seed(admin_pg)
    request = create_request(action="publish")

    class CheckedPermissions(DatabaseAdminNoticePermissionProvider):
        async def lock_grant(self, db, *, user_id):
            assert await db.scalar(text("SHOW lock_timeout")) == "5s"
            assert await db.scalar(text("SHOW statement_timeout")) == "30s"
            return await super().lock_grant(db, user_id=user_id)

    async def scenario():
        # Explicitly disable fixture defaults: the API must impose its own bound.
        url = admin_pg.url.update_query_dict({"options":
            f"-csearch_path={admin_pg.schema},public -clock_timeout=0 -cstatement_timeout=0"})
        engine = create_async_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def submit():
            async with factory() as db:
                assert await db.scalar(text("SHOW lock_timeout")) == "0"
                assert await db.scalar(text("SHOW statement_timeout")) == "0"
                await db.rollback()
                result = await service.create_notice(db, request, user_id=8,
                    permissions=CheckedPermissions(),
                    expected_scope=AdminNoticeScope(actor_user_id="8", admin_account_id="108"), now=STAMP)
                assert await db.scalar(text("SHOW lock_timeout")) == "0"
                assert await db.scalar(text("SHOW statement_timeout")) == "0"
                return result

        try:
            with admin_pg.engine.begin() as holder:
                holder.execute(text("SELECT pg_advisory_xact_lock(-7100000001)"))
                with pytest.raises(ApiError) as failure:
                    await asyncio.wait_for(submit(), timeout=12)
                assert failure.value.status_code == 409
                assert failure.value.code == "NOTICE_STATE_CONFLICT"
                assert counts(admin_pg) == (0, 0, 0)
            success = await submit()
            assert success.status == "published" and not success.replayed
            assert (await submit()).replayed
            assert counts(admin_pg) == (1, 4, 1)
        finally:
            await engine.dispose()

    run_async(scenario())
