from datetime import datetime, timezone
from time import time_ns
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.api import deps
from app.api.dev import auth as dev_auth
from app.core.config import settings
from app.core.redis import get_redis
from app.db.session import get_db
from app.main import create_app
from app.models import AuthSession, UserAccount
from app.services import onboarding_service


class MemoryRedis:
    def __init__(self):
        self.values: dict[str, tuple[str, int]] = {}
        self.get = AsyncMock(side_effect=self._get)
        self.set = AsyncMock(side_effect=self._set)
        self.delete = AsyncMock(side_effect=self._delete)

    async def _get(self, key):
        entry = self.values.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at <= time_ns() // 1_000_000:
            self.values.pop(key, None)
            return None
        return value

    async def _set(self, key, value, *, nx=False, pxat):
        if nx and await self._get(key) is not None:
            return None
        self.values[key] = (value, pxat)
        return True

    async def _delete(self, key):
        return int(self.values.pop(key, None) is not None)


@pytest.fixture
def redis_auth_environment(monkeypatch):
    monkeypatch.setattr(settings, "session_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", None)
    monkeypatch.setattr(settings, "enable_dev_auth_bypass", False)
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    user = UserAccount(
        user_id=23,
        email="local@example.com",
        nickname="공룡",
        signup_channel="consumer",
        auth_provider="local",
        account_status="active",
    )
    database = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
        execute=AsyncMock(side_effect=AssertionError("Unexpected SQL session fallback")),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    redis = MemoryRedis()
    find_user = AsyncMock(return_value=user)
    monkeypatch.setattr(deps, "get_user_by_id", find_user)
    monkeypatch.setattr(dev_auth, "authenticate_user", AsyncMock(return_value=user))

    async def override_database():
        yield database

    api = create_app(enable_dev_api=True)
    api.dependency_overrides[get_db] = override_database
    api.dependency_overrides[get_redis] = lambda: redis
    with TestClient(api, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            client=client,
            user=user,
            database=database,
            redis=redis,
            find_user=find_user,
            api=api,
        )


def anonymous_session(environment):
    response = environment.client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json()["sessionState"] == "anonymous"
    return response.json()["csrfToken"], environment.client.cookies.get(settings.session_cookie_name)


def login(environment, csrf):
    return environment.client.post(
        "/api/v1/dev/auth/login",
        json={"email": "local@example.com", "password": "password123"},
        headers={"X-CSRF-Token": csrf},
    )


def use_cookie(client, raw_token):
    client.cookies.clear()
    client.cookies.set(settings.session_cookie_name, raw_token, domain="testserver.local", path="/")


def assert_no_sql_sessions(environment):
    environment.database.execute.assert_not_awaited()
    assert all(
        not isinstance(call.args[0], AuthSession)
        for call in environment.database.add.call_args_list
    )


def assert_store_unavailable(response):
    assert response.status_code == 503
    assert response.json()["code"] == "SESSION_STORE_UNAVAILABLE"
    assert "set-cookie" not in response.headers


def test_redis_login_rotates_cookie_and_logout_prevents_replay(redis_auth_environment):
    environment = redis_auth_environment
    csrf, anonymous_token = anonymous_session(environment)
    assert len(environment.redis.values) == 1
    environment.database.commit.assert_not_awaited()
    response = login(environment, csrf)
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    authenticated_token = environment.client.cookies.get(settings.session_cookie_name)
    assert authenticated_token != anonymous_token
    assert len(environment.redis.values) == 1

    current = environment.client.get("/api/v1/auth/session")
    assert current.status_code == 200
    assert current.json()["authenticated"] is True
    assert current.json()["sessionState"] == "onboarding_pending"
    assert current.json()["csrfToken"] != csrf
    assert current.headers["cache-control"] == "private, no-store"
    assert environment.client.get("/api/v1/users/me").json()["code"] == "ONBOARDING_REQUIRED"

    use_cookie(environment.client, anonymous_token)
    assert environment.client.get("/api/v1/users/me").status_code == 401
    use_cookie(environment.client, authenticated_token)
    assert environment.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 403
    assert len(environment.redis.values) == 1
    response = environment.client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": current.json()["csrfToken"]}
    )
    assert response.status_code == 204
    assert response.content == b""
    assert not environment.redis.values
    assert environment.client.cookies.get(settings.session_cookie_name) is None
    use_cookie(environment.client, authenticated_token)
    assert environment.client.get("/api/v1/users/me").status_code == 401
    assert_no_sql_sessions(environment)


