"""Isolated HTTP + real SQL/session checks. No service DB, accounts or migrations."""

from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import admin_deps
from app.api.session_store import get_session_store
from app.api.v1.admin.router import router
from app.core.config import settings
from app.core.redis import get_redis
from app.core.security import csrf_token_for_session, hash_token
from app.db.session import get_db
from app.main import create_app
from app.models import AuthSession, ContentSource, SourceSite, UserAccount
from app.repositories.auth_session_repository import utcnow
from app.schemas.admin_content_review import AdminContentReviewRequest
from app.services import admin_content_service as service
from app.services.admin_permission_provider import get_admin_permission_provider

PATHS = ("/api/v1/admin/contents", "/api/v1/admin/contents/2")
TOKEN = "isolated-admin-api-test-cookie"


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (UserAccount, AuthSession, SourceSite, ContentSource):
        model.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(text("""CREATE TABLE content (
            content_id BIGINT PRIMARY KEY, title TEXT NOT NULL,
            judgment_status TEXT NOT NULL, status TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL, published_at TIMESTAMP, summary TEXT, body TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT '2026-11-13 09:12:34.123456+09:00'
        )"""))
        connection.execute(text("""INSERT INTO content
            (content_id, title, judgment_status, status, created_at, published_at, summary, body) VALUES
            (1, '달빛 OST', 'pending', 'active', '2026-11-10 00:00:00', NULL, NULL, '본문'),
            (2, '달빛 소식', 'needs_review', 'pending_review', '2026-11-11 00:00:00', NULL, '요약', '상세 본문'),
            (3, '달빛 100%_특집', 'needs_review', 'active', '2026-11-11 00:00:00', NULL, NULL, '본문')
        """))
    with Session(engine) as db:
        db.add(UserAccount(user_id=7, nickname="격리 테스트", auth_provider="kakao", provider_user_id="fixture",
                           account_status="active", onboarding_completed_at=utcnow()))
        db.add(AuthSession(auth_session_id=1, user_id=7, session_state="active", session_token_hash=hash_token(TOKEN),
                           csrf_token_hash="fixture", expires_at=utcnow() + timedelta(minutes=30)))
        db.add(SourceSite(source_site_id=1, media_name="테스트 출처", collect_method="manual", topic_id=1))
        db.add_all([
            ContentSource(content_source_id=1, content_id=2, source_site_id=None, source_url="https://example.org/extra",
                          source_title=None, source_role="material", citation_order=None),
            ContentSource(content_source_id=2, content_id=2, source_site_id=1, source_url="https://example.org/primary",
                          source_title="원문", source_role="primary", citation_order=1),
        ])
        db.commit()

    class ReadSession:
        def __init__(self, db):
            self.db = db

        async def execute(self, statement):
            return self.db.execute(statement)

        async def scalar(self, statement):
            return self.db.scalar(statement)

        async def get(self, model, key):
            return self.db.get(model, key)

        async def commit(self):
            self.db.commit()

    async def isolated_db():
        with Session(engine) as db:
            yield ReadSession(db)

    monkeypatch.setattr(settings, "redis_url", None)
    app = create_app(enable_dev_api=False)
    # The only router registration and allowing provider live in isolated tests.
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = isolated_db
    app.dependency_overrides[get_session_store] = lambda: None
    permission = AsyncMock(return_value=True)
    app.dependency_overrides[get_admin_permission_provider] = lambda: SimpleNamespace(can_read_contents=permission)
    reads = [AsyncMock(wraps=service.list_contents), AsyncMock(wraps=service.get_content)]
    monkeypatch.setattr(service, "list_contents", reads[0])
    monkeypatch.setattr(service, "get_content", reads[1])
    with TestClient(app, raise_server_exceptions=False, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        yield SimpleNamespace(client=client, app=app, engine=engine, permission=permission, reads=reads)
    engine.dispose()


def login(api):
    api.client.cookies.set(settings.session_cookie_name, TOKEN)


def assert_closed(api, status, code):
    for path in PATHS:
        response = api.client.get(path, headers={"X-Role": "admin", "X-Admin": "true"}, params={"role": "admin"})
        assert response.status_code == status
        assert response.json()["code"] == code
        assert set(response.json()) == {"code", "message"}
        assert "secret" not in response.text
        assert response.headers["cache-control"] == "private, no-store"
    for read in api.reads:
        read.assert_not_awaited()


@pytest.mark.parametrize("cookie", [None, "unknown-cookie"])
def test_anonymous_or_invalid_cookie_cannot_read_even_with_client_role(api, cookie):
    if cookie:
        api.client.cookies.set(settings.session_cookie_name, cookie)
    assert_closed(api, 401, "AUTH_REQUIRED")
    api.permission.assert_not_awaited()


def test_default_provider_denies_real_authenticated_user(api):
    login(api)
    del api.app.dependency_overrides[get_admin_permission_provider]
    assert_closed(api, 403, "ADMIN_REQUIRED")


@pytest.mark.parametrize("decision", [False, None, "admin", 1])
def test_only_explicit_server_permission_grants_access(api, decision):
    login(api)
    api.permission.return_value = decision
    assert_closed(api, 403, "ADMIN_REQUIRED")


@pytest.mark.parametrize("change", [
    "UPDATE auth_session SET revoked_at = CURRENT_TIMESTAMP",
    "UPDATE auth_session SET expires_at = '2000-01-01'",
    "UPDATE auth_session SET session_state = 'onboarding_pending'",
    "UPDATE auth_session SET session_state = 'restoration_pending'",
    "UPDATE user_account SET account_status = 'suspended'",
    "UPDATE user_account SET account_status = 'withdrawn'",
    "DELETE FROM user_account",
])
def test_invalid_session_or_inactive_user_is_denied(api, change):
    login(api)
    with api.engine.begin() as db:
        db.execute(text(change))
    assert_closed(api, 401, "AUTH_REQUIRED")
    api.permission.assert_not_awaited()


def test_dev_bypass_settings_never_create_admin_authentication(api, monkeypatch):
    monkeypatch.setattr(settings, "enable_dev_api", True)
    monkeypatch.setattr(settings, "enable_dev_auth_bypass", True)
    assert_closed(api, 401, "AUTH_REQUIRED")
    login(api)
    del api.app.dependency_overrides[get_admin_permission_provider]
    assert_closed(api, 403, "ADMIN_REQUIRED")


@pytest.mark.parametrize(("failure", "code"), [
    ("session", "ADMIN_AUTH_UNAVAILABLE"), ("user", "ADMIN_AUTH_UNAVAILABLE"),
    ("permission", "ADMIN_PERMISSION_UNAVAILABLE"),
])
def test_verification_failure_is_closed_without_content_reads(api, monkeypatch, failure, code):
    login(api)
    error = RuntimeError("secret storage credentials")
    if failure == "permission":
        api.permission.side_effect = error
    else:
        target = "find_auth_session" if failure == "session" else "get_user_by_id"
        monkeypatch.setattr(admin_deps, target, AsyncMock(side_effect=error))
    assert_closed(api, 503, code)


def test_revocation_is_checked_again_with_the_same_cookie(api):
    login(api)
    for path in PATHS:
        assert api.client.get(path).status_code == 200
    api.permission.return_value = False
    for read in api.reads:
        read.reset_mock()
    assert_closed(api, 403, "ADMIN_REQUIRED")
    assert api.client.cookies.get(settings.session_cookie_name) == TOKEN
    assert api.permission.await_count == 4
    assert all(call.kwargs == {"user_id": 7} for call in api.permission.await_args_list)


@pytest.mark.parametrize("mode", ["valid", "missing", "malformed", "unavailable", "no-client"])
def test_configured_redis_sessions_never_fall_back_to_valid_sql_session(api, monkeypatch, mode):
    login(api)
    monkeypatch.setattr(settings, "session_backend", "redis")
    redis = AsyncMock()
    now = utcnow()
    redis.get.return_value = json.dumps({
        "session_token_hash": hash_token(TOKEN), "csrf_token_hash": hash_token(csrf_token_for_session(TOKEN)),
        "user_id": 7, "session_state": "active", "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
    })
    if mode == "missing":
        redis.get.return_value = None
    elif mode == "malformed":
        redis.get.return_value = '{"role":"admin"}'
    elif mode == "unavailable":
        redis.get.side_effect = RedisConnectionError("secret")
    del api.app.dependency_overrides[get_session_store]
    api.app.dependency_overrides[get_redis] = lambda: None if mode == "no-client" else redis
    if mode == "valid":
        for path in PATHS:
            assert api.client.get(path).status_code == 200
    elif mode in {"missing", "malformed"}:
        assert_closed(api, 401, "AUTH_REQUIRED")
    else:
        assert_closed(api, 503, "SESSION_STORE_UNAVAILABLE" if mode == "no-client" else "ADMIN_AUTH_UNAVAILABLE")


@pytest.mark.parametrize(("query", "ids", "total"), [
    ({}, [3, 2, 1], 3), ({"q": " OST "}, [1], 1), ({"q": "%_"}, [3], 1),
    ({"judgmentStatus": "needs_review"}, [3, 2], 2), ({"status": "pending_review"}, [2], 1),
    ({"q": "달빛", "judgmentStatus": "needs_review", "status": "active"}, [3], 1),
    ({"page": 2, "pageSize": 1}, [2], 3), ({"page": 4, "pageSize": 1}, [], 3),
])
def test_allowed_admin_http_query_uses_existing_real_sql(api, query, ids, total):
    login(api)
    response = api.client.get(PATHS[0], params=query)
    assert response.status_code == 200
    body = response.json()
    assert [item["contentId"] for item in body["items"]] == ids
    assert body["total"] == total
    assert body["page"] == query.get("page", 1)
    assert body["pageSize"] == query.get("pageSize", 20)
    assert all("updatedAt" not in item for item in body["items"])
    assert response.headers["cache-control"] == "private, no-store"


def test_detail_sources_and_missing_content_use_existing_service(api):
    login(api)
    response = api.client.get(PATHS[1])
    assert response.status_code == 200
    body = response.json()
    assert body["body"] == "상세 본문"
    assert body["sources"] == [
        {"url": "https://example.org/primary", "title": "원문", "publisher": "테스트 출처", "role": "primary", "citationOrder": 1},
        {"url": "https://example.org/extra", "title": None, "publisher": None, "role": "material", "citationOrder": None},
    ]
    assert body["updatedAt"] == "2026-11-13T09:12:34.123456+09:00"
    assert set(body) == {"contentId", "title", "judgmentStatus", "status", "createdAt", "publishedAt", "updatedAt", "summary", "body", "sources"}
    assert api.client.get(PATHS[0] + "/1").json()["sources"] == []
    missing = api.client.get(PATHS[0] + "/999")
    assert missing.status_code == 404
    assert missing.json()["code"] == "CONTENT_NOT_FOUND"


@pytest.mark.parametrize("stored", [
    "2026-11-13T00:12:34.123456+00:00", "2026-11-13T09:12:34.000001+09:00",
    "2026-11-12T20:42:34.123400-03:30", "2026-11-13T00:12:34+00:00",
])
def test_detail_preserves_changed_db_version_through_http_and_review_contract(api, stored):
    login(api)
    before = api.client.get(PATHS[1]).json()
    with api.engine.begin() as db:
        db.execute(text("UPDATE content SET updated_at = :value WHERE content_id = 2"), {"value": stored})
    response = api.client.get(PATHS[1])
    assert response.status_code == 200
    body = response.json()
    assert body["updatedAt"] == stored.replace("+00:00", "Z")
    assert {key: value for key, value in body.items() if key != "updatedAt"} == {
        key: value for key, value in before.items() if key != "updatedAt"
    }
    # Validate the next slice's DTO locally; never execute a review POST.
    request = AdminContentReviewRequest(
        requestId="00000000-0000-4000-8000-000000000001", action="approve", rationale="검토 완료",
        expectedStatus=body["status"], expectedJudgmentStatus=body["judgmentStatus"], expectedUpdatedAt=body["updatedAt"],
    )
    expected = datetime.fromisoformat(stored)
    assert request.expected_updated_at == expected
    assert request.expected_updated_at.microsecond == expected.microsecond
    assert request.expected_updated_at.utcoffset() == expected.utcoffset()


def test_detail_openapi_requires_updated_at_only_on_detail(api):
    schemas = api.client.get("/openapi.json").json()["components"]["schemas"]
    assert "updatedAt" in schemas["AdminContentDetail"]["required"]
    assert schemas["AdminContentDetail"]["properties"]["updatedAt"]["format"] == "date-time"
    assert "updatedAt" not in schemas["AdminContentItem"]["properties"]


@pytest.mark.parametrize("suffix", ["?page=0", "?pageSize=101", "?status=published", "?judgmentStatus=approved", "/0", "/invalid"])
def test_validation_runs_behind_authorization_and_never_calls_service(api, suffix):
    path = PATHS[0] + suffix
    assert api.client.get(path).status_code == 401
    login(api)
    api.permission.return_value = False
    assert api.client.get(path).status_code == 403
    api.permission.return_value = True
    response = api.client.get(path)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    for read in api.reads:
        read.assert_not_awaited()


def test_content_storage_failure_is_sanitized_not_empty_success(api):
    login(api)
    for path, read in zip(PATHS, api.reads):
        read.side_effect = RuntimeError("secret internal content")
        response = api.client.get(path)
        assert response.status_code == 500
        assert response.json()["code"] == "INTERNAL_ERROR"
        assert "secret" not in response.text and "items" not in response.json()
        assert response.headers["cache-control"] == "private, no-store"
