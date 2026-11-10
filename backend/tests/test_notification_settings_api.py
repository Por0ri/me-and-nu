"""Disposable PostgreSQL only: real migrations, HTTP auth/CSRF, SQL and concurrency.

NOTIFICATION_SETTINGS_TEST_DATABASE_URL must explicitly name a local
notification_settings_test database. Every test owns a generated schema.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import csrf_token_for_session, hash_token
from app.db.session import get_db
from app.main import create_app
from app.models import AuthSession, Notification, NotificationSetting

BASE = "b3e7a5c19d42"
TARGET = "91c7a2e4b630"
PATH = "/api/v1/users/me/notification-settings"


def test_routes_and_patch_schema():
    paths = create_app(enable_dev_api=False).openapi()["paths"]
    assert set(paths[PATH]) == {"get", "patch"}
    from app.schemas.notification_settings import NotificationSettingsPatch
    from pydantic import ValidationError
    assert NotificationSettingsPatch(version=1, inAppEnabled=False).in_app_enabled is False
    for value in [{}, {"version": 1}, {"version": True, "inAppEnabled": True},
                  {"version": "1", "inAppEnabled": True}, {"version": 1, "in_app_enabled": True},
                  {"version": 1, "inAppEnabled": None}, {"version": 1, "inAppEnabled": "true"},
                  {"version": 1, "nightInAppEnabled": 1}, {"version": 1, "inAppEnabled": False, "marketing": True}]:
        with pytest.raises(ValidationError):
            NotificationSettingsPatch.model_validate(value)


@pytest.fixture
def pg(monkeypatch):
    value = os.environ.get("NOTIFICATION_SETTINGS_TEST_DATABASE_URL")
    if not value:
        pytest.skip("requires an explicitly selected disposable PostgreSQL database")
    url = make_url(value)
    if url.host not in {"127.0.0.1", "localhost"} or url.database != "notification_settings_test" or url.query:
        pytest.fail("Only the disposable local notification_settings_test database is accepted")
    schema = "notification_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    with admin.begin() as db:
        assert db.scalar(text("SELECT current_database()")) == "notification_settings_test"
        db.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema},public"})
    engine = create_engine(scoped, poolclass=NullPool)

    def migrate(target, downgrade=False):
        config = Config()
        config.set_main_option("script_location", "alembic")
        with engine.begin() as db:
            config.attributes["connection"] = db
            (command.downgrade if downgrade else command.upgrade)(config, target)

    try:
        migrate(BASE)
        with engine.begin() as db:
            users = []
            for index in range(3):
                users.append(db.scalar(text("INSERT INTO user_account (nickname,auth_provider,provider_user_id,onboarding_completed_at) "
                    "VALUES (:name,'kakao',:provider,:completed) RETURNING user_id"), {
                    "name": f"설정 검증 {index}", "provider": uuid4().hex,
                    "completed": None if index == 2 else datetime.now(timezone.utc)}))
            db.execute(text("INSERT INTO notification_setting (user_id,report_result,creator_new_post,saved_resurface,marketing,updated_at) "
                "VALUES (:user,false,true,false,true,'2026-10-01T00:00:00Z')"), {"user": users[0]})
            db.execute(text("INSERT INTO consent_history (user_id,consent_type,policy_version,is_agreed) "
                            "VALUES (:user,'marketing','test-v1',true)"), {"user": users[0]})
        migrate(TARGET)
        for key, val in {"session_backend": "postgres", "redis_url": None, "enable_dev_auth_bypass": False,
                         "session_cookie_secure": False}.items():
            monkeypatch.setattr(settings, key, val)
        yield engine, scoped, migrate, users
    finally:
        engine.dispose()
        # Only the generated schema in this explicitly selected disposable database.
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def api(pg):
    engine, url, migrate, users = pg
    now = datetime.now(timezone.utc)
    tokens = ["notification-fixture-" + uuid4().hex for _ in users]
    with Session(engine) as db:
        for index, (user_id, token) in enumerate(zip(users, tokens)):
            db.add(AuthSession(user_id=user_id, session_state="onboarding_pending" if index == 2 else "active",
                session_token_hash=hash_token(token), csrf_token_hash=hash_token(csrf_token_for_session(token)),
                created_at=now, expires_at=now + timedelta(hours=1)))
        db.add(Notification(user_id=users[0], notification_type="system_notice", title="필수 공지", body="계속 확인 가능", created_at=now))
        db.commit()
    async_engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async def database():
        async with factory() as db:
            yield db
    app = create_app(enable_dev_api=False)
    app.dependency_overrides[get_db] = database
    options = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
    def client(index=0):
        result = TestClient(app, raise_server_exceptions=False, backend_options=options)
        result.cookies.set(settings.session_cookie_name, tokens[index])
        result.headers["X-CSRF-Token"] = csrf_token_for_session(tokens[index])
        return result
    with client() as active:
        yield active, client, engine, users, tokens, migrate
    app.dependency_overrides.clear()


def test_migration_defaults_backfill_and_legacy_data_preserved(pg):
    engine, _, migrate, users = pg
    with engine.connect() as db:
        rows = db.execute(text("SELECT user_id,in_app_enabled,night_in_app_enabled,version,marketing FROM notification_setting ORDER BY user_id")).all()
        assert rows == [(users[0], True, False, 1, True), (users[1], True, False, 1, False)]
        assert db.execute(text("SELECT report_result,creator_new_post,saved_resurface,updated_at FROM notification_setting WHERE user_id=:user"), {"user": users[0]}).one() == (False, True, False, datetime(2026, 10, 1, tzinfo=timezone.utc))
        assert db.scalar(text("SELECT count(*) FROM consent_history")) == 1
    # Existing onboarding's ORM constructor gets the approved PM defaults automatically.
    with Session(engine) as db:
        row = NotificationSetting(user_id=users[2]); db.add(row); db.commit(); db.refresh(row)
        assert (row.in_app_enabled, row.night_in_app_enabled, row.version, row.marketing) == (True, False, 1, False)
    for statement in ["UPDATE notification_setting SET in_app_enabled=NULL", "UPDATE notification_setting SET night_in_app_enabled=NULL",
                      "UPDATE notification_setting SET version=NULL", "UPDATE notification_setting SET version=0"]:
        with pytest.raises(IntegrityError), engine.begin() as db:
            db.execute(text(statement))
    migrate(BASE, True)
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM notification_setting")) == 3
        assert db.scalar(text("SELECT marketing FROM notification_setting WHERE user_id=:user"), {"user": users[0]}) is True
        assert db.scalar(text("SELECT count(*) FROM consent_history")) == 1
    migrate(TARGET)
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM notification_setting WHERE in_app_enabled AND NOT night_in_app_enabled AND version=1")) == 3


def test_get_patch_persist_exact_values_and_preserve_notices_and_consents(api):
    client, _, engine, users, _, _ = api
    initial = {"inAppEnabled": True, "nightInAppEnabled": False, "version": 1}
    response = client.get(PATH)
    assert response.status_code == 200 and response.json() == initial
    assert response.headers["cache-control"] == "private, no-store"
    assert client.get(PATH).json() == initial  # GET does not increment or initialize rows.
    saved = client.patch(PATH, json={"version": 1, "inAppEnabled": False, "nightInAppEnabled": True})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"inAppEnabled": False, "nightInAppEnabled": True, "version": 2}
    assert client.get(PATH).json() == saved.json()
    with Session(engine) as db:
        row = db.get(NotificationSetting, users[0])
        assert (row.in_app_enabled, row.night_in_app_enabled, row.version) == (False, True, 2)
        assert (row.report_result, row.creator_new_post, row.saved_resurface, row.marketing) == (False, True, False, True)
        assert db.scalar(text("SELECT count(*) FROM consent_history")) == 1
    inbox = client.get("/api/v1/users/me/notifications").json()
    assert len(inbox["items"]) == 1 and inbox["items"][0]["type"] == "system_notice"
    assert client.get("/api/v1/users/me/notifications/unread-count").json()["unreadCount"] == 1
    notification_id = inbox["items"][0]["notificationId"]
    assert client.patch(f"/api/v1/users/me/notifications/{notification_id}", json={"isRead": True}).status_code == 200
    assert len(client.get("/api/v1/users/me/notifications").json()["items"]) == 1
    assert client.get("/api/v1/users/me/notifications/unread-count").json()["unreadCount"] == 0


def test_invalid_requests_never_write(api):
    client, _, _, _, _, _ = api
    for body in [{}, {"version": 1}, {"version": 0, "inAppEnabled": False}, {"version": True, "inAppEnabled": False},
                 {"version": 1.0, "inAppEnabled": False}, {"version": "1", "inAppEnabled": False},
                 {"version": 1, "inAppEnabled": None}, {"version": 1, "nightInAppEnabled": "false"},
                 {"version": 1, "inAppEnabled": 0}, {"version": 1, "inAppEnabled": False, "userId": 99},
                 {"version": 1, "inAppEnabled": False, "marketing": False}, {"version": 1, "in_app_enabled": False}]:
        response = client.patch(PATH, json=body)
        assert response.status_code == 422, (body, response.text)
        assert response.json()["code"] == "VALIDATION_ERROR"
    assert client.get(PATH).json() == {"inAppEnabled": True, "nightInAppEnabled": False, "version": 1}


def test_auth_onboarding_csrf_and_other_member_isolation(api):
    client, make_client, engine, users, tokens, _ = api
    for headers in [{}, {"X-CSRF-Token": "wrong"}, {"X-CSRF-Token": csrf_token_for_session(tokens[1])},
                    {"X-CSRF-Token": csrf_token_for_session(tokens[0]), "Origin": "https://untrusted.example"}]:
        with make_client() as probe:
            probe.headers.pop("X-CSRF-Token", None)
            response = probe.patch(PATH, json={"version": 1, "inAppEnabled": False}, headers=headers)
            assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    with make_client(2) as pending:
        assert pending.get(PATH).status_code == 403
        assert pending.patch(PATH, json={"version": 1, "inAppEnabled": False}).status_code == 403
    with make_client() as anonymous:
        anonymous.cookies.clear()
        assert anonymous.get(PATH).status_code == 401
        assert anonymous.patch(PATH, json={"version": 1, "inAppEnabled": False}).status_code == 401
    assert client.patch(PATH, json={"version": 1, "inAppEnabled": False}).status_code == 200
    with make_client(1) as other:
        assert other.get(PATH, params={"userId": users[0]}).json() == {"inAppEnabled": True, "nightInAppEnabled": False, "version": 1}
        assert other.patch(PATH, json={"version": 1, "nightInAppEnabled": True, "userId": users[0]}).status_code == 422
        # A cookie switch with the old user's CSRF is rejected, even if versions coincide.
        assert other.patch(PATH, json={"version": 1, "inAppEnabled": False}, headers={"X-CSRF-Token": csrf_token_for_session(tokens[0])}).status_code == 403
    with engine.begin() as db:
        db.execute(text("UPDATE auth_session SET revoked_at=now() WHERE user_id=:user"), {"user": users[0]})
    assert client.get(PATH).status_code == 401
    assert client.patch(PATH, json={"version": 2, "inAppEnabled": True}).status_code == 401


def test_stale_and_concurrent_updates_have_exactly_one_winner(api):
    client, _, _, _, tokens, _ = api
    async def concurrent():
        # One event loop avoids Windows TestClient portal/psycopg loop interaction;
        # each HTTP request still owns a separate real PostgreSQL transaction.
        async with AsyncClient(transport=ASGITransport(app=client.app), base_url="http://testserver",
                               cookies={settings.session_cookie_name: tokens[0]},
                               headers={"X-CSRF-Token": csrf_token_for_session(tokens[0])}) as other:
            return await asyncio.wait_for(asyncio.gather(
                other.patch(PATH, json={"version": 1, "inAppEnabled": False}),
                other.patch(PATH, json={"version": 1, "nightInAppEnabled": True}),
            ), timeout=15)
    options = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
    results = asyncio.run(concurrent(), **options)
    assert sorted(response.status_code for response in results) == [200, 409]
    winner = next(response.json() for response in results if response.status_code == 200)
    assert client.get(PATH).json() == winner and winner["version"] == 2
    stale = client.patch(PATH, json={"version": 1, "inAppEnabled": False})
    assert stale.status_code == 409 and stale.json()["code"] == "NOTIFICATION_SETTINGS_CONFLICT"
    no_change = client.patch(PATH, json={"version": 2, "inAppEnabled": winner["inAppEnabled"]})
    assert no_change.status_code == 200 and no_change.json()["version"] == 3


def test_missing_row_is_not_synthesized_and_downgrade_guards_saved_values(api):
    client, _, engine, users, _, migrate = api
    assert client.patch(PATH, json={"version": 1, "inAppEnabled": True}).status_code == 200
    with pytest.raises(DBAPIError, match="Refusing to discard"):
        migrate(BASE, True)
    assert client.get(PATH).json()["version"] == 2
    with engine.begin() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == TARGET
        db.execute(text("DELETE FROM notification_setting WHERE user_id=:user"), {"user": users[0]})
    for response in [client.get(PATH), client.patch(PATH, json={"version": 2, "inAppEnabled": False})]:
        assert response.status_code == 409 and response.json()["code"] == "NOTIFICATION_SETTINGS_NOT_INITIALIZED"
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM notification_setting WHERE user_id=:user"), {"user": users[0]}) == 0


def test_upgrade_and_downgrade_preserve_checking_restoration_sessions(pg):
    engine, _, migrate, users = pg
    migrate(BASE, True)
    now = datetime.now(timezone.utc)
    token = "restoration-migration-fixture-" + uuid4().hex
    with Session(engine) as db:
        db.execute(text("UPDATE user_account SET account_status='withdrawn', withdrawn_at=:now WHERE user_id=:user"),
                   {"now": now, "user": users[0]})
        session = AuthSession(user_id=users[0], session_state="restoration_pending",
            session_token_hash=hash_token(token), csrf_token_hash=hash_token(csrf_token_for_session(token)),
            created_at=now, expires_at=now + timedelta(hours=1))
        db.add(session); db.commit(); db.refresh(session)
        session_id = session.auth_session_id
    # This database starts at the already-merged Checking revision, with real data.
    for target, downgrade in [(TARGET, False), (BASE, True), (TARGET, False)]:
        migrate(target, downgrade)
        with Session(engine) as db:
            assert db.get(AuthSession, session_id).session_state == "restoration_pending"
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == target
            assert db.scalar(text("SELECT account_status FROM user_account WHERE user_id=:user"), {"user": users[0]}) == "withdrawn"
            assert db.execute(text("SELECT report_result,creator_new_post,saved_resurface,marketing FROM notification_setting WHERE user_id=:user"),
                              {"user": users[0]}).one() == (False, True, False, True)
            assert db.scalar(text("SELECT count(*) FROM consent_history")) == 1
            if target == TARGET:
                row = db.get(NotificationSetting, users[0])
                assert (row.in_app_enabled, row.night_in_app_enabled, row.version) == (True, False, 1)


def test_withdrawal_blocks_settings_and_restoration_preserves_preferences(api):
    client, _, engine, users, _, _ = api
    saved = client.patch(PATH, json={"version": 1, "inAppEnabled": False, "nightInAppEnabled": True}).json()
    assert saved == {"inAppEnabled": False, "nightInAppEnabled": True, "version": 2}
    assert client.delete("/api/v1/users/me", params={"mode": "grace"}).status_code == 202
    assert client.get(PATH).status_code == 401
    assert client.patch(PATH, json={"version": 2, "inAppEnabled": True}).status_code == 401
    # Emulate the restricted session issued after OAuth; no real provider login.
    token = "restoration-api-fixture-" + uuid4().hex
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        db.add(AuthSession(user_id=users[0], session_state="restoration_pending",
            session_token_hash=hash_token(token), csrf_token_hash=hash_token(csrf_token_for_session(token)),
            created_at=now, expires_at=now + timedelta(hours=1)))
        db.commit()
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_name, token, domain="testserver.local", path="/")
    client.headers["X-CSRF-Token"] = csrf_token_for_session(token)
    assert client.get("/api/v1/auth/session").json()["sessionState"] == "restoration_pending"
    assert client.get(PATH).status_code == 401
    assert client.patch(PATH, json={"version": 2, "inAppEnabled": True}).status_code == 401
    assert client.post("/api/v1/auth/withdrawal-restorations").status_code == 200
    current = client.get("/api/v1/auth/session").json()
    assert current["sessionState"] == "active"
    client.headers["X-CSRF-Token"] = current["csrfToken"]
    assert client.get(PATH).json() == saved
    assert client.patch(PATH, json={"version": 2, "nightInAppEnabled": False}).json() == {
        "inAppEnabled": False, "nightInAppEnabled": False, "version": 3}
    with Session(engine) as db:
        assert db.get(NotificationSetting, users[0]).marketing is True
        assert db.scalar(text("SELECT count(*) FROM consent_history")) == 1
    assert len(client.get("/api/v1/users/me/notifications").json()["items"]) == 1
