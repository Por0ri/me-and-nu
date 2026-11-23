"""Registered admin support routes with actual isolated session and grant reads."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.session_store import get_session_store
from app.core.config import settings
from app.core.security import hash_token
from app.db.session import get_db
from app.main import create_app
from app.models import AdminAccount, AuthSession, UserAccount
from app.services import admin_support_service as service
from app.services.admin_permission_provider import get_admin_permission_provider
from test_admin_support import MappingRows, ReadSession, support_tables

PATHS = ("/api/v1/admin/reports", "/api/v1/admin/reports/1", "/api/v1/admin/inquiries", "/api/v1/admin/inquiries/1")
TOKEN = "isolated-support-read-token"


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (UserAccount, AdminAccount, AuthSession):
        model.__table__.create(engine)
    meta = support_tables()
    meta.create_all(engine)
    with Session(engine) as db:
        db.add_all([UserAccount(user_id=uid, nickname="PRIVATE_REPORTER" if uid == 1 else "관리자",
                               email=f"private-{uid}@example.invalid", auth_provider="kakao", provider_user_id=f"fixture-{uid}")
                    for uid in (1, 7)])
        db.add(AdminAccount(admin_account_id=7, user_id=7, role="viewer"))
        db.add(AuthSession(auth_session_id=7, user_id=7, session_state="active", session_token_hash=hash_token(TOKEN),
                           csrf_token_hash="fixture", expires_at=datetime.now(timezone.utc)+timedelta(hours=1)))
        db.commit()
    with engine.begin() as connection:
        fixture = ReadSession(connection, meta)
        fixture.content(title="달빛 100%_OST"); fixture.report(); fixture.inquiry()

    class Adapter:
        def __init__(self, session): self.session = session
        async def execute(self, statement):
            result = self.session.execute(statement)
            if "record_id" in result.keys():
                rows = [{k: v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v
                         for k, v in row.items()} for row in result.mappings()]
                return MappingRows(rows)
            return result
        async def scalar(self, statement): return self.session.scalar(statement)
        async def get(self, model, key): return self.session.get(model, key)
        async def commit(self): self.session.commit()

    async def database():
        with Session(engine) as session:
            yield Adapter(session)

    monkeypatch.setattr(settings, "session_backend", "postgres")
    monkeypatch.setattr(settings, "redis_url", None)
    app = create_app(enable_dev_api=False)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_session_store] = lambda: None
    reads = []
    for name in ("list_support", "get_report", "get_inquiry"):
        wrapped = AsyncMock(wraps=getattr(service, name))
        monkeypatch.setattr(service, name, wrapped)
        reads.append(wrapped)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, app=app, engine=engine, reads=reads)
    engine.dispose()


def login(api):
    api.client.cookies.set(settings.session_cookie_name, TOKEN)


def test_registered_read_only_contract_requires_real_session(api):
    spec = api.app.openapi()
    for path in PATHS:
        response = api.client.get(path, headers={"X-Role": "admin"})
        assert response.status_code == 401
    for path in ("/api/v1/admin/reports", "/api/v1/admin/reports/{reportId}",
                 "/api/v1/admin/inquiries", "/api/v1/admin/inquiries/{inquiryId}"):
        assert set(spec["paths"][path]) == {"get"}
    assert all(read.await_count == 0 for read in api.reads)


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_all_read_roles_receive_actual_safe_summary_and_detail(api, role):
    with api.engine.begin() as connection:
        connection.execute(text("UPDATE admin_account SET role=:role"), {"role": role})
    login(api)
    for path in PATHS:
        response = api.client.get(path)
        assert response.status_code == 200, response.text
        assert "no-store" in response.headers["cache-control"]
        assert "PRIVATE_REPORTER" not in response.text and "@example.invalid" not in response.text
    page = api.client.get(PATHS[0]).json()
    assert page["total"] == 2 and page["pageSize"] == 5
    assert page["statistics"] == {"all": 2, "waiting": 2, "reviewing": 0, "completed": 0}
    assert {item["id"] for item in page["items"]} == {"I-1", "R-1"}
    assert all(isinstance(item["recordId"], str) and "message" not in item for item in page["items"])
    inquiry = api.client.get(PATHS[3]).json()
    assert inquiry["message"] == "PRIVATE_INQUIRY_BODY" and inquiry["answer"] is None
    assert inquiry["answerAvailability"] == "unavailable"


def test_revocation_is_fresh_and_content_permission_cannot_grant_support_read(api):
    login(api)
    assert api.client.get(PATHS[0]).status_code == 200
    before = [read.await_count for read in api.reads]
    with api.engine.begin() as connection:
        connection.execute(text("UPDATE admin_account SET revoked_at=:at"), {"at": datetime.now(timezone.utc)})
    for path in PATHS:
        response = api.client.get(path, headers={"X-Role": "admin"})
        assert response.status_code == 403 and response.json()["code"] == "ADMIN_REQUIRED"
    assert before == [read.await_count for read in api.reads]
    api.app.dependency_overrides[get_admin_permission_provider] = lambda: SimpleNamespace(
        can_read_contents=AsyncMock(return_value=True), can_read_support=AsyncMock(return_value=False))
    assert api.client.get(PATHS[0]).status_code == 403


def test_search_uses_private_header_and_url_query_is_rejected_without_echo(api):
    login(api)
    response = api.client.get(PATHS[0], headers={"X-Admin-Search": quote("%_", safe="")})
    assert response.status_code == 200 and response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == "R-1"
    assert "X-Admin-Search" in response.headers["vary"]
    for path in (PATHS[0], PATHS[2]):
        response = api.client.get(path + "?q=PRIVATE_EMAIL")
        assert response.status_code == 422 and "PRIVATE_EMAIL" not in response.text
        response = api.client.get(path, headers={"X-Admin-Search": "%ZZ_PRIVATE_EMAIL"})
        assert response.status_code == 422 and "PRIVATE_EMAIL" not in response.text


@pytest.mark.parametrize("suffix", ["?kind=unknown", "?status=resolved", "?period=1y", "?page=0", "?pageSize=101"])
def test_bad_filters_fail_before_read(api, suffix):
    login(api)
    response = api.client.get(PATHS[0]+suffix)
    assert response.status_code == 422
    assert all(read.await_count == 0 for read in api.reads)


def test_detail_ids_are_bounded_and_missing_records_are_404(api):
    login(api)
    for root in (PATHS[0], PATHS[2]):
        for value in ("0", "-1", "R-1", "9223372036854775808"):
            assert api.client.get(root + "/" + value).status_code == 422
        assert api.client.get(root + "/999").status_code == 404


def test_support_database_failure_is_controlled_without_echo(api, monkeypatch):
    login(api)
    monkeypatch.setattr(service, "list_support", AsyncMock(side_effect=SQLAlchemyError("PRIVATE_DATABASE_URL")))
    response = api.client.get(PATHS[0])
    assert response.status_code == 503 and response.json()["code"] == "ADMIN_SUPPORT_UNAVAILABLE"
    assert "PRIVATE" not in response.text


def test_deleted_account_inquiry_detail_is_404_with_no_body_leak(api):
    login(api)
    with api.engine.begin() as connection:
        connection.execute(text("UPDATE user_account SET withdrawn_at=:at WHERE user_id=1"), {"at": datetime.now(timezone.utc)})
    assert api.client.get(PATHS[2]).json()["total"] == 0
    response = api.client.get(PATHS[3])
    assert response.status_code == 404 and "PRIVATE" not in response.text
    # Report survives withdrawal, with no requester identity exposed.
    assert api.client.get(PATHS[1]).status_code == 200
