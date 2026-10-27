"""HTTP OAuth flows with real session/CSRF logic and isolated external stores."""

from datetime import datetime, timezone
from time import time_ns
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.api import deps
from app.api.v1.auth import oauth
from app.core.config import settings
from app.core.exceptions import ApiError
from app.core.redis import get_redis
from app.core.security import create_session_id
from app.db.session import get_db
from app.main import create_app
from app.models import UserAccount
from app.services.oauth_provider import OAuthIdentity


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.get = AsyncMock(side_effect=self._get)
        self.set = AsyncMock(side_effect=self._set)
        self.delete = AsyncMock(side_effect=self._delete)

    async def _get(self, key):
        entry = self.values.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if expiry <= time_ns() // 1_000_000:
            self.values.pop(key, None)
            return None
        return value

    async def _set(self, key, value, *, nx=False, pxat):
        if nx and await self._get(key) is not None:
            return None
        self.values[key] = value, pxat
        return True

    async def _delete(self, key):
        return int(self.values.pop(key, None) is not None)


@pytest.fixture
def environment(monkeypatch):
    for key, value in {
        "session_backend": "redis", "redis_url": None,
        "enable_dev_auth_bypass": False, "session_cookie_secure": False,
        "kakao_oauth_enabled": True, "naver_oauth_enabled": True,
        "kakao_client_id": "test-kakao", "naver_client_id": "test-naver",
        "kakao_client_secret": SecretStr("test-secret"),
        "naver_client_secret": SecretStr("test-secret"),
        "oauth_frontend_origin": "http://localhost:3000",
        "oauth_public_api_origin": "http://localhost:8000",
    }.items():
        monkeypatch.setattr(settings, key, value)
    db = SimpleNamespace(
        add=Mock(), flush=AsyncMock(),
        execute=AsyncMock(side_effect=AssertionError("Unexpected database request")),
        commit=AsyncMock(), rollback=AsyncMock(),
    )
    redis = MemoryRedis()
    attempts = {}
    user = UserAccount(
        user_id=23, auth_provider="kakao", provider_user_id="123",
        nickname="새사용자", account_status="active",
    )
    identity = OAuthIdentity("kakao", "123", None, None)
    provider = SimpleNamespace(
        build_authorization_url=Mock(
            side_effect=lambda name, state: "https://provider.example/authorize?" + urlencode({"state": state})
        ),
        exchange_identity=AsyncMock(return_value=identity),
    )

    async def create_attempt(_, *, provider, browser_session_hash, ttl_seconds):
        state = create_session_id()
        attempts[state] = provider, browser_session_hash
        return state

    async def consume_attempt(_, *, provider, browser_session_hash, raw_state):
        if attempts.get(raw_state) != (provider, browser_session_hash):
            return False
        del attempts[raw_state]
        return True

    async def database():
        yield db

    monkeypatch.setattr(oauth, "create_oauth_attempt", AsyncMock(side_effect=create_attempt))
    monkeypatch.setattr(oauth, "consume_oauth_attempt", AsyncMock(side_effect=consume_attempt))
    monkeypatch.setattr(oauth, "resolve_oauth_user", AsyncMock(return_value=user))
    monkeypatch.setattr(deps, "get_user_by_id", AsyncMock(return_value=user))
    api = create_app(enable_dev_api=False)
    api.dependency_overrides[get_db] = database
    api.dependency_overrides[get_redis] = lambda: redis
    api.dependency_overrides[oauth.get_oauth_provider_client] = lambda: provider
    with TestClient(api, raise_server_exceptions=False, follow_redirects=False) as client:
        yield SimpleNamespace(
            client=client, api=api, db=db, redis=redis, user=user,
            provider=provider, attempts=attempts,
        )