@pytest.mark.parametrize("account_status", ["disabled", "missing"])
def test_redis_cookie_still_requires_active_database_user(redis_auth_environment, account_status):
    environment = redis_auth_environment
    environment.user.onboarding_completed_at = datetime.now(timezone.utc)
    csrf, _ = anonymous_session(environment)
    assert login(environment, csrf).status_code == 200
    assert environment.client.get("/api/v1/users/me").status_code == 200
    environment.find_user.reset_mock()
    if account_status == "missing":
        environment.find_user.return_value = None
    else:
        environment.user.account_status = account_status
    response = environment.client.get("/api/v1/users/me")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"
    environment.find_user.assert_awaited_once_with(environment.database, environment.user.user_id)
    assert len(environment.redis.values) == 1
    assert_no_sql_sessions(environment)


def test_missing_cookie_does_not_lookup_redis_or_authenticate(redis_auth_environment):
    environment = redis_auth_environment
    response = environment.client.get("/api/v1/users/me")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"
    environment.redis.get.assert_not_awaited()
    environment.redis.set.assert_not_awaited()
    environment.find_user.assert_not_awaited()
    assert_no_sql_sessions(environment)


@pytest.mark.parametrize("path", ["/api/v1/auth/session", "/api/v1/users/me"])
def test_redis_read_failure_does_not_issue_anonymous_replacement(redis_auth_environment, path):
    environment = redis_auth_environment
    _, raw_token = anonymous_session(environment)
    environment.redis.get.side_effect = RedisError("Redis unavailable")
    response = environment.client.get(path)
    assert_store_unavailable(response)
    assert environment.client.cookies.get(settings.session_cookie_name) == raw_token
    assert environment.redis.set.await_count == 1
    assert_no_sql_sessions(environment)


def test_missing_redis_client_does_not_fallback_to_database(redis_auth_environment):
    environment = redis_auth_environment
    environment.api.dependency_overrides[get_redis] = lambda: None
    assert_store_unavailable(environment.client.get("/api/v1/auth/session"))
    environment.database.add.assert_not_called()
    environment.database.commit.assert_not_awaited()
    assert_no_sql_sessions(environment)


def test_redis_anonymous_write_failure_does_not_issue_cookie(redis_auth_environment):
    environment = redis_auth_environment
    environment.redis.set.side_effect = RedisError("Redis unavailable")
    assert_store_unavailable(environment.client.get("/api/v1/auth/session"))
    assert environment.client.cookies.get(settings.session_cookie_name) is None
    assert not environment.redis.values
    environment.database.commit.assert_not_awaited()
    assert_no_sql_sessions(environment)


@pytest.mark.parametrize("operation", ["set", "delete"])
def test_redis_login_storage_failure_does_not_issue_cookie(redis_auth_environment, operation):
    environment = redis_auth_environment
    csrf, anonymous_token = anonymous_session(environment)
    getattr(environment.redis, operation).side_effect = RedisError("Redis unavailable")
    assert_store_unavailable(login(environment, csrf))
    assert environment.client.cookies.get(settings.session_cookie_name) == anonymous_token
    environment.database.commit.assert_not_awaited()
    assert_no_sql_sessions(environment)


def test_login_commit_failure_removes_unissued_redis_session(redis_auth_environment):
    environment = redis_auth_environment
    csrf, anonymous_token = anonymous_session(environment)
    environment.database.commit.side_effect = RuntimeError("Commit failed")
    response = login(environment, csrf)
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "set-cookie" not in response.headers
    assert environment.client.cookies.get(settings.session_cookie_name) == anonymous_token
    assert not environment.redis.values
    environment.database.rollback.assert_awaited_once()
    assert_no_sql_sessions(environment)


