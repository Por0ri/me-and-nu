"""Opt-in integration tests using real PostgreSQL, migrations and permission providers.

Run scripts/run_admin_postgres_checks.py for a disposable local pgvector container.
An explicit ADMIN_POSTGRES_TEST_URL is required; the application DATABASE_URL is
never used for test storage. Every test owns, and removes, a random schema.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
import re
import sys
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.exceptions import ApiError
from app.db.migration_policy import SERVICE_SCHEMA_REVISION
from app.models import AdminAccount, AdminActionLog, AgentRun, Content, Draft, Topic, UserAccount
from app.repositories import admin_content_review_repository as review_repo
from app.schemas.admin_content_review import AdminContentReviewRequest, AdminContentReviewScope
from app.services import admin_content_review_service as review_service


PARENT = "d7b3e9a1c452"
STAMP = datetime(2026, 11, 18, 9, 12, 34, 123456, timezone(timedelta(hours=9)))


def run_async(task):
    # Psycopg's async driver needs a selector loop on Windows.
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(task)
    return asyncio.run(task)


@pytest.fixture
def admin_pg():
    value = os.environ.get("ADMIN_POSTGRES_TEST_URL")
    if not value:
        pytest.skip("requires an explicitly selected disposable local admin_be_test DB")
    url = make_url(value)
    if (url.drivername != "postgresql+psycopg" or url.host not in {"127.0.0.1", "localhost"}
            or not re.fullmatch(r"admin_be_test(?:_[a-z0-9]+)?", url.database or "") or url.query):
        pytest.fail("Only an explicitly selected local admin_be_test PostgreSQL database is accepted")
    owner = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    schema = "admin_integration_" + uuid4().hex
    with owner.begin() as db:
        assert db.scalar(text("SELECT current_database()")) == url.database
        assert db.scalar(text("SELECT to_regclass('public.alembic_version')")) is None, "Use a fresh test DB"
        db.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema},public -clock_timeout=5000 -cstatement_timeout=15000"})
    engine = create_engine(scoped, poolclass=NullPool, connect_args={"connect_timeout": 5})

    def migrate(target=SERVICE_SCHEMA_REVISION, *, downgrade=False):
        config = Config()
        config.set_main_option("script_location", "alembic")
        with engine.begin() as db:
            config.attributes["connection"] = db
            (command.downgrade if downgrade else command.upgrade)(config, target)

    try:
        yield SimpleNamespace(engine=engine, url=scoped, migrate=migrate, schema=schema)
    finally:
        engine.dispose()
        assert re.fullmatch(r"admin_integration_[0-9a-f]{32}", schema)
        with owner.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        owner.dispose()


@asynccontextmanager
async def sessions(state):
    engine = create_async_engine(state.url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def populate(state):
    state.migrate()
    with Session(state.engine) as db, db.begin():
        for user_id, role in ((7, "admin"), (8, "operator"), (9, "viewer"), (10, "operator")):
            db.add(UserAccount(user_id=user_id, nickname=f"test-{role}-{user_id}", auth_provider="kakao",
                               provider_user_id=f"isolated-{user_id}", onboarding_completed_at=STAMP))
        db.add(Topic(topic_id=1, topic_code="anime", topic_name="애니메이션", is_active=True))
        db.flush()
        db.add_all([AdminAccount(admin_account_id=user_id + 100, user_id=user_id, role=role)
                    for user_id, role in ((7, "admin"), (8, "operator"), (9, "viewer"), (10, "operator"))])
        db.add(AgentRun(agent_run_id=1, topic_id=1, agent_code="pg-integration", agent_version="test",
                        status="succeeded", input_payload={}))
        db.flush()
        db.add(Draft(draft_id=1, agent_run_id=1, topic_id=1, production_type="ai", status="checking",
                     updated_at=STAMP, factcheck_result={"evidence": "preserved"}))
        db.flush()
        db.add(Content(content_id=1, source_draft_id=1, topic_id=1, production_type="ai", title="합성 검수 콘텐츠",
                       status="pending_review", judgment_status="needs_review", updated_at=STAMP,
                       ai_judgment_basis={"evidence": "preserved"}))


def request(**changes):
    return AdminContentReviewRequest(**{
        "requestId": str(uuid4()), "action": "approve", "rationale": "PostgreSQL 격리 검증",
        "expectedStatus": "pending_review", "expectedJudgmentStatus": "needs_review",
        "expectedUpdatedAt": STAMP.isoformat(), **changes,
    })


async def apply(factory, payload, *, user_id=8, content_id=1, expected_scope=None):
    from app.services.admin_review_permission_provider import DatabaseAdminReviewPermissionProvider
    async with factory() as db:
        return await review_service.review_content(db, content_id, payload, user_id=user_id,
            permissions=DatabaseAdminReviewPermissionProvider(),
            expected_scope=expected_scope or AdminContentReviewScope(actor_user_id=user_id, admin_account_id=user_id + 100))


def counts(state):
    with state.engine.connect() as db:
        content = db.execute(text("SELECT status, judgment_status FROM content WHERE content_id=1")).one()
        draft = db.scalar(text("SELECT status FROM draft WHERE draft_id=1"))
        audits = db.scalar(text("SELECT count(*) FROM admin_action_log WHERE target_type='content'"))
        return (*content, draft, audits)


def test_migration_preserves_members_and_protects_admin_history(admin_pg):
    admin_pg.migrate(PARENT)
    with admin_pg.engine.begin() as db:
        db.execute(text("INSERT INTO user_account(user_id,nickname,auth_provider,provider_user_id) "
                        "VALUES (7,'기존 회원','kakao','existing-member')"))
    admin_pg.migrate()
    admin_pg.migrate()  # Repeated deployment is a no-op.
    with admin_pg.engine.connect() as db:
        assert db.scalar(text("SELECT nickname FROM user_account WHERE user_id=7")) == "기존 회원"
        assert db.scalar(text("SELECT count(*) FROM admin_account")) == 0
        assert db.scalar(text("SELECT count(*) FROM admin_action_log")) == 0
    admin_pg.migrate(PARENT, downgrade=True)
    admin_pg.migrate()
    with admin_pg.engine.begin() as db:
        db.execute(text("INSERT INTO admin_account(user_id,role) VALUES (7,'admin')"))
    with pytest.raises(DBAPIError, match="Refusing to discard"):
        admin_pg.migrate(PARENT, downgrade=True)
    with admin_pg.engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == SERVICE_SCHEMA_REVISION
        assert db.scalar(text("SELECT count(*) FROM admin_account")) == 1


def test_postgres_enforces_one_active_grant_even_across_roles(admin_pg):
    populate(admin_pg)
    with admin_pg.engine.begin() as db:
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(text("INSERT INTO admin_account(user_id,role) VALUES (8,'admin')"))
        db.execute(text("UPDATE admin_account SET revoked_at=now() WHERE user_id=8"))
        db.execute(text("INSERT INTO admin_account(user_id,role) VALUES (8,'viewer')"))
        assert db.scalar(text("SELECT count(*) FROM admin_account WHERE user_id=8")) == 2
        assert db.scalar(text("SELECT count(*) FROM admin_account WHERE user_id=8 AND revoked_at IS NULL")) == 1


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_real_provider_atomic_review_microseconds_and_replay(admin_pg, action):
    populate(admin_pg)
    with admin_pg.engine.begin() as db:
        db.execute(text("UPDATE content SET published_at=:stamp"), {"stamp": STAMP})
        persisted = db.scalar(text("SELECT updated_at FROM content WHERE content_id=1"))
        assert persisted == STAMP and persisted.microsecond == 123456 and persisted.tzinfo is not None
    payload = request(action=action)

    async def scenario():
        async with sessions(admin_pg) as factory:
            result = await apply(factory, payload)
            replay = await apply(factory, payload)
            assert result.replayed is False and replay.replayed is True
            assert result.audit_log_id == replay.audit_log_id
            assert result.published_at == STAMP and replay.published_at == STAMP
            assert result.updated_at.tzinfo is not None and result.published_at.tzinfo is not None
            assert result.updated_at == replay.updated_at
            for field in ("updatedAt", "publishedAt"):
                assert datetime.fromisoformat(result.model_dump(mode="json", by_alias=True)[field].replace("Z", "+00:00")).tzinfo is not None
            with pytest.raises(ApiError) as conflict:
                await apply(factory, payload.model_copy(update={"rationale": "다른 사유"}))
            assert (conflict.value.status_code, conflict.value.code) == (409, "REVIEW_REQUEST_CONFLICT")
    run_async(scenario())
    assert counts(admin_pg) == (("active", "confirmed", "published", 1) if action == "approve"
                                else ("hidden", "failed", "discarded", 1))


def test_microsecond_stale_version_is_rejected(admin_pg):
    populate(admin_pg)
    async def scenario():
        async with sessions(admin_pg) as factory:
            with pytest.raises(ApiError) as stale:
                await apply(factory, request(expectedUpdatedAt=STAMP.replace(microsecond=123455).isoformat()))
            assert stale.value.status_code == 409
    run_async(scenario())
    assert counts(admin_pg) == ("pending_review", "needs_review", "checking", 0)


@pytest.mark.parametrize("user_id", [9, 11])
def test_viewer_or_missing_grant_cannot_write(admin_pg, user_id):
    populate(admin_pg)
    async def scenario():
        async with sessions(admin_pg) as factory:
            with pytest.raises(ApiError) as denied:
                await apply(factory, request(), user_id=user_id)
            assert denied.value.status_code == 403
    run_async(scenario())
    assert counts(admin_pg) == ("pending_review", "needs_review", "checking", 0)


def test_same_request_concurrent_calls_commit_once(admin_pg):
    populate(admin_pg)
    payload = request()
    async def scenario():
        async with sessions(admin_pg) as factory:
            first, second = await asyncio.gather(apply(factory, payload), apply(factory, payload))
            assert first.audit_log_id == second.audit_log_id
            assert {first.replayed, second.replayed} == {False, True}
    run_async(scenario())
    assert counts(admin_pg) == ("active", "confirmed", "published", 1)


def test_two_operators_opposite_actions_have_one_winner(admin_pg):
    populate(admin_pg)
    async def scenario():
        async with sessions(admin_pg) as factory:
            results = await asyncio.gather(apply(factory, request(), user_id=8),
                                           apply(factory, request(action="reject"), user_id=10), return_exceptions=True)
            success = [value for value in results if not isinstance(value, Exception)]
            errors = [value for value in results if isinstance(value, Exception)]
            assert len(success) == len(errors) == 1
            assert isinstance(errors[0], ApiError) and errors[0].status_code == 409
    run_async(scenario())
    assert counts(admin_pg) in {("active", "confirmed", "published", 1), ("hidden", "failed", "discarded", 1)}


def test_audit_insert_database_failure_rolls_back_content_and_draft(admin_pg):
    populate(admin_pg)
    with admin_pg.engine.begin() as db:
        db.execute(text("""CREATE FUNCTION fail_test_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected audit write failure'; END $$"""))
        db.execute(text("CREATE TRIGGER test_audit_failure BEFORE INSERT ON admin_action_log "
                        "FOR EACH ROW EXECUTE FUNCTION fail_test_audit()"))
    payload = request()
    async def scenario():
        async with sessions(admin_pg) as factory:
            with pytest.raises(ApiError) as failure:
                await apply(factory, payload)
            assert failure.value.status_code == 503
            assert counts(admin_pg) == ("pending_review", "needs_review", "checking", 0)
            with admin_pg.engine.begin() as db:
                db.execute(text("DROP TRIGGER test_audit_failure ON admin_action_log"))
            assert (await apply(factory, payload)).replayed is False
    run_async(scenario())
    assert counts(admin_pg) == ("active", "confirmed", "published", 1)


