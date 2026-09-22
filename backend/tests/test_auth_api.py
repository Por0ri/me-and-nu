from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies as api_dependencies
from app.api.routes import auth as auth_routes
from app.db.session import get_db
from app.main import app
from app.mocks.session_store import mock_sessions
from app.models.user import UserAccount
from app.services.user_service import EmailAlreadyRegisteredError


@pytest.fixture
def user() -> UserAccount:
    return UserAccount(
        user_id=1,
        email="test@example.com",
        nickname="테스터",
        signup_channel="consumer",
        auth_provider="local",
        hashed_password="hashed-password",
    )


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch, user: UserAccount):
    async def override_get_db():
        yield object()

    monkeypatch.setattr(
        auth_routes,
        "authenticate_user",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(
        api_dependencies,
        "get_user_by_id",
        AsyncMock(return_value=user),
    )
    app.dependency_overrides[get_db] = override_get_db
    mock_sessions.clear()

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
    mock_sessions.clear()


def test_register_success(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    user: UserAccount,
):
    register_mock = AsyncMock(return_value=user)
    monkeypatch.setattr(auth_routes, "register_user", register_mock)

    response = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "test1234",
            "nickname": "테스터",
            "role": "consumer",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "email": "test@example.com",
        "nickname": "테스터",
        "role": "consumer",
    }
    register_mock.assert_awaited_once()


def test_register_duplicate_email(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        auth_routes,
        "register_user",
        AsyncMock(side_effect=EmailAlreadyRegisteredError()),
    )

    response = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "test1234",
            "nickname": "테스터",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "이미 가입된 이메일입니다."


def test_register_rejects_invalid_role(auth_client: TestClient):
    response = auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "test1234",
            "nickname": "테스터",
            "role": "admin",
        },
    )

    assert response.status_code == 422


def test_session_cookie_authentication_flow(auth_client: TestClient):
    before_login = auth_client.get("/api/v1/auth/me")
    assert before_login.status_code == 401

    login_response = auth_client.post(
        "/api/v1/auth/login",
        json={
            "email": "test@example.com",
            "password": "test1234",
        },
    )

    assert login_response.status_code == 200
    assert "session_id" in login_response.cookies
    assert "HttpOnly" in login_response.headers["set-cookie"]
    assert "SameSite=lax" in login_response.headers["set-cookie"]

    me_response = auth_client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "test@example.com"

    logout_response = auth_client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 200

    after_logout = auth_client.get("/api/v1/auth/me")
    assert after_logout.status_code == 401


def test_login_rejects_invalid_credentials(
    auth_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        auth_routes,
        "authenticate_user",
        AsyncMock(return_value=None),
    )

    response = auth_client.post(
        "/api/v1/auth/login",
        json={
            "email": "test@example.com",
            "password": "wrong-password",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "이메일 또는 비밀번호가 올바르지 않습니다."