def start(environment, provider="kakao"):
    session = environment.client.get("/api/v1/auth/session")
    assert session.status_code == 200
    response = environment.client.post(
        f"/api/v1/auth/login/{provider}",
        json={"purpose": "signin"},
        headers={"X-CSRF-Token": session.json()["csrfToken"]},
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    return parse_qs(urlsplit(response.json()["authorizationUrl"]).query)["state"][0]


def callback(environment, state, provider="kakao", **params):
    return environment.client.get(
        f"/api/v1/auth/login/{provider}/callback",
        params={"state": state, "code": "one-use-code", **params},
    )


def assert_failure(response, code):
    assert response.status_code == 303
    assert response.headers["location"] == f"http://localhost:3000/pivot/login?error={code}"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "set-cookie" not in response.headers
    assert response.content == b""


@pytest.mark.parametrize("provider", ["kakao", "naver"])
@pytest.mark.parametrize("complete", [False, True])
def test_login_rotates_session_and_enforces_onboarding(environment, provider, complete):
    environment.provider.exchange_identity.return_value = OAuthIdentity(provider, "123", None, None)
    if complete:
        environment.user.onboarding_completed_at = datetime.now(timezone.utc)
    state = start(environment, provider)
    old_cookie = environment.client.cookies.get(settings.session_cookie_name)
    old_csrf = environment.client.get("/api/v1/auth/session").json()["csrfToken"]
    response = callback(environment, state, provider)
    assert response.status_code == 303
    path = "/pivot/home" if complete else "/pivot/consent"
    assert response.headers["location"] == "http://localhost:3000" + path
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert old_cookie != environment.client.cookies.get(settings.session_cookie_name)
    assert len(environment.redis.values) == 1
    current = environment.client.get("/api/v1/auth/session").json()
    assert current["authenticated"] is True
    assert current["sessionState"] == ("active" if complete else "onboarding_pending")
    assert current["user"] == ({"id": 23, "accountType": "user"} if complete else None)
    assert current["csrfToken"] != old_csrf
    if not complete:
        blocked = environment.client.get("/api/v1/users/me")
        assert blocked.status_code == 403
        assert blocked.json()["code"] == "ONBOARDING_REQUIRED"
    assert environment.client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": old_csrf}
    ).status_code == 403
    assert environment.client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": current["csrfToken"]}
    ).status_code == 204
    assert environment.client.get("/api/v1/auth/session").json()["authenticated"] is False
    assert environment.user.last_login_at is not None
    environment.provider.exchange_identity.assert_awaited_once_with(provider, "one-use-code", state)


def test_start_requires_real_cookie_and_csrf(environment):
    assert environment.client.post("/api/v1/auth/login/kakao", json={}).status_code == 401
    environment.client.get("/api/v1/auth/session")
    assert environment.client.post("/api/v1/auth/login/kakao", json={}).status_code == 403
    oauth.create_oauth_attempt.assert_not_awaited()