def test_redis_logout_delete_failure_does_not_clear_cookie(redis_auth_environment):
    environment = redis_auth_environment
    csrf, _ = anonymous_session(environment)
    assert login(environment, csrf).status_code == 200
    current = environment.client.get("/api/v1/auth/session")
    raw_token = environment.client.cookies.get(settings.session_cookie_name)
    environment.redis.delete.side_effect = RedisError("Redis unavailable")
    environment.database.commit.reset_mock()
    response = environment.client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": current.json()["csrfToken"]}
    )
    assert_store_unavailable(response)
    assert environment.client.cookies.get(settings.session_cookie_name) == raw_token
    assert len(environment.redis.values) == 1
    environment.database.commit.assert_not_awaited()
    assert_no_sql_sessions(environment)


def prepare_onboarding(environment, monkeypatch):
    csrf, _ = anonymous_session(environment)
    assert login(environment, csrf).status_code == 200
    current = environment.client.get("/api/v1/auth/session")
    pending_token = environment.client.cookies.get(settings.session_cookie_name)
    monkeypatch.setattr(onboarding_service, "lock_user", AsyncMock(return_value=environment.user))
    monkeypatch.setattr(
        onboarding_service,
        "get_current_policies",
        AsyncMock(return_value={"terms": SimpleNamespace(policy_version="v1", is_required=True)}),
    )
    environment.database.commit.reset_mock()
    return current.json()["csrfToken"], pending_token


def onboarding_request(environment, csrf):
    return environment.client.post(
        "/api/v1/onboarding",
        json={
            "nickname": "새 이름",
            "birthDate": "2000-01-01",
            "accountType": "creator",
            "consents": [{"type": "terms", "policyVersion": "v1", "agreed": True}],
        },
        headers={"X-CSRF-Token": csrf},
    )


def test_redis_onboarding_rotates_pending_session(redis_auth_environment, monkeypatch):
    environment = redis_auth_environment
    csrf, pending_token = prepare_onboarding(environment, monkeypatch)
    response = onboarding_request(environment, csrf)
    assert response.status_code == 201
    assert response.json()["onboardingCompleted"] is True
    active_token = environment.client.cookies.get(settings.session_cookie_name)
    assert active_token != pending_token
    assert len(environment.redis.values) == 1
    current = environment.client.get("/api/v1/auth/session")
    assert current.json()["sessionState"] == "active"
    assert current.json()["user"] == {"id": 23, "accountType": "creator"}
    assert environment.client.get("/api/v1/users/me").status_code == 200
    use_cookie(environment.client, pending_token)
    assert environment.client.get("/api/v1/users/me").status_code == 401
    environment.database.commit.assert_awaited_once()
    assert_no_sql_sessions(environment)


@pytest.mark.parametrize("operation", ["set", "delete"])
def test_redis_onboarding_failure_rolls_back_without_cookie(redis_auth_environment, monkeypatch, operation):
    environment = redis_auth_environment
    csrf, pending_token = prepare_onboarding(environment, monkeypatch)
    getattr(environment.redis, operation).side_effect = RedisError("Redis unavailable")
    response = onboarding_request(environment, csrf)
    assert_store_unavailable(response)
    assert environment.client.cookies.get(settings.session_cookie_name) == pending_token
    environment.database.commit.assert_not_awaited()
    environment.database.rollback.assert_awaited_once()
    assert_no_sql_sessions(environment)


def test_onboarding_commit_failure_removes_unissued_redis_session(redis_auth_environment, monkeypatch):
    environment = redis_auth_environment
    csrf, pending_token = prepare_onboarding(environment, monkeypatch)
    environment.database.commit.side_effect = RuntimeError("Commit failed")
    response = onboarding_request(environment, csrf)
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "set-cookie" not in response.headers
    assert environment.client.cookies.get(settings.session_cookie_name) == pending_token
    assert not environment.redis.values
    environment.database.rollback.assert_awaited_once()
    assert_no_sql_sessions(environment)
