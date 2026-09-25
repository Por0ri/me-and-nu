"""Public session contract and local-only authentication entry points."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import AuthContext, get_optional_auth_context
from app.api.dev import auth as dev_auth
from app.api.v1.auth import router as session_routes
from app.core.config import settings
from app.core.security import create_session_id, csrf_token_for_session, hash_token
from app.db.session import get_db
from app.main import app
from app.models.user import UserAccount
from app.repositories.auth_session_repository import find_auth_session
from app.services.user_service import EmailAlreadyRegisteredError


@pytest.fixture
def auth_environment(monkeypatch: pytest.MonkeyPatch):
    user = UserAccount(
        user_id=23, email="local@example.com", nickname="공룡",
        signup_channel="consumer", auth_provider="local",
        hashed_password="hashed-password", account_status="active",
    )
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    sessions: dict[str, SimpleNamespace] = {}

    async def override_get_db():
        yield db

    async def override_get_context(request: Request):
        raw = request.cookies.get(settings.session_cookie_name)
        row = sessions.get(raw or "")
        if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(timezone.utc):
            return None
        return AuthContext(session=row, raw_token=raw, user=user if row.user_id else None)

    async def fake_create_session(_, *, user_id: int | None, session_state: str):
        raw = create_session_id()
        row = SimpleNamespace(
            user_id=user_id, session_state=session_state,
            csrf_token_hash=hash_token(csrf_token_for_session(raw)),
            revoked_at=None,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        sessions[raw] = row
        return row, raw

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_optional_auth_context] = override_get_context
    monkeypatch.setattr(session_routes, "create_auth_session", fake_create_session)
    monkeypatch.setattr(dev_auth, "create_auth_session", fake_create_session)
    monkeypatch.setattr(dev_auth, "authenticate_user", AsyncMock(return_value=user))
    with TestClient(app) as client:
        yield client, user, db, sessions
    app.dependency_overrides.clear()


def _anonymous_csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json()["sessionState"] == "anonymous"
    return response.json()["csrfToken"]


def test_session_issues_anonymous_csrf_cookie(auth_environment):
    client, _, _, _ = auth_environment
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.json()["user"] is None
    assert response.json()["csrfToken"]
    assert settings.session_cookie_name in response.cookies
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"


def test_dev_registration_requires_csrf_and_uses_dev_path(auth_environment, monkeypatch):
    client, user, _, _ = auth_environment
    register = AsyncMock(return_value=user)
    monkeypatch.setattr(dev_auth, "register_user", register)
    payload = {"email": "local@example.com", "password": "password123", "nickname": "공룡", "role": "consumer"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 404
    csrf = _anonymous_csrf(client)
    assert client.post("/api/v1/dev/auth/register", json=payload).status_code == 403
    response = client.post("/api/v1/dev/auth/register", json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201
    assert response.json() == {"id": 23, "email": "local@example.com", "nickname": "공룡", "accountType": "consumer"}
    register.assert_awaited_once()


def test_dev_register_duplicate_and_invalid_role(auth_environment, monkeypatch):
    client, _, _, _ = auth_environment
    csrf = _anonymous_csrf(client)
    payload = {"email": "local@example.com", "password": "password123", "nickname": "공룡"}
    monkeypatch.setattr(dev_auth, "register_user", AsyncMock(side_effect=EmailAlreadyRegisteredError()))
    duplicate = client.post("/api/v1/dev/auth/register", json=payload, headers={"X-CSRF-Token": csrf})
    invalid = client.post("/api/v1/dev/auth/register", json={**payload, "role": "admin"}, headers={"X-CSRF-Token": csrf})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "EMAIL_ALREADY_REGISTERED"
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_ERROR"


def test_malformed_json_uses_common_400_error(auth_environment):
    client, _, _, _ = auth_environment
    csrf = _anonymous_csrf(client)
    response = client.post(
        "/api/v1/dev/auth/register",
        content="{invalid",
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["fields"]


def test_unknown_path_uses_common_error(auth_environment):
    client, _, _, _ = auth_environment
    response = client.get("/api/v1/no-such-resource")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_login_session_logout_and_expiration(auth_environment):
    client, user, _, sessions = auth_environment
    csrf = _anonymous_csrf(client)
    login = client.post(
        "/api/v1/dev/auth/login",
        json={"email": "local@example.com", "password": "password123"},
        headers={"X-CSRF-Token": csrf},
    )
    assert login.status_code == 200
    assert login.json()["user"]["accountType"] == "consumer"
    current = client.get("/api/v1/auth/session")
    assert current.json()["authenticated"] is True
    assert current.json()["sessionState"] == "onboarding_pending"
    assert current.json()["user"] is None
    assert client.get("/api/v1/users/me").json()["code"] == "ONBOARDING_REQUIRED"
    assert client.post("/api/v1/auth/logout").status_code == 403
    logout = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": current.json()["csrfToken"]})
    assert logout.status_code == 204
    assert logout.content == b""
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/users/me").json()["code"] == "AUTH_REQUIRED"
    raw = next(raw for raw, row in sessions.items() if row.user_id == user.user_id)
    sessions[raw].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    client.cookies.set(settings.session_cookie_name, raw)
    assert client.get("/api/v1/auth/session").json()["authenticated"] is False


def test_login_rejects_invalid_credentials(auth_environment, monkeypatch):
    client, _, _, _ = auth_environment
    csrf = _anonymous_csrf(client)
    monkeypatch.setattr(dev_auth, "authenticate_user", AsyncMock(return_value=None))
    response = client.post(
        "/api/v1/dev/auth/login",
        json={"email": "local@example.com", "password": "wrong-password"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_profile_patch_contract(auth_environment):
    client, user, _, sessions = auth_environment
    user.onboarding_completed_at = datetime.now(timezone.utc)
    raw = create_session_id()
    sessions[raw] = SimpleNamespace(
        user_id=user.user_id, session_state="active",
        csrf_token_hash=hash_token(csrf_token_for_session(raw)),
        revoked_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    client.cookies.set(settings.session_cookie_name, raw)
    csrf = csrf_token_for_session(raw)
    assert client.get("/api/v1/users/me").json()["userId"] == user.user_id
    assert client.patch("/api/v1/users/me", json={}, headers={"X-CSRF-Token": csrf}).status_code == 422
    missing_image = client.patch("/api/v1/users/me", json={"profileImageId": 501}, headers={"X-CSRF-Token": csrf})
    assert missing_image.status_code == 404
    assert missing_image.json()["code"] == "IMAGE_NOT_FOUND"
    patched = client.patch("/api/v1/users/me", json={"nickname": "새 이름"}, headers={"X-CSRF-Token": csrf})
    assert patched.status_code == 200
    assert patched.json()["nickname"] == "새 이름"
    assert patched.json()["accountType"] == "consumer"


@pytest.mark.asyncio
async def test_session_lookup_records_last_access():
    row = SimpleNamespace(
        revoked_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        last_accessed_at=None,
    )
    result = SimpleNamespace(scalar_one_or_none=lambda: row)
    db = SimpleNamespace(execute=AsyncMock(return_value=result), commit=AsyncMock())
    found = await find_auth_session(db, "opaque-session")
    assert found is row
    assert row.last_accessed_at is not None
    db.commit.assert_awaited_once()
