"""HTTP writes against disposable SQL, real session/CSRF and current DB grants."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.admin.support_router import get_support_write_db
from app.core.config import settings
from app.core.security import csrf_token_for_session, hash_token
from app.models import AdminActionLog
from app.schemas.admin_support_action import AdminInquiryAnswerRequest, AdminReportActionRequest
from app.services.admin_support_permission_provider import get_admin_support_permission_provider
from test_admin_support_api import TOKEN, api, login  # noqa: F401
from test_admin_content_review import WriteSession


@pytest.fixture
def writes(api):
    AdminActionLog.__table__.create(api.engine)
    with api.engine.begin() as db:
        db.execute(text("UPDATE admin_account SET role='operator'"))
        db.execute(text("UPDATE auth_session SET csrf_token_hash=:csrf"),
                   {"csrf": hash_token(csrf_token_for_session(TOKEN))})
    async def database():
        with Session(api.engine) as db:
            yield WriteSession(db)
    api.app.dependency_overrides[get_support_write_db] = database
    login(api)
    return api


def payload(api, kind="report", **changes):
    path = "/api/v1/admin/" + ("reports/1" if kind == "report" else "inquiries/1")
    detail = api.client.get(path)
    assert detail.status_code == 200, detail.text
    value = detail.json()
    body = {"requestId": str(uuid4()), "expectedStatus": value["rawStatus"],
            "expectedUpdatedAt": value["updatedAt"]}
    body.update({"action": "resolve", "rationale": "출처와 사실 확인"} if kind == "report"
                else {"answer": "답변 본문 PRIVATE_ANSWER alice@example.invalid"})
    body.update(changes)
    return body


def headers(**changes):
    return {"X-CSRF-Token": csrf_token_for_session(TOKEN), "X-Admin-Support-Actor": "7",
            "X-Admin-Support-Grant": "7", **changes}


def send(api, body, kind="report", **kwargs):
    path = "/api/v1/admin/reports/1/actions" if kind == "report" else "/api/v1/admin/inquiries/1/answer"
    return api.client.post(path, json=body, headers=kwargs.get("headers", headers()))


def stored(api):
    with api.engine.connect() as db:
        return {table: [dict(row) for row in db.execute(text("SELECT * FROM " + table)).mappings()]
                for table in ("content_report", "inquiry", "inquiry_answer", "admin_action_log")}


@pytest.mark.parametrize("patch", [
    {"answer": " \n "}, {"answer": "x" * 5001}, {"answer": 15}, {"answer": "x\x00y"},
    {"expectedStatus": "answered"}, {"expectedUpdatedAt": "2026-11-23T00:00:00.1234567Z"},
    {"expectedUpdatedAt": "2026-11-23T00:00:00-00:00"}, {"expectedUpdatedAt": 123},
    {"requestId": "bad"}, {"actorUserId": 7},
])
def test_answer_contract_rejects_unsafe_or_ambiguous_inputs(patch):
    with pytest.raises(ValidationError):
        AdminInquiryAnswerRequest(**{"requestId": str(uuid4()), "answer": "안내",
            "expectedStatus": "received", "expectedUpdatedAt": "2026-11-23T00:00:00.123456Z", **patch})


def test_report_contract_requires_reason_and_preserves_microseconds():
    data = {"requestId": str(uuid4()), "action": "resolve", "rationale": "확인", "expectedStatus": "received",
            "expectedUpdatedAt": "2026-11-23T09:00:00.123456+09:00"}
    assert AdminReportActionRequest(**data).expected_updated_at.microsecond == 123456
    with pytest.raises(ValidationError):
        AdminReportActionRequest(**(data | {"rationale": "  "}))


@pytest.mark.parametrize("action,status", [("start_review", "reviewing"), ("resolve", "resolved"), ("reject", "rejected")])
def test_report_transitions_and_atomic_audit(writes, action, status):
    before = stored(writes)
    request = payload(writes, action=action)
    response = send(writes, request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["rawStatus"] == status and result["recordId"] == "1" and not result["replayed"]
    after = stored(writes)
    assert after["content_report"][0]["status"] == status
    assert after["inquiry"] == before["inquiry"]
    assert len(after["admin_action_log"]) == 1 and after["admin_action_log"][0]["rationale"] == request["rationale"]
    retry = send(writes, request)
    assert retry.status_code == 200 and retry.json() == (result | {"replayed": True})
    assert len(stored(writes)["admin_action_log"]) == 1
    conflict = send(writes, request | {"rationale": "다른 내용"})
    assert conflict.status_code == 409 and conflict.json()["code"] == "SUPPORT_REQUEST_CONFLICT"


def test_answer_publication_replay_and_no_body_in_audit(writes):
    request = payload(writes, "inquiry")
    response = send(writes, request, "inquiry")
    assert response.status_code == 200, response.text
    assert response.json()["rawStatus"] == "answered"
    after = stored(writes)
    assert after["inquiry"][0]["status"] == "answered"
    assert after["inquiry_answer"][0]["body"] == request["answer"]
    serialized_audit = json.dumps(after["admin_action_log"], ensure_ascii=False)
    assert "PRIVATE_ANSWER" not in serialized_audit and "alice@example.invalid" not in serialized_audit
    assert "PRIVATE_INQUIRY_BODY" not in serialized_audit
    detail = writes.client.get("/api/v1/admin/inquiries/1").json()
    assert detail["answer"] == request["answer"] and detail["answeredAt"]
    assert detail["answerAvailability"] == "available"
    assert detail["actions"]["allowedActions"] == [] and detail["actions"]["disabledReason"] == "terminal_state"
    assert send(writes, request, "inquiry").json()["replayed"] is True
    changed = send(writes, request | {"answer": "다른 답변"}, "inquiry")
    assert changed.status_code == 409 and changed.json()["code"] == "SUPPORT_REQUEST_CONFLICT"
    fresh = send(writes, request | {"requestId": str(uuid4())}, "inquiry")
    assert fresh.status_code == 409 and fresh.json()["code"] == "SUPPORT_STATE_CONFLICT"
    assert len(stored(writes)["inquiry_answer"]) == len(stored(writes)["admin_action_log"]) == 1


def test_same_version_changes_and_terminal_states_conflict(writes):
    initial = payload(writes, action="start_review")
    assert send(writes, initial).status_code == 200
    assert send(writes, initial | {"requestId": str(uuid4()), "action": "resolve"}).status_code == 409
    current = payload(writes, action="reject")
    assert send(writes, current).status_code == 200
    terminal = writes.client.get("/api/v1/admin/reports/1").json()
    assert terminal["actions"]["allowedActions"] == []
    assert terminal["rawStatus"] == "rejected"


@pytest.mark.parametrize("kind", ["report", "inquiry"])
def test_auth_csrf_scope_and_viewer_cannot_write(writes, kind):
    request = payload(writes, kind)
    before = stored(writes)
    for invalid in [headers(**{"X-CSRF-Token": "invalid"}),
                    headers(Origin="https://evil.invalid")]:
        assert send(writes, request, kind, headers=invalid).status_code == 403
    scope = send(writes, request, kind, headers=headers(**{"X-Admin-Support-Actor": "8"}))
    assert scope.status_code == 409 and scope.json()["code"] == "SUPPORT_SCOPE_CHANGED"
    for invalid in [headers(**{"X-Admin-Support-Grant": "0"}), headers(**{"X-Admin-Support-Actor": "9223372036854775808"})]:
        assert send(writes, request, kind, headers=invalid).status_code == 422
    with writes.engine.begin() as db:
        db.execute(text("UPDATE admin_account SET role='viewer'"))
    denied = send(writes, request, kind)
    assert denied.status_code == 403 and denied.json()["code"] == "ADMIN_WRITE_REQUIRED"
    writes.client.cookies.clear()
    assert send(writes, request, kind).status_code == 401
    assert stored(writes) == before


def test_replay_rechecks_grant_revocation(writes):
    request = payload(writes, "inquiry")
    assert send(writes, request, "inquiry").status_code == 200
    with writes.engine.begin() as db:
        db.execute(text("UPDATE admin_account SET revoked_at=CURRENT_TIMESTAMP"))
    assert send(writes, request, "inquiry").status_code == 403
    assert len(stored(writes)["admin_action_log"]) == 1


def test_withdrawn_requester_cannot_be_answered(writes):
    request = payload(writes, "inquiry")
    with writes.engine.begin() as db:
        db.execute(text("UPDATE user_account SET account_status='withdrawn', withdrawn_at=CURRENT_TIMESTAMP WHERE user_id=1"))
    result = send(writes, request, "inquiry")
    assert result.status_code == 404 and result.json()["code"] == "ADMIN_INQUIRY_NOT_FOUND"
    assert not stored(writes)["inquiry_answer"] and not stored(writes)["admin_action_log"]


@pytest.mark.parametrize("kind", ["report", "inquiry"])
def test_audit_failure_rolls_back_status_and_answer(writes, kind):
    request = payload(writes, kind)
    before = stored(writes)
    with writes.engine.begin() as db:
        db.execute(text("CREATE TRIGGER fail_support_audit BEFORE INSERT ON admin_action_log BEGIN SELECT RAISE(ABORT,'fixture'); END"))
    response = send(writes, request, kind)
    assert response.status_code == 503 and response.json()["code"] == "ADMIN_SUPPORT_WRITE_UNAVAILABLE"
    assert stored(writes) == before


def test_permission_store_failure_is_not_denial_or_success(writes):
    request = payload(writes)
    writes.app.dependency_overrides[get_admin_support_permission_provider] = lambda: SimpleNamespace(
        lock_grant=AsyncMock(side_effect=RuntimeError("PRIVATE_DB_ERROR")))
    result = send(writes, request)
    assert result.status_code == 503 and result.json()["code"] == "ADMIN_PERMISSION_UNAVAILABLE"
    assert "PRIVATE_DB_ERROR" not in result.text
    assert not stored(writes)["admin_action_log"]