@pytest.mark.parametrize("purpose", ["reauth", "restore"])
def test_unimplemented_account_flows_are_not_treated_as_signin(environment, purpose):
    csrf = environment.client.get("/api/v1/auth/session").json()["csrfToken"]
    response = environment.client.post(
        "/api/v1/auth/login/kakao", json={"purpose": purpose}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "UNSUPPORTED_AUTH_PURPOSE"
    oauth.create_oauth_attempt.assert_not_awaited()


def test_no_body_defaults_to_signin_and_external_return_url_is_rejected(environment):
    csrf = environment.client.get("/api/v1/auth/session").json()["csrfToken"]
    response = environment.client.post(
        "/api/v1/auth/login/kakao", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    response = environment.client.post(
        "/api/v1/auth/login/kakao", json={"redirectTo": "https://untrusted.example"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


def test_disabled_provider_and_unsupported_provider(environment, monkeypatch):
    csrf = environment.client.get("/api/v1/auth/session").json()["csrfToken"]
    monkeypatch.setattr(settings, "naver_oauth_enabled", False)
    for provider, status, code in [
        ("naver", 503, "AUTH_UNAVAILABLE"), ("apple", 422, "UNSUPPORTED_PROVIDER")
    ]:
        response = environment.client.post(
            f"/api/v1/auth/login/{provider}", json={}, headers={"X-CSRF-Token": csrf}
        )
        assert response.status_code == status
        assert response.json()["code"] == code
    oauth.create_oauth_attempt.assert_not_awaited()


def test_start_propagates_rate_limit(environment, monkeypatch):
    monkeypatch.setattr(oauth, "create_oauth_attempt", AsyncMock(
        side_effect=ApiError(429, "RATE_LIMITED", "잠시 후 다시 시도해 주세요.")
    ))
    csrf = environment.client.get("/api/v1/auth/session").json()["csrfToken"]
    response = environment.client.post(
        "/api/v1/auth/login/kakao", json={}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"
    environment.provider.build_authorization_url.assert_not_called()


def test_wrong_browser_provider_and_state_never_exchange_code(environment):
    state = start(environment)
    old_cookie = environment.client.cookies.get(settings.session_cookie_name)
    assert_failure(callback(environment, state, "naver"), "AUTH_FAILED")
    assert_failure(callback(environment, "wrong-state"), "AUTH_FAILED")
    environment.client.cookies.clear()
    environment.client.get("/api/v1/auth/session")
    assert environment.client.cookies.get(settings.session_cookie_name) != old_cookie
    assert_failure(callback(environment, state), "AUTH_FAILED")
    environment.provider.exchange_identity.assert_not_awaited()


def test_cancel_consumes_state_even_when_provider_could_be_retried(environment):
    state = start(environment)
    response = environment.client.get(
        "/api/v1/auth/login/kakao/callback", params={"state": state, "error": "access_denied"}
    )
    assert_failure(response, "AUTH_CANCELLED")
    assert_failure(callback(environment, state), "AUTH_FAILED")
    environment.provider.exchange_identity.assert_not_awaited()


@pytest.mark.parametrize("suffix", [
    "&state=duplicate", "&code=duplicate", "&error=access_denied",
])
def test_ambiguous_callback_is_rejected(environment, suffix):
    state = start(environment)
    response = environment.client.get(
        f"/api/v1/auth/login/kakao/callback?state={state}&code=code{suffix}"
    )
    assert_failure(response, "AUTH_FAILED")
    environment.provider.exchange_identity.assert_not_awaited()


@pytest.mark.parametrize("code", ["", "x" * 4097], ids=["missing", "oversized"])
def test_invalid_code_consumes_state_without_provider_request(environment, code):
    state = start(environment)
    assert_failure(callback(environment, state, code=code), "AUTH_FAILED")
    assert state not in environment.attempts
    environment.provider.exchange_identity.assert_not_awaited()


@pytest.mark.parametrize("failure,expected", [
    (ApiError(401, "AUTH_FAILED", "private provider detail"), "AUTH_FAILED"),
    (ApiError(503, "AUTH_UNAVAILABLE", "private provider detail"), "AUTH_UNAVAILABLE"),
])
def test_provider_failure_has_fixed_error_and_cannot_replay(environment, failure, expected):
    state = start(environment)
    old_cookie = environment.client.cookies.get(settings.session_cookie_name)
    environment.provider.exchange_identity.side_effect = failure
    response = callback(environment, state)
    assert_failure(response, expected)
    assert "private provider detail" not in str(response.headers)
    assert environment.client.cookies.get(settings.session_cookie_name) == old_cookie
    assert_failure(callback(environment, state), "AUTH_FAILED")
    assert environment.provider.exchange_identity.await_count == 1
    oauth.resolve_oauth_user.assert_not_awaited()


@pytest.mark.parametrize("code", ["AUTH_ACCOUNT_EXISTS", "AUTH_ACCOUNT_UNAVAILABLE"])
def test_rejected_account_does_not_receive_login_cookie(environment, monkeypatch, code):
    state = start(environment)
    monkeypatch.setattr(oauth, "resolve_oauth_user", AsyncMock(
        side_effect=ApiError(409, code, "private account detail")
    ))
    assert_failure(callback(environment, state), code)
    assert len(environment.redis.values) == 1
    assert environment.client.get("/api/v1/auth/session").json()["authenticated"] is False


def test_expired_or_logged_out_start_session_rejects_callback(environment):
    state = start(environment)
    environment.redis.values.clear()
    assert_failure(callback(environment, state), "AUTH_FAILED")
    environment.provider.exchange_identity.assert_not_awaited()


def test_redis_outage_redirects_without_cookie_or_provider_call(environment):
    state = start(environment)
    environment.redis.get.side_effect = RedisError("redis://secret-value")
    response = callback(environment, state)
    assert_failure(response, "AUTH_UNAVAILABLE")
    assert "secret-value" not in str(response.headers)
    environment.provider.exchange_identity.assert_not_awaited()


def test_commit_failure_discards_unissued_redis_session(environment):
    state = start(environment)
    environment.db.commit.side_effect = SQLAlchemyError("database password")
    response = callback(environment, state)
    assert_failure(response, "AUTH_UNAVAILABLE")
    assert not environment.redis.values
    assert state not in environment.attempts


def test_rollback_failure_still_discards_unissued_redis_session(environment):
    state = start(environment)
    environment.db.commit.side_effect = SQLAlchemyError("database unavailable")
    environment.db.rollback.side_effect = SQLAlchemyError("database unavailable")
    assert_failure(callback(environment, state), "AUTH_UNAVAILABLE")
    assert not environment.redis.values


def test_provider_identity_must_match_callback(environment):
    state = start(environment)
    environment.provider.exchange_identity.return_value = OAuthIdentity("naver", "123", None, None)
    assert_failure(callback(environment, state), "AUTH_FAILED")
    oauth.resolve_oauth_user.assert_not_awaited()


def test_backend_only_mode_returns_to_session_endpoint(environment, monkeypatch):
    monkeypatch.setattr(settings, "oauth_frontend_origin", "")
    state = start(environment)
    response = callback(environment, state)
    assert response.status_code == 303
    assert response.headers["location"] == "/api/v1/auth/session"
    session = environment.client.get(response.headers["location"])
    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    assert session.json()["sessionState"] == "onboarding_pending"
    replay = callback(environment, state)
    assert replay.headers["location"] == "/api/v1/auth/session?error=AUTH_FAILED"


def test_https_session_cookie_is_secure(environment, monkeypatch):
    state = start(environment)
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    response = callback(environment, state)
    assert response.status_code == 303
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def test_routes_available_with_dev_api_disabled(environment):
    schema = environment.client.get("/openapi.json").json()
    assert "/api/v1/auth/login/{provider}" in schema["paths"]
    assert "/api/v1/auth/login/{provider}/callback" in schema["paths"]
    assert "/api/v1/dev/auth/login" not in schema["paths"]
