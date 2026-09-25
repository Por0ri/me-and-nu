"""The no-login Swagger mode must remain confined to an explicit local opt-in."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1.topics import router as topics_router
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models import UserAccount
from app.schemas.catalog import TopicsResponse


def test_local_dev_bypass_uses_demo_user_without_cookie_or_csrf(monkeypatch):
    monkeypatch.setattr(settings, "enable_dev_api", True)
    monkeypatch.setattr(settings, "enable_dev_auth_bypass", True)
    user = UserAccount(
        user_id=99,
        email="swagger-demo@menu.invalid",
        nickname="Swagger 테스트",
        signup_channel="consumer",
        account_status="active",
        auth_provider="kakao",
        provider_user_id="menu-swagger-demo-v1",
        onboarding_completed_at=datetime.now(timezone.utc),
    )
    find_user = AsyncMock(return_value=user)
    monkeypatch.setattr(deps, "get_user_by_email", find_user)
    topics = AsyncMock(return_value=TopicsResponse(topics=[]))
    monkeypatch.setattr(topics_router.catalog_service, "topics", topics)
    db = SimpleNamespace(commit=AsyncMock())

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000)) as client:
            session = client.get("/api/v1/auth/session")
            assert session.status_code == 200
            assert session.json()["authenticated"] is True
            assert session.json()["onboardingCompleted"] is True
            assert settings.session_cookie_name not in client.cookies

            response = client.get("/api/v1/topics")
            assert response.status_code == 200
            assert response.json() == {"topics": []}
            assert client.post("/api/v1/auth/logout").status_code == 204
            forbidden = client.post("/api/v1/auth/logout", headers={"Origin": "https://elsewhere.example"})
            assert forbidden.status_code == 403
            assert forbidden.json()["code"] == "CSRF_INVALID"
    finally:
        app.dependency_overrides.clear()
    assert find_user.await_count >= 2
    topics.assert_awaited_once()


def test_dev_bypass_requires_both_flag_and_loopback_request(monkeypatch):
    monkeypatch.setattr(settings, "enable_dev_api", True)
    monkeypatch.setattr(settings, "enable_dev_auth_bypass", True)
    with TestClient(app, base_url="http://localhost:8000", client=("198.51.100.7", 50000)) as client:
        assert client.get("/api/v1/topics").status_code == 401
    with TestClient(app, base_url="http://example.com", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/v1/topics").status_code == 401
    monkeypatch.setattr(settings, "enable_dev_auth_bypass", False)
    with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/v1/topics").status_code == 401