def test_committed_response_loss_reconciles_original_request(admin_pg):
    populate(admin_pg)
    payload = request(action="reject")
    async def scenario():
        async with sessions(admin_pg) as factory:
            await apply(factory, payload)  # Simulate losing the result after the real commit.
            replay = await apply(factory, payload)
            assert replay.replayed and replay.audit_log_id > 0 and replay.status == "hidden"
    run_async(scenario())
    assert counts(admin_pg) == ("hidden", "failed", "discarded", 1)


def test_deferred_commit_failure_rolls_back_all_changes(admin_pg):
    populate(admin_pg)
    with admin_pg.engine.begin() as db:
        db.execute(text("""CREATE FUNCTION fail_test_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected deferred commit failure'; END $$"""))
        db.execute(text("CREATE CONSTRAINT TRIGGER test_commit_failure AFTER INSERT ON admin_action_log "
                        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_test_commit()"))
    payload = request()
    async def scenario():
        async with sessions(admin_pg) as factory:
            with pytest.raises(ApiError) as failure:
                await apply(factory, payload)
            assert failure.value.status_code == 503
            assert counts(admin_pg) == ("pending_review", "needs_review", "checking", 0)
            with admin_pg.engine.begin() as db:
                db.execute(text("DROP TRIGGER test_commit_failure ON admin_action_log"))
            assert (await apply(factory, payload)).replayed is False
    run_async(scenario())
    assert counts(admin_pg) == ("active", "confirmed", "published", 1)


def test_real_postgres_lock_timeout_returns_conflict_and_can_retry(admin_pg):
    populate(admin_pg)
    payload = request()
    async def scenario():
        async with sessions(admin_pg) as factory:
            async with factory() as blocker, blocker.begin():
                await blocker.execute(select(Content.content_id).where(Content.content_id == 1).with_for_update())
                async with factory() as db:
                    # Session setting, deliberately outside the transaction owned by review.
                    await db.execute(text("SET lock_timeout='100ms'"))
                    await db.commit()
                    from app.services.admin_review_permission_provider import DatabaseAdminReviewPermissionProvider
                    with pytest.raises(ApiError) as conflict:
                        await review_service.review_content(db, 1, payload, user_id=8,
                            permissions=DatabaseAdminReviewPermissionProvider(),
                            expected_scope=AdminContentReviewScope(actor_user_id=8, admin_account_id=108))
                    assert (conflict.value.status_code, conflict.value.code) == (409, "REVIEW_STATE_CONFLICT")
                assert counts(admin_pg) == ("pending_review", "needs_review", "checking", 0)
            assert (await apply(factory, payload)).replayed is False
    run_async(scenario())
    assert counts(admin_pg) == ("active", "confirmed", "published", 1)


def test_revocation_blocks_old_replay_and_new_action(admin_pg):
    from app.services.admin_grant_service import grant_admin, revoke_admin
    populate(admin_pg)
    payload = request()
    async def scenario():
        async with sessions(admin_pg) as factory:
            await apply(factory, payload)
            async with factory() as db:
                await revoke_admin(db, actor_user_id=7, admin_account_id=108, rationale="격리 회수 검증")
            for attempted in (payload, request(action="reject")):
                with pytest.raises(ApiError) as denied:
                    await apply(factory, attempted)
                assert denied.value.status_code == 403
            async with factory() as db:
                replacement = await grant_admin(db, actor_user_id=7, target_user_id=8,
                                                role="operator", rationale="새 권한 부여 검증")
            assert replacement["adminAccountId"] != 108
            with pytest.raises(ApiError) as old_grant_scope:
                await apply(factory, payload)
            assert old_grant_scope.value.code == "REVIEW_SCOPE_CHANGED"
    run_async(scenario())
    assert counts(admin_pg) == ("active", "confirmed", "published", 1)


def test_runtime_http_pending_review_cannot_cross_regrant_scope(admin_pg, monkeypatch):
    """No original commit: an unchanged content version must not adopt a new grant."""
    from httpx import ASGITransport, AsyncClient
    from app.api.session_store import get_session_store
    from app.api.v1.admin.review_router import get_review_db
    from app.core.config import settings
    from app.core.security import csrf_token_for_session, hash_token
    from app.db.session import get_db
    from app.main import create_app
    from app.models import AuthSession
    from app.services.admin_grant_service import grant_admin, revoke_admin

    populate(admin_pg)
    for name, value in {"session_backend": "postgres", "redis_url": None, "enable_dev_auth_bypass": False,
                        "session_cookie_secure": False, "frontend_origins": "http://localhost:3000"}.items():
        monkeypatch.setattr(settings, name, value)
    token = "scope-pending-" + uuid4().hex
    now = datetime.now(timezone.utc)
    with Session(admin_pg.engine) as db, db.begin():
        db.add(AuthSession(user_id=8, session_state="active", session_token_hash=hash_token(token),
            csrf_token_hash=hash_token(csrf_token_for_session(token)), created_at=now,
            last_accessed_at=now, expires_at=now + timedelta(hours=1)))
    with admin_pg.engine.connect() as db:
        before_content = dict(db.execute(select(Content.__table__)).mappings().one())
        before_draft = dict(db.execute(select(Draft.__table__)).mappings().one())

    async def scenario():
        async with sessions(admin_pg) as factory:
            async def isolated_db():
                async with factory() as db:
                    yield db
            app = create_app(enable_dev_api=False)
            app.dependency_overrides[get_db] = isolated_db
            app.dependency_overrides[get_review_db] = isolated_db
            app.dependency_overrides[get_session_store] = lambda: None
            headers = {"Cookie": f"{settings.session_cookie_name}={token}", "Origin": "http://localhost:3000",
                "X-CSRF-Token": csrf_token_for_session(token)}
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
                path = "/api/v1/admin/contents/1"
                original = (await client.get(path, headers=headers)).json()
                assert original["review"]["scope"] == {"actorUserId": 8, "adminAccountId": 108}
                payload = request(expectedUpdatedAt=original["updatedAt"]).model_dump(mode="json", by_alias=True)
                assert len(payload) == 6
                async with factory() as db:
                    await revoke_admin(db, actor_user_id=7, admin_account_id=108, rationale="격리 원 권한 회수")
                async with factory() as db:
                    replacement = await grant_admin(db, actor_user_id=7, target_user_id=8,
                        role="operator", rationale="격리 동일 회원 새 권한")
                assert replacement["adminAccountId"] != 108
                fresh = await client.get(path, headers=headers)
                assert fresh.status_code == 200
                assert fresh.json()["review"] == {"allowedActions": ["approve", "reject"], "disabledReason": None,
                    "scope": {"actorUserId": 8, "adminAccountId": replacement["adminAccountId"]}}
                assert fresh.json()["updatedAt"] == original["updatedAt"]
                response = await client.post(path + "/review", json=payload, headers={**headers,
                    "X-Admin-Review-Actor": "8", "X-Admin-Review-Grant": "108"})
                assert response.status_code == 409 and response.json()["code"] == "REVIEW_SCOPE_CHANGED"
    run_async(scenario())
    assert counts(admin_pg) == ("pending_review", "needs_review", "checking", 0)
    with admin_pg.engine.connect() as db:
        assert dict(db.execute(select(Content.__table__)).mappings().one()) == before_content
        assert dict(db.execute(select(Draft.__table__)).mappings().one()) == before_draft


def test_review_holds_permission_lock_until_commit_before_revoke(admin_pg, monkeypatch):
    from app.services.admin_grant_service import revoke_admin
    populate(admin_pg)
    payload = request(action="reject")
    original = review_repo.lock_content
    async def scenario():
        reached = asyncio.Event()
        release = asyncio.Event()
        async def pause_after_grant(db, content_id):
            reached.set()
            await release.wait()
            return await original(db, content_id)
        monkeypatch.setattr(review_repo, "lock_content", pause_after_grant)
        async with sessions(admin_pg) as factory:
            pending_review = asyncio.create_task(apply(factory, payload))
            await asyncio.wait_for(reached.wait(), timeout=5)
            async def revoke():
                async with factory() as db:
                    return await revoke_admin(db, actor_user_id=7, admin_account_id=108, rationale="검수 경합 회수")
            pending_revoke = asyncio.create_task(revoke())
            # Confirm a real PostgreSQL waiter instead of guessing from a sleep.
            for _ in range(100):
                async with factory() as db:
                    blocked = await db.scalar(text("SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname=current_database() AND wait_event_type='Lock' AND pid<>pg_backend_pid()"))
                if blocked:
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("Revocation never waited on the review transaction's lock")
            assert not pending_revoke.done()
            release.set()
            result, _ = await asyncio.wait_for(asyncio.gather(pending_review, pending_revoke), timeout=10)
            assert result.status == "hidden"
            with pytest.raises(ApiError) as denied:
                await apply(factory, payload)
            assert denied.value.status_code == 403
    run_async(scenario())
    assert counts(admin_pg) == ("hidden", "failed", "discarded", 1)


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_runtime_http_get_review_audit_and_revocation_e2e(admin_pg, monkeypatch, action):
    from httpx import ASGITransport, AsyncClient
    from app.api.session_store import get_session_store
    from app.api.v1.admin.review_router import get_review_db
    from app.core.config import settings
    from app.core.security import csrf_token_for_session, hash_token
    from app.db.session import get_db
    from app.main import create_app
    from app.models import AuthSession
    from app.services.admin_grant_service import revoke_admin

    populate(admin_pg)
    for name, value in {"session_backend": "postgres", "redis_url": None, "enable_dev_auth_bypass": False,
                        "session_cookie_secure": False, "frontend_origins": "http://localhost:3000"}.items():
        monkeypatch.setattr(settings, name, value)
    now = datetime.now(timezone.utc)
    tokens = {8: "pg-operator-" + uuid4().hex, 9: "pg-viewer-" + uuid4().hex,
              10: "pg-other-operator-" + uuid4().hex}
    with Session(admin_pg.engine) as db, db.begin():
        for user_id, token in tokens.items():
            db.add(AuthSession(user_id=user_id, session_state="active", session_token_hash=hash_token(token),
                               csrf_token_hash=hash_token(csrf_token_for_session(token)), created_at=now,
                               last_accessed_at=now, expires_at=now + timedelta(hours=1)))

    async def scenario():
        async with sessions(admin_pg) as factory:
            async def isolated_db():
                async with factory() as db:
                    yield db
            app = create_app(enable_dev_api=False)
            app.dependency_overrides[get_db] = isolated_db
            app.dependency_overrides[get_review_db] = isolated_db
            app.dependency_overrides[get_session_store] = lambda: None
            # Runtime router, real session authentication, real CSRF and real
            # DB permission providers; only the database/session-store wiring is scoped.
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
                path = "/api/v1/admin/contents/1"
                assert (await client.get(path)).status_code == 401
                headers = lambda user_id: {
                    "Cookie": f"{settings.session_cookie_name}={tokens[user_id]}",
                    "Origin": "http://localhost:3000", "X-CSRF-Token": csrf_token_for_session(tokens[user_id]),
                    "X-Admin-Review-Actor": str(user_id), "X-Admin-Review-Grant": str(user_id + 100),
                }
                viewer = await client.get(path, headers=headers(9))
                assert viewer.status_code == 200, viewer.text
                assert viewer.json()["review"]["allowedActions"] == []
                payload = request(action=action).model_dump(mode="json", by_alias=True)
                assert len(payload) == 6
                assert (await client.post(path + "/review", json=payload, headers=headers(9))).status_code == 403
                detail = await client.get(path, headers=headers(8))
                assert detail.status_code == 200, detail.text
                body = detail.json()
                assert body["review"] == {"allowedActions": ["approve", "reject"], "disabledReason": None,
                    "scope": {"actorUserId": 8, "adminAccountId": 108}}
                assert datetime.fromisoformat(body["updatedAt"].replace("Z", "+00:00")) == STAMP
                assert ".123456" in body["updatedAt"]
                switched = await client.post(path + "/review", json=payload,
                    headers={**headers(10), "X-Admin-Review-Actor": "8", "X-Admin-Review-Grant": "108"})
                assert switched.status_code == 409 and switched.json()["code"] == "REVIEW_SCOPE_CHANGED"
                assert counts(admin_pg) == ("pending_review", "needs_review", "checking", 0)
                # Conflict -> latest detail -> an explicitly new review intent.
                with admin_pg.engine.begin() as db:
                    db.execute(text("UPDATE content SET updated_at=:stamp"), {"stamp": STAMP + timedelta(microseconds=1)})
                payload["expectedUpdatedAt"] = body["updatedAt"]
                stale = await client.post(path + "/review", json=payload, headers=headers(8))
                assert stale.status_code == 409 and stale.json()["code"] == "REVIEW_STATE_CONFLICT"
                refreshed = (await client.get(path, headers=headers(8))).json()
                assert ".123457" in refreshed["updatedAt"]
                payload.update(requestId=str(uuid4()), expectedUpdatedAt=refreshed["updatedAt"])
                succeeded = await client.post(path + "/review", json=payload, headers=headers(8))
                assert succeeded.status_code == 200, succeeded.text
                result = succeeded.json()
                assert result["auditLogId"] > 0 and result["replayed"] is False
                assert datetime.fromisoformat(result["updatedAt"].replace("Z", "+00:00")).tzinfo is not None
                if action == "approve":
                    assert datetime.fromisoformat(result["publishedAt"].replace("Z", "+00:00")).tzinfo is not None
                else:
                    assert result["publishedAt"] is None
                replay = (await client.post(path + "/review", json=payload, headers=headers(8))).json()
                assert replay == {**result, "replayed": True}
                fresh = (await client.get(path, headers=headers(8))).json()
                assert fresh["status"] == result["status"] and fresh["updatedAt"] == result["updatedAt"]
                assert fresh["review"]["allowedActions"] == []
                assert fresh["review"]["scope"] == {"actorUserId": 8, "adminAccountId": 108}
                async with factory() as db:
                    await revoke_admin(db, actor_user_id=7, admin_account_id=108, rationale="HTTP 검증 후 회수")
                assert (await client.get(path, headers=headers(8))).status_code == 403
                assert (await client.post(path + "/review", json=payload, headers=headers(8))).status_code == 403
            with admin_pg.engine.connect() as db:
                audit = db.execute(select(AdminActionLog).where(AdminActionLog.admin_action_log_id == result["auditLogId"])).mappings().one()
                assert audit["after_value"]["request_id"] == payload["requestId"]
                assert audit["after_value"]["response"]["status"] == result["status"]
    run_async(scenario())
    assert counts(admin_pg) == (("active", "confirmed", "published", 1) if action == "approve"
                                else ("hidden", "failed", "discarded", 1))


def test_real_grant_bootstrap_concurrent_grant_revoke_and_regrant(admin_pg):
    from app.services.admin_grant_service import bootstrap_admin, grant_admin, revoke_admin
    admin_pg.migrate()
    with Session(admin_pg.engine) as db, db.begin():
        db.add_all([UserAccount(user_id=user_id, nickname=f"grant-{user_id}", auth_provider="kakao",
                               provider_user_id=f"grant-{user_id}") for user_id in (7, 8)])
    async def scenario():
        async with sessions(admin_pg) as factory:
            async with factory() as db:
                initial = await bootstrap_admin(db, target_user_id=7, rationale="격리 최초 관리자")
            async def grant(role):
                async with factory() as db:
                    return await grant_admin(db, actor_user_id=7, target_user_id=8, role=role, rationale="동시 부여")
            results = await asyncio.gather(grant("operator"), grant("viewer"), return_exceptions=True)
            winners = [value for value in results if isinstance(value, dict)]
            errors = [value for value in results if isinstance(value, ApiError)]
            assert len(winners) == len(errors) == 1 and errors[0].status_code == 409
            old_id = winners[0]["adminAccountId"]
            async with factory() as db:
                await revoke_admin(db, actor_user_id=7, admin_account_id=old_id, rationale="역할 변경 전 회수")
            replacement = await grant("operator")
            assert replacement["adminAccountId"] != old_id
            async with factory() as db:
                with pytest.raises(ApiError) as last_admin:
                    await revoke_admin(db, actor_user_id=7, admin_account_id=initial["adminAccountId"], rationale="마지막 관리자")
                assert last_admin.value.code == "ADMIN_LAST_MANAGER"
    run_async(scenario())
    with admin_pg.engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM admin_account WHERE user_id=8")) == 2
        assert db.scalar(text("SELECT count(*) FROM admin_account WHERE user_id=8 AND revoked_at IS NULL")) == 1
        assert db.scalar(text("SELECT count(*) FROM admin_action_log")) == 4


@pytest.mark.parametrize("first", ["admin", "importer"])
def test_admin_review_and_importer_share_row_locks_and_preserve_decisions(admin_pg, tmp_path, monkeypatch, first):
    from app.seeds import carousel_contents as seed
    from test_carousel_import import make_raw_bundle
    from test_carousel_postgres import _live_bundle
    populate(admin_pg)
    # Replace the synthetic row with a real imported bundle and its real slides,
    # sources, draft, agent run and immutable digest.
    with admin_pg.engine.begin() as db:
        for table in ("content", "draft", "agent_run"):
            db.execute(text(f"DELETE FROM {table}"))
    raw = make_raw_bundle(tmp_path)
    raw["items"][0]["package"]["menu_work_ids"] = []
    pending, approved = _live_bundle(tmp_path, raw)
    original_lock = review_repo.lock_content
    original_verify = seed._verify_stored_copy

    async def scenario():
        async with sessions(admin_pg) as factory:
            async def run_import(bundle):
                async with factory() as db, db.begin():
                    return await seed.import_bundle(db, bundle, apply=True)
            imported = await run_import(pending)
            content_id = imported["items"][0]["content_id"]
            assert content_id == 1
            async with factory() as db:
                stamp = await db.scalar(select(Content.updated_at).where(Content.content_id == content_id))
            payload = request(action="reject", expectedUpdatedAt=stamp.isoformat())
            reached, release = asyncio.Event(), asyncio.Event()

            if first == "admin":
                async def paused_lock(db, content_id):
                    row = await original_lock(db, content_id)
                    reached.set()
                    await release.wait()
                    return row
                monkeypatch.setattr(review_repo, "lock_content", paused_lock)
                leader = asyncio.create_task(apply(factory, payload, content_id=content_id))
                await asyncio.wait_for(reached.wait(), timeout=5)
                follower = asyncio.create_task(run_import(approved))
            else:
                async def paused_verify(*args):
                    await original_verify(*args)
                    reached.set()
                    await release.wait()
                monkeypatch.setattr(seed, "_verify_stored_copy", paused_verify)
                leader = asyncio.create_task(run_import(approved))
                await asyncio.wait_for(reached.wait(), timeout=5)
                follower = asyncio.create_task(apply(factory, payload, content_id=content_id))

            for _ in range(100):
                async with factory() as db:
                    blocked = await db.scalar(text("SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname=current_database() AND wait_event_type='Lock' AND pid<>pg_backend_pid()"))
                if blocked:
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("The competing writer did not wait on PostgreSQL row locks")
            assert not follower.done()
            release.set()
            results = await asyncio.wait_for(asyncio.gather(leader, follower, return_exceptions=True), timeout=10)
            assert not isinstance(results[0], Exception), results[0]
            if first == "admin":
                assert isinstance(results[1], seed.CarouselImportError) and "Administrator-reviewed" in str(results[1])
                assert counts(admin_pg) == ("hidden", "failed", "discarded", 1)
                repeated = await run_import(pending)
                assert repeated["unchanged"] == 1 and repeated["items"][0]["admin_review_protected"] is True
                assert counts(admin_pg) == ("hidden", "failed", "discarded", 1)
            else:
                assert isinstance(results[1], ApiError) and results[1].status_code == 409
                assert counts(admin_pg) == ("active", "confirmed", "published", 0)
    run_async(scenario())
