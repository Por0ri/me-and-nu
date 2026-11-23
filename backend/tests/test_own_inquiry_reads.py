"""Own inquiry GETs use real sessions and ownership SQL against isolated SQLite."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.session_store import get_session_store
from app.core.config import settings
from app.core.security import hash_token
from app.db.session import get_db
from app.main import create_app
from app.models import AdminAccount, AuthSession, Inquiry, InquiryAnswer, UserAccount
from app.services import inquiry_read_service

BIG_ID = 9007199254740993
NOW = datetime.now(timezone.utc)
TOKEN = "own-inquiry-isolated-session"


class Rows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def all(self): return self.rows
    def one_or_none(self): return self.rows[0] if self.rows else None


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _): connection.execute("PRAGMA foreign_keys=ON")
    for model in (UserAccount, AdminAccount, AuthSession, Inquiry, InquiryAnswer):
        model.__table__.create(engine)
    with Session(engine) as db:
        db.add_all([UserAccount(user_id=uid, nickname=f"member{uid}", email=f"private{uid}@example.invalid",
            auth_provider="kakao", provider_user_id=f"fixture{uid}", onboarding_completed_at=NOW) for uid in (1, 2, 7)])
        db.flush()
        db.add(AdminAccount(admin_account_id=7, user_id=7, role="operator"))
        db.add(AuthSession(auth_session_id=1, user_id=1, session_state="active", session_token_hash=hash_token(TOKEN),
            csrf_token_hash="unused-read-token", expires_at=NOW + timedelta(hours=1)))
        db.add_all([Inquiry(inquiry_id=id_, user_id=owner, inquiry_type="service", detail=body, status=status,
            created_at=NOW - timedelta(minutes=id_ if id_ < 100 else 0), updated_at=NOW)
            for id_, owner, body, status in [(1, 1, "MY_PENDING", "received"), (2, 2, "OTHER_SECRET", "answered"),
                (3, 1, "LEGACY", "answered"), (BIG_ID, 1, "MY_ANSWERED", "answered")]])
        db.flush()
        db.add_all([InquiryAnswer(inquiry_id=id_, admin_account_id=7, body=body, answered_at=NOW)
                    for id_, body in [(2, "OTHER_ANSWER_SECRET"), (BIG_ID, "MY_PUBLIC_ANSWER")]])
        db.commit()

    class Adapter:
        def __init__(self, session): self.session = session
        async def execute(self, statement):
            result = self.session.execute(statement)
            if "inquiry_id" in result.keys():
                return Rows([{key: value.replace(tzinfo=timezone.utc) if isinstance(value, datetime) and value.tzinfo is None else value
                              for key, value in row.items()} for row in result.mappings()])
            return result
        async def scalar(self, statement): return self.session.scalar(statement)
        async def get(self, model, key): return self.session.get(model, key)
        async def commit(self): self.session.commit()
    async def database():
        with Session(engine) as db: yield Adapter(db)
    monkeypatch.setattr(settings, "session_backend", "postgres")
    monkeypatch.setattr(settings, "redis_url", None)
    app = create_app(enable_dev_api=False)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_session_store] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, engine=engine, app=app)
    engine.dispose()


def login(api): api.client.cookies.set(settings.session_cookie_name, TOKEN)


def test_actual_session_required_and_dev_bypass_does_not_reveal_history(api, monkeypatch):
    monkeypatch.setattr(settings, "enable_dev_auth_bypass", True)
    for path in ("/api/v1/inquiries", "/api/v1/inquiries/1"):
        response = api.client.get(path, headers={"X-Role": "admin"})
        assert response.status_code == 401 and response.json()["code"] == "AUTH_REQUIRED"
        assert "no-store" in response.headers["cache-control"]


def test_list_owns_records_and_serializes_bigint_without_internal_or_full_reply_data(api):
    login(api)
    response = api.client.get("/api/v1/inquiries", params={"pageSize": 2})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 3 and page["pageSize"] == 2
    assert page["items"][0]["inquiryId"] == str(BIG_ID)
    assert page["items"][0]["answerAvailability"] == "available"
    assert set(page["items"][0]) == {"inquiryId", "inquiryType", "status", "createdAt", "updatedAt", "preview", "answerAvailability"}
    for forbidden in ("OTHER_SECRET", "OTHER_ANSWER_SECRET", "MY_PUBLIC_ANSWER", "private", "adminAccountId"):
        assert forbidden not in response.text
    empty = api.client.get("/api/v1/inquiries", params={"page": 100}).json()
    assert empty["total"] == 3 and empty["items"] == []


def test_detail_available_pending_legacy_and_owner_boundary(api):
    login(api)
    answered = api.client.get(f"/api/v1/inquiries/{BIG_ID}")
    assert answered.status_code == 200, answered.text
    row = answered.json()
    assert row["answer"] == "MY_PUBLIC_ANSWER" and row["answeredAt"].endswith("Z")
    assert row["answerAvailability"] == "available" and "adminAccountId" not in row
    pending = api.client.get("/api/v1/inquiries/1").json()
    assert pending["answerAvailability"] == "none" and pending["answer"] is None
    legacy = api.client.get("/api/v1/inquiries/3").json()
    assert legacy["status"] == "answered" and legacy["answerAvailability"] == "unavailable" and legacy["answer"] is None
    missing = api.client.get("/api/v1/inquiries/404")
    other = api.client.get("/api/v1/inquiries/2")
    assert missing.status_code == other.status_code == 404 and missing.json() == other.json()


def test_actor_header_binds_cookie_owner_without_granting_access(api):
    login(api)
    assert api.client.get("/api/v1/inquiries", headers={"X-Inquiry-Actor-User-Id": "1"}).status_code == 200
    for path in ("/api/v1/inquiries", "/api/v1/inquiries/2"):
        response = api.client.get(path, headers={"X-Inquiry-Actor-User-Id": "2"})
        assert response.status_code == 403 and response.json()["code"] == "INQUIRY_SESSION_CHANGED"
        assert "OTHER_SECRET" not in response.text


@pytest.mark.parametrize("change", ["account_status='withdrawn'", "account_status='suspended'", "withdrawn_at=CURRENT_TIMESTAMP"])
def test_withdrawal_and_disabled_account_immediately_hide_history(api, change):
    login(api)
    with api.engine.begin() as db: db.execute(text(f"UPDATE user_account SET {change} WHERE user_id=1"))
    assert api.client.get("/api/v1/inquiries").status_code == 401
    assert api.client.get(f"/api/v1/inquiries/{BIG_ID}").status_code == 401


def test_incomplete_onboarding_and_expired_session_fail_closed(api):
    login(api)
    with api.engine.begin() as db: db.execute(text("UPDATE user_account SET onboarding_completed_at=NULL WHERE user_id=1"))
    assert api.client.get("/api/v1/inquiries").status_code == 403
    with api.engine.begin() as db: db.execute(text("UPDATE auth_session SET expires_at=:at"), {"at": NOW - timedelta(days=1)})
    assert api.client.get("/api/v1/inquiries").status_code == 401


def test_limits_and_storage_errors_do_not_return_fake_empty_success(api, monkeypatch):
    login(api)
    for path in ("/api/v1/inquiries?page=0", "/api/v1/inquiries?pageSize=51", "/api/v1/inquiries/9223372036854775808"):
        assert api.client.get(path).status_code == 422
    monkeypatch.setattr(inquiry_read_service, "list_own_inquiries", AsyncMock(side_effect=SQLAlchemyError("PRIVATE_FAILURE")))
    response = api.client.get("/api/v1/inquiries")
    assert response.status_code == 503 and "PRIVATE_FAILURE" not in response.text


def test_answer_cascades_when_owner_is_purged(api):
    with api.engine.begin() as db:
        db.execute(text("DELETE FROM user_account WHERE user_id=1"))
        assert db.scalar(select(InquiryAnswer.inquiry_id).where(InquiryAnswer.inquiry_id == BIG_ID)) is None
        assert db.scalar(select(InquiryAnswer.inquiry_id).where(InquiryAnswer.inquiry_id == 2)) == 2
