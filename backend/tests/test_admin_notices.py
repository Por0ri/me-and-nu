"""Real HTTP/session/grant/CSRF and SQL notice transactions in disposable SQLite."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.session_store import get_session_store
from app.api.v1.admin.notices_router import get_notice_write_db
from app.core.config import settings
from app.core.security import csrf_token_for_session, hash_token
from app.db.session import get_db
from app.main import create_app
from app.models import AdminAccount, AdminActionLog, AuthSession, Notice, Notification, UserAccount
from app.schemas.admin_notice import AdminNoticeActionRequest, AdminNoticeCreateRequest
from app.services.admin_notice_permission_provider import get_admin_notice_permission_provider
from app.services.admin_permission_provider import get_admin_permission_provider

TOKEN = "isolated-admin-notice-token"
ROOT = "/api/v1/admin/notices"


class SqlSession:
    def __init__(self, db): self.db = db
    def in_transaction(self): return self.db.in_transaction()
    def get_bind(self): return self.db.get_bind()
    @asynccontextmanager
    async def begin(self):
        with self.db.begin(): yield
    async def execute(self, statement): return self.db.execute(statement)
    async def scalar(self, statement): return self.db.scalar(statement)
    async def get(self, model, key): return self.db.get(model, key)
    async def commit(self): self.db.commit()
    async def flush(self): self.db.flush()


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (UserAccount, AdminAccount, AuthSession, AdminActionLog, Notice, Notification):
        model.__table__.create(engine)
    with Session(engine) as db, db.begin():
        db.add(UserAccount(user_id=7, nickname="synthetic-admin", auth_provider="kakao", provider_user_id="notice-admin"))
        db.add(UserAccount(user_id=1, nickname="synthetic-owner", auth_provider="kakao", provider_user_id="notice-owner",
                           onboarding_completed_at=datetime.now(timezone.utc)))
        db.add(AdminAccount(admin_account_id=17, user_id=7, role="operator"))
        db.add(AuthSession(auth_session_id=7, user_id=7, session_state="active", session_token_hash=hash_token(TOKEN),
            csrf_token_hash=hash_token(csrf_token_for_session(TOKEN)),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    async def database():
        with Session(engine) as db: yield SqlSession(db)
    monkeypatch.setattr(settings, "session_backend", "postgres")
    monkeypatch.setattr(settings, "redis_url", None)
    app = create_app(enable_dev_api=False)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_notice_write_db] = database
    app.dependency_overrides[get_session_store] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set(settings.session_cookie_name, TOKEN)
        yield SimpleNamespace(client=client, app=app, engine=engine)
    engine.dispose()


def payload(**changes):
    return {"requestId": str(uuid4()), "action": "save_draft", "title": "합성 100%_ 공지",
            "body": "NOTICE_BODY_PRIVATE_<script>test</script>", "scheduledAt": None, **changes}


def headers(**changes):
    return {"X-CSRF-Token": csrf_token_for_session(TOKEN), "X-Admin-Notice-Actor": "7",
            "X-Admin-Notice-Grant": "17", **changes}


def create(api, body=None, **changes):
    return api.client.post(ROOT, json=body or payload(**changes), headers=headers())


def action(api, notice_id, kind, **changes):
    detail = api.client.get(ROOT + "/" + str(notice_id)).json()
    body = {"requestId": str(uuid4()), "action": kind, "expectedStatus": detail["status"],
            "expectedUpdatedAt": detail["updatedAt"], **changes}
    if kind != "cancel_schedule":
        body.setdefault("title", detail["title"])
        body.setdefault("body", detail["body"])
    return body


def send_action(api, notice_id, body):
    return api.client.post(ROOT + "/" + str(notice_id) + "/actions", json=body, headers=headers())


def snapshot(api):
    with api.engine.connect() as db:
        return {name: [dict(row) for row in db.execute(text("SELECT * FROM " + name)).mappings()]
                for name in ("notice", "notification", "admin_action_log")}


@pytest.mark.parametrize("patch", [
    {"title": " "}, {"body": "\n"}, {"title": "x" * 101}, {"body": "x" * 5001},
    {"title": 12}, {"body": "x\x00y"}, {"requestId": "bad"}, {"actorId": 7},
    {"action": "schedule"}, {"scheduledAt": "2027-01-01T00:00:00+09:00"},
    {"action": "schedule", "scheduledAt": "2027-01-01T00:00:00"},
    {"action": "schedule", "scheduledAt": "2027-01-01T00:00:00.1234567Z"},
    {"action": "schedule", "scheduledAt": "2027-01-01T00:00:00-00:00"},
    {"action": "schedule", "scheduledAt": 123},
    {"action": "schedule", "scheduledAt": "0001-01-01T00:00:00+09:00"},
    {"action": "schedule", "scheduledAt": "9999-12-31T23:59:59-09:00"},
])
def test_create_rejects_ambiguous_or_unsafe_input(patch):
    with pytest.raises(ValidationError): AdminNoticeCreateRequest(**payload(**patch))


def test_edit_requires_full_author_fields_and_exact_timestamp():
    value = {"requestId": str(uuid4()), "action": "publish", "title": "제목", "body": "본문",
             "expectedStatus": "draft", "expectedUpdatedAt": "2026-11-23T09:00:00.123456+09:00"}
    parsed = AdminNoticeActionRequest(**value)
    assert parsed.expected_updated_at.microsecond == 123456
    for patch in ({"title": None}, {"body": None}, {"expectedUpdatedAt": "2026-11-23T09:00:00"},
                  {"action": "cancel_schedule"}, {"expectedStatus": "published"}):
        with pytest.raises(ValidationError): AdminNoticeActionRequest(**(value | patch))


def test_registered_contract_and_real_session_required(api):
    schema = api.app.openapi()["paths"]
    assert set(schema[ROOT]) == {"get", "post"}
    assert set(schema[ROOT + "/{noticeId}"]) == {"get"}
    assert set(schema[ROOT + "/{noticeId}/actions"]) == {"post"}
    api.client.cookies.clear()
    for response in (api.client.get(ROOT), api.client.get(ROOT + "/1"),
                     api.client.post(ROOT, json=payload(), headers=headers())):
        assert response.status_code == 401
        assert "no-store" in response.headers["cache-control"]
    assert all(not rows for rows in snapshot(api).values())


@pytest.mark.parametrize("role,can_create", [("viewer", False), ("operator", True), ("admin", True)])
def test_read_capabilities_and_current_write_roles(api, role, can_create):
    with api.engine.begin() as db: db.execute(text("UPDATE admin_account SET role=:role"), {"role": role})
    page = api.client.get(ROOT)
    assert page.status_code == 200 and page.json()["total"] == 0
    capabilities = page.json()["capabilities"]
    assert capabilities["allowedActions"] == (["create"] if can_create else [])
    assert capabilities["scope"] == ({"actorUserId": "7", "adminAccountId": "17"} if can_create else None)
    result = create(api)
    assert result.status_code == (200 if can_create else 403)


def test_create_draft_replay_and_request_identity(api):
    request = payload()
    first = create(api, request)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["status"] == "draft" and not result["replayed"] and result["scheduledAt"] is None
    assert result["publishedAt"] is None
    retry = create(api, request)
    assert retry.status_code == 200 and retry.json() == (result | {"replayed": True})
    conflict = create(api, request | {"body": "변경"})
    assert conflict.status_code == 409 and conflict.json()["code"] == "NOTICE_REQUEST_CONFLICT"
    data = snapshot(api)
    assert len(data["notice"]) == len(data["admin_action_log"]) == 1
    assert data["notification"] == []
    assert request["body"] not in json.dumps(data["admin_action_log"])


def test_draft_edit_schedule_cancel_publish_lifecycle(api):
    created = create(api).json()
    notice_id = created["noticeId"]
    edit = action(api, notice_id, "save_draft", title="수정 제목", body="수정 본문")
    saved = send_action(api, notice_id, edit)
    assert saved.status_code == 200, saved.text
    assert saved.json()["updatedAt"] != created["updatedAt"]
    stale = send_action(api, notice_id, edit | {"requestId": str(uuid4())})
    assert stale.status_code == 409 and stale.json()["code"] == "NOTICE_STATE_CONFLICT"
    schedule = action(api, notice_id, "schedule", scheduledAt=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    scheduled = send_action(api, notice_id, schedule)
    assert scheduled.status_code == 200 and scheduled.json()["status"] == "scheduled"
    detail = api.client.get(ROOT + "/" + notice_id).json()
    assert detail["actions"]["allowedActions"] == ["cancel_schedule"]
    assert snapshot(api)["notification"] == []
    cancel = action(api, notice_id, "cancel_schedule")
    cancelled = send_action(api, notice_id, cancel)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "draft"
    assert cancelled.json()["scheduledAt"] is None
    publish = action(api, notice_id, "publish")
    published = send_action(api, notice_id, publish)
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published" and published.json()["publishedAt"]
    detail = api.client.get(ROOT + "/" + notice_id).json()
    assert detail["title"] == "수정 제목" and detail["body"] == "수정 본문"
    assert detail["actions"]["allowedActions"] == [] and detail["actions"]["disabledReason"] == "terminal_state"
    assert send_action(api, notice_id, publish).json()["replayed"] is True
    assert send_action(api, notice_id, publish | {"requestId": str(uuid4())}).status_code == 409
    data = snapshot(api)
    assert len(data["notification"]) == 1 and len(data["admin_action_log"]) == 5


def test_immediate_publish_delivers_once_and_read_count_is_real(api):
    request = payload(action="publish", body="알림 본문" * 300)
    first = create(api, request)
    assert first.status_code == 200, first.text
    notice_id = first.json()["noticeId"]
    assert first.json()["publishedAt"]
    assert create(api, request).json()["replayed"] is True
    data = snapshot(api)
    assert len(data["notification"]) == 1
    notification = data["notification"][0]
    assert notification["notice_id"] == int(notice_id) and len(notification["body"]) == 500
    detail = api.client.get(ROOT + "/" + notice_id).json()
    assert detail["body"] == request["body"] and detail["readCount"] == 0
    with api.engine.begin() as db: db.execute(text("UPDATE notification SET read_at=CURRENT_TIMESTAMP"))
    assert api.client.get(ROOT + "/" + notice_id).json()["readCount"] == 1


def test_schedule_requires_future_and_retry_stays_valid_after_time_passes(api):
    invalid = create(api, payload(action="schedule", scheduledAt="2020-01-01T00:00:00Z"))
    assert invalid.status_code == 422 and invalid.json()["code"] == "NOTICE_SCHEDULE_INVALID"
    request = payload(action="schedule", scheduledAt=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    first = create(api, request)
    assert first.status_code == 200 and first.json()["status"] == "scheduled"
    assert snapshot(api)["notification"] == []
    assert create(api, request).json()["replayed"] is True


def test_list_search_status_statistics_and_empty_page(api):
    assert create(api).status_code == 200
    assert create(api, payload(action="publish", title="다른 제목")).status_code == 200
    assert create(api, payload(action="schedule", scheduledAt=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())).status_code == 200
    result = api.client.get(ROOT + "?status=published&pageSize=1").json()
    assert result["total"] == 1 and len(result["items"]) == 1
    assert result["statistics"] == {"all": 3, "draft": 1, "scheduled": 1, "published": 1}
    assert "body" not in result["items"][0]
    empty = api.client.get(ROOT + "?page=100").json()
    assert empty["items"] == [] and empty["total"] == 3
    search = api.client.get(ROOT, headers={"X-Admin-Search": quote("%_", safe="")})
    assert search.status_code == 200 and search.json()["total"] == 2
    assert "X-Admin-Search" in search.headers["vary"]
    for suffix in ("?q=PRIVATE", "?page=0", "?pageSize=101", "?status=other"):
        invalid = api.client.get(ROOT + suffix)
        assert invalid.status_code == 422 and "PRIVATE" not in invalid.text


def test_bad_csrf_scope_and_revocation_cannot_write(api):
    before = snapshot(api)
    for invalid in (headers(**{"X-CSRF-Token": "invalid"}), headers(Origin="https://evil.invalid")):
        assert api.client.post(ROOT, json=payload(), headers=invalid).status_code == 403
    for key in ("X-Admin-Notice-Actor", "X-Admin-Notice-Grant"):
        changed = api.client.post(ROOT, json=payload(), headers=headers(**{key: "999"}))
        assert changed.status_code == 409 and changed.json()["code"] == "NOTICE_SCOPE_CHANGED"
        assert api.client.post(ROOT, json=payload(), headers=headers(**{key: "9223372036854775808"})).status_code == 422
    assert snapshot(api) == before
    request = payload()
    assert create(api, request).status_code == 200
    with api.engine.begin() as db: db.execute(text("UPDATE admin_account SET revoked_at=CURRENT_TIMESTAMP"))
    assert create(api, request).status_code == 403
    assert api.client.get(ROOT).status_code == 403


def test_other_read_capability_cannot_grant_notice_access(api):
    api.app.dependency_overrides[get_admin_permission_provider] = lambda: SimpleNamespace(
        can_read_contents=AsyncMock(return_value=True), can_read_notices=AsyncMock(return_value=False))
    assert api.client.get(ROOT).status_code == 403


@pytest.mark.parametrize("kind", ["save_draft", "schedule", "publish"])
def test_audit_failure_rolls_back_notice_and_all_deliveries(api, kind):
    with api.engine.begin() as db:
        db.execute(text("CREATE TRIGGER fail_notice_audit BEFORE INSERT ON admin_action_log BEGIN SELECT RAISE(ABORT,'fixture'); END"))
    request = payload(action=kind, scheduledAt=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat() if kind == "schedule" else None)
    result = create(api, request)
    assert result.status_code == 503 and result.json()["code"] == "ADMIN_NOTICE_WRITE_UNAVAILABLE"
    assert all(not rows for rows in snapshot(api).values())


def test_permission_failure_and_bounded_missing_ids(api):
    for value in ("0", "-1", "9223372036854775808"):
        assert api.client.get(ROOT + "/" + value).status_code == 422
    assert api.client.get(ROOT + "/999").status_code == 404
    api.app.dependency_overrides[get_admin_notice_permission_provider] = lambda: SimpleNamespace(
        lock_grant=AsyncMock(side_effect=RuntimeError("PRIVATE_STORE")))
    result = create(api)
    assert result.status_code == 503 and result.json()["code"] == "ADMIN_PERMISSION_UNAVAILABLE"
    assert "PRIVATE" not in result.text
