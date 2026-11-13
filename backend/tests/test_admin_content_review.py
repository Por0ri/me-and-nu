"""Real SQL against disposable SQLite files. No migrations/service DB access.

PR #71 table contract is represented only in fixture DDL. The allowing provider
below is test-only; production has no role policy/provider or registered route.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.api.v1.admin.review_router import get_review_db, router
from app.core.config import settings
from app.core.exceptions import ApiError
from app.core.security import csrf_token_for_session, hash_token
from app.main import create_app
from app.models import Content
from app.repositories import admin_content_review_repository as repo
from app.repositories.carousel_repository import public_content_condition
from app.schemas.admin_content_review import AdminContentReviewRequest
from app.services import admin_content_review_service as service
from app.services.admin_review_permission_provider import (
    AdminReviewGrant, get_admin_review_permission_provider,
)
from test_admin_content_api import TOKEN, api, login  # noqa: F401 - real auth fixture

STAMP = datetime(2026, 11, 13, tzinfo=timezone.utc)
BASIS = {"method": "carousel_import_v1", "digest": "preserved-evidence",
         "operator_review": None, "preview": True,
         "local_test_preview": {"database": "test_old", "quality_approval": "not_reviewed"},
         "checking_test_preview": {"database": "test_other", "environment": "checking"}}


class WriteSession:
    """Reuse the existing sync-to-async SQL test pattern, with real transactions."""
    def __init__(self, db):
        self.db = db

    def in_transaction(self):
        return self.db.in_transaction()

    @asynccontextmanager
    async def begin(self):
        with self.db.begin():
            yield

    async def execute(self, statement):
        return self.db.execute(statement)


class TestPermissionProvider:
    __test__ = False
    def __init__(self):
        self.allowed = True
        self.targets = {2}

    async def lock_grant(self, db, *, user_id, content_id):
        row = (await db.execute(text(
            "SELECT admin_account_id, user_id FROM admin_account "
            "WHERE user_id = 7 AND revoked_at IS NULL"
        ))).mappings().one_or_none()
        if self.allowed and content_id in self.targets and row and row["user_id"] == user_id:
            return AdminReviewGrant(**row)
        return None


@pytest.fixture
def review_db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "review.sqlite"), connect_args={"timeout": 5})
    @event.listens_for(engine, "connect")
    def constraints(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    with engine.begin() as db:
        for statement in [
            "CREATE TABLE admin_account (admin_account_id INTEGER PRIMARY KEY, user_id BIGINT NOT NULL, revoked_at TIMESTAMP)",
            """CREATE TABLE draft (draft_id BIGINT PRIMARY KEY, status TEXT NOT NULL
                CHECK (status IN ('writing','checking','approved','published','discarded')),
                updated_at TIMESTAMP NOT NULL, factcheck_result JSON, title TEXT, body TEXT)""",
            """CREATE TABLE content (content_id BIGINT PRIMARY KEY, source_draft_id BIGINT REFERENCES draft(draft_id),
                status TEXT NOT NULL CHECK (status IN ('active','pending_review','hidden','deleted')),
                judgment_status TEXT NOT NULL CHECK (judgment_status IN ('pending','needs_review','confirmed','failed')),
                updated_at TIMESTAMP NOT NULL, published_at TIMESTAMP, deleted_at TIMESTAMP,
                ai_judgment_basis JSON, title TEXT, body TEXT, summary TEXT, feed_valid_until TIMESTAMP)""",
            """CREATE TABLE admin_action_log (admin_action_log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_account_id BIGINT NOT NULL REFERENCES admin_account(admin_account_id),
                action_type VARCHAR(40) NOT NULL, target_type VARCHAR(20) NOT NULL, target_id BIGINT,
                before_value JSON, after_value JSON, rationale TEXT NOT NULL,
                is_reverted BOOLEAN NOT NULL DEFAULT FALSE, acted_at TIMESTAMP NOT NULL)""",
            """CREATE TABLE content_source (content_source_id BIGINT PRIMARY KEY, content_id BIGINT REFERENCES content(content_id),
                source_url TEXT, source_title TEXT, source_role TEXT, citation_order INTEGER, agent_run_source_id BIGINT)""",
            "CREATE TABLE content_slide (content_id BIGINT, page INTEGER, image_data BLOB)",
            "INSERT INTO admin_account VALUES (11, 7, NULL)",
        ]:
            db.execute(text(statement))
        db.execute(text("INSERT INTO draft VALUES (3, 'checking', :stamp, :basis, '초안 제목', '초안 본문')"),
                   {"stamp": "2026-11-13 00:00:00.000000", "basis": json.dumps(BASIS)})
        db.execute(text("""INSERT INTO content VALUES (2, 3, 'pending_review', 'needs_review', :stamp,
            NULL, NULL, :basis, '제목', '본문', '요약', '2099-01-01 00:00:00.000000')"""),
                   {"stamp": "2026-11-13 00:00:00.000000", "basis": json.dumps(BASIS)})
        db.execute(text("INSERT INTO content_source VALUES (1,2,'https://example.org','원문','primary',1,17)"))
        db.execute(text("INSERT INTO content_slide VALUES (2,1,X'0102'),(2,2,X'0304')"))
    state = SimpleNamespace(engine=engine, permissions=TestPermissionProvider())
    yield state
    engine.dispose()


def request_data(**overrides):
    return {"requestId": str(uuid4()), "action": "approve", "rationale": " 출처 확인 완료 ",
            "expectedStatus": "pending_review", "expectedJudgmentStatus": "needs_review",
            "expectedUpdatedAt": STAMP.isoformat(), **overrides}


async def review(state, request=None, **kwargs):
    request = request or AdminContentReviewRequest(**request_data())
    with Session(state.engine) as db:
        return await service.review_content(WriteSession(db), 2, request, user_id=7,
            permissions=kwargs.get("permissions", state.permissions))


def rows(state, table):
    assert table in {"content", "draft", "admin_action_log", "content_source", "content_slide"}
    with state.engine.connect() as db:
        return [dict(row) for row in db.execute(text(f"SELECT * FROM {table}")).mappings()]


def snapshot(state):
    return {name: rows(state, name) for name in ["content", "draft", "admin_action_log", "content_source", "content_slide"]}


def change(state, sql):
    with state.engine.begin() as db:
        db.execute(text(sql))


@pytest.mark.parametrize("patch", [
    {"rationale": " \n "}, {"rationale": "x" * 2001}, {"rationale": 12},
    {"rationale": "a\x00b"}, {"rationale": "a\ud800b"}, {"action": "hide"},
    {"expectedStatus": "hidden"}, {"expectedJudgmentStatus": "confirmed"},
    {"expectedUpdatedAt": "2026-11-13"}, {"expectedUpdatedAt": 123},
    {"requestId": "not-uuid"}, {"actorId": 7}, {"adminAccountId": 11}, {"status": "active"},
])
def test_strict_dto(patch):
    with pytest.raises(ValidationError):
        AdminContentReviewRequest(**request_data(**patch))


def test_dto_unicode_and_whitespace():
    assert AdminContentReviewRequest(**request_data(rationale="  검토\n😀  ")).rationale == "검토\n😀"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("status", ["active", "pending_review"])
@pytest.mark.parametrize("draft_status", ["checking", "approved"])
async def test_atomic_transition_and_audit_preserve_sources_and_evidence(review_db, action, status, draft_status):
    change(review_db, f"UPDATE content SET status='{status}'")
    change(review_db, f"UPDATE draft SET status='{draft_status}'")
    before = snapshot(review_db)
    result = await review(review_db, AdminContentReviewRequest(**request_data(action=action, expectedStatus=status)))
    after = snapshot(review_db)
    content, draft, audit = after["content"][0], after["draft"][0], after["admin_action_log"][0]
    assert (content["status"], content["judgment_status"], draft["status"]) == (
        ("active", "confirmed", "published") if action == "approve" else ("hidden", "failed", "discarded"))
    assert after["content_source"] == before["content_source"]
    assert after["content_slide"] == before["content_slide"]
    for key in ("title", "body", "summary", "feed_valid_until", "deleted_at", "source_draft_id"):
        assert content[key] == before["content"][0][key]
    expected_basis = {k: v for k, v in BASIS.items() if k not in service.PREVIEW_KEYS} if action == "approve" else BASIS
    assert json.loads(content["ai_judgment_basis"]) == expected_basis
    assert json.loads(draft["factcheck_result"]) == expected_basis
    assert audit["admin_account_id"] == 11 and audit["rationale"] == "출처 확인 완료"
    assert audit["action_type"] == "content." + action and audit["target_type"] == "content" and audit["target_id"] == 2
    assert json.loads(audit["before_value"])["content"]["status"] == status
    assert json.loads(audit["after_value"])["content"]["status"] == result.status
    assert json.loads(audit["after_value"])["actor_user_id"] == 7
    assert audit["is_reverted"] == 0 and result.audit_log_id == audit["admin_action_log_id"]
    assert (result.published_at == result.updated_at) if action == "approve" else result.published_at is None
    # Reuse the actual consumer predicate without modifying it: only approved,
    # unexpired content with sources/slides and nonfuture publication can appear.
    with Session(review_db.engine) as db:
        visible = db.scalars(select(Content.content_id).where(public_content_condition())).all()
    # SQLite CURRENT_TIMESTAMP has second precision whereas the new publication
    # has microseconds. The past/future cases below assert positive visibility.
    if action == "reject":
        assert visible == []


@pytest.mark.asyncio
@pytest.mark.parametrize("published", ["2020-01-01 00:00:00.000000", "2099-01-01 00:00:00.000000"])
@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_existing_publication_time_is_never_changed(review_db, published, action):
    change(review_db, f"UPDATE content SET published_at='{published}'")
    await review(review_db, AdminContentReviewRequest(**request_data(action=action)))
    assert rows(review_db, "content")[0]["published_at"] == published
    with Session(review_db.engine) as db:
        visible = db.scalars(select(Content.content_id).where(public_content_condition())).all()
    assert visible == ([2] if action == "approve" and published.startswith("2020") else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [
    "UPDATE content SET feed_valid_until='2020-01-01'", "DELETE FROM content_source",
    "DELETE FROM content_slide WHERE page=2", "UPDATE content_slide SET page=3 WHERE page=2",
])
async def test_approval_does_not_override_existing_feed_requirements(review_db, sql):
    change(review_db, "UPDATE content SET published_at='2020-01-01 00:00:00.000000'")
    change(review_db, sql)
    result = await review(review_db)
    assert result.status == "active" and result.judgment_status == "confirmed"
    with Session(review_db.engine) as db:
        assert db.scalars(select(Content.content_id).where(public_content_condition())).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [
    "UPDATE content SET status='hidden'", "UPDATE content SET status='deleted'",
    "UPDATE content SET judgment_status='pending'", "UPDATE content SET judgment_status='failed'",
    "UPDATE content SET judgment_status='confirmed'", "UPDATE content SET deleted_at=CURRENT_TIMESTAMP",
    "UPDATE content SET updated_at='2026-11-14 00:00:00.000000'", "UPDATE content SET source_draft_id=NULL",
    "UPDATE draft SET status='writing'", "UPDATE draft SET status='published'", "UPDATE draft SET status='discarded'",
    "UPDATE content SET ai_judgment_basis='[]'", "UPDATE draft SET factcheck_result='[]'",
])
async def test_invalid_or_stale_state_changes_nothing(review_db, sql):
    change(review_db, sql)
    before = snapshot(review_db)
    with pytest.raises(ApiError) as error:
        await review(review_db)
    assert error.value.status_code == 409
    assert snapshot(review_db) == before


@pytest.mark.asyncio
async def test_replays_require_matching_audit_and_do_not_repeat_writes(review_db):
    request = AdminContentReviewRequest(**request_data())
    result = await review(review_db, request)
    before = snapshot(review_db)
    replay = await review(review_db, request)
    assert replay.replayed and replay.audit_log_id == result.audit_log_id
    assert replay.updated_at == result.updated_at
    assert snapshot(review_db) == before
    for data in [request.model_copy(update={"rationale": "다른 사유"}), request.model_copy(update={"action": "reject"}),
                 AdminContentReviewRequest(**request_data())]:
        with pytest.raises(ApiError) as error:
            await review(review_db, data)
        assert error.value.status_code == 409
    assert snapshot(review_db) == before


@pytest.mark.asyncio
async def test_matching_final_state_without_audit_is_never_success(review_db):
    change(review_db, "UPDATE content SET status='active', judgment_status='confirmed'")
    change(review_db, "UPDATE draft SET status='published'")
    with pytest.raises(ApiError) as error:
        await review(review_db)
    assert error.value.status_code == 409 and not rows(review_db, "admin_action_log")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["draft", "audit", "commit", "missing_audit", "invalid_audit_id"])
async def test_failure_rolls_back_content_draft_and_log(review_db, monkeypatch, phase):
    before = snapshot(review_db)
    if phase == "draft":
        change(review_db, "CREATE TRIGGER deny_draft BEFORE UPDATE ON draft BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    elif phase == "audit":
        change(review_db, "CREATE TRIGGER deny_log BEFORE INSERT ON admin_action_log BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    elif phase == "missing_audit":
        change(review_db, "DROP TABLE admin_action_log")
    elif phase == "invalid_audit_id":
        monkeypatch.setattr(repo, "append_audit", AsyncMock(return_value=None))
    else:
        def fail_commit(_):
            raise RuntimeError("fixture commit failure")
        event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises((ApiError, RuntimeError, ValidationError)):
            await review(review_db)
    finally:
        if phase == "commit":
            event.remove(Session, "before_commit", fail_commit)
    for name in before:
        if phase != "missing_audit" or name != "admin_action_log":
            assert rows(review_db, name) == before[name]


@pytest.mark.asyncio
async def test_existing_transaction_and_untrusted_actor_are_rejected(review_db):
    with Session(review_db.engine) as db:
        db.execute(text("SELECT 1"))
        with pytest.raises(RuntimeError, match="fresh transaction"):
            await service.review_content(WriteSession(db), 2, AdminContentReviewRequest(**request_data()),
                user_id=7, permissions=review_db.permissions)
    with pytest.raises(ValueError):
        with Session(review_db.engine) as db:
            await service.review_content(WriteSession(db), 2, AdminContentReviewRequest(**request_data()),
                user_id=True, permissions=review_db.permissions)


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", [None, True, 1, AdminReviewGrant(11, 8), AdminReviewGrant(True, 7)])
async def test_only_verified_write_grant_allows_a_transition(review_db, permission):
    before = snapshot(review_db)
    with pytest.raises(ApiError) as error:
        await review(review_db, permissions=SimpleNamespace(lock_grant=AsyncMock(return_value=permission)))
    assert error.value.status_code == 403 and snapshot(review_db) == before


@pytest.mark.asyncio
async def test_revocation_blocks_replay_and_fresh_write(review_db):
    request = AdminContentReviewRequest(**request_data())
    await review(review_db, request)
    change(review_db, "UPDATE admin_account SET revoked_at=CURRENT_TIMESTAMP")
    before = snapshot(review_db)
    with pytest.raises(ApiError) as error:
        await review(review_db, request)
    assert error.value.status_code == 403 and snapshot(review_db) == before


@pytest.mark.asyncio
async def test_unknown_grant_cannot_leave_content_changed_without_audit(review_db):
    before = snapshot(review_db)
    permissions = SimpleNamespace(lock_grant=AsyncMock(return_value=AdminReviewGrant(999, 7)))
    with pytest.raises(ApiError) as error:
        await review(review_db, permissions=permissions)
    assert error.value.status_code == 503 and snapshot(review_db) == before


@pytest.mark.parametrize("same_request", [True, False])
def test_two_connections_race_and_only_one_transition_commits(review_db, monkeypatch, same_request):
    barrier = Barrier(2)
    original = repo.lock_content
    async def rendezvous(db, content_id):
        result = await original(db, content_id)
        barrier.wait(timeout=10)  # Both requests have read the same original row.
        return result
    monkeypatch.setattr(repo, "lock_content", rendezvous)
    data = request_data()
    requests = [AdminContentReviewRequest(**data), AdminContentReviewRequest(**(data if same_request else request_data(action="reject")))]
    def run(request):
        try:
            return asyncio.run(review(review_db, request))
        except ApiError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, requests))
    successes = [result for result in results if not isinstance(result, ApiError)]
    failures = [result for result in results if isinstance(result, ApiError)]
    assert successes and all(error.status_code == 409 for error in failures)
    assert len(rows(review_db, "admin_action_log")) == 1
    content, draft = rows(review_db, "content")[0], rows(review_db, "draft")[0]
    assert (content["status"], content["judgment_status"], draft["status"]) in [
        ("active", "confirmed", "published"), ("hidden", "failed", "discarded")]
    if same_request:
        monkeypatch.setattr(repo, "lock_content", original)
        assert asyncio.run(review(review_db, requests[0])).replayed


@pytest.mark.asyncio
async def test_postgresql_row_locks_compile_and_no_ddl_is_owned():
    seen = []
    class Result:
        def mappings(self): return self
        def one_or_none(self): return None
    async def execute(statement):
        seen.append(str(statement.compile(dialect=postgresql.dialect())))
        return Result()
    db = SimpleNamespace(execute=execute)
    await repo.lock_content(db, 2)
    await repo.lock_draft(db, 3)
    assert all(sql.endswith("FOR UPDATE") for sql in seen)
    assert not hasattr(repo.audit_log, "create")


@pytest.fixture
def review_api(api, review_db):
    login(api)
    with api.engine.begin() as db:
        db.execute(text("UPDATE auth_session SET csrf_token_hash=:hash"), {"hash": hash_token(csrf_token_for_session(TOKEN))})
    async def isolated_writer():
        with Session(review_db.engine) as db:
            yield WriteSession(db)
    api.app.include_router(router, prefix="/api/v1")
    api.app.dependency_overrides[get_review_db] = isolated_writer
    api.app.dependency_overrides[get_admin_review_permission_provider] = lambda: review_db.permissions
    api.writer = review_db
    return api


def post(api, **kwargs):
    return api.client.post("/api/v1/admin/contents/2/review", json=kwargs.get("json", request_data()),
                          headers=kwargs.get("headers", {"X-CSRF-Token": csrf_token_for_session(TOKEN)}))


def test_http_commit_response_and_replay(review_api):
    data = request_data()
    response = post(review_api, json=data)
    assert response.status_code == 200, response.text
    assert response.json()["auditLogId"] == rows(review_api.writer, "admin_action_log")[0]["admin_action_log_id"]
    assert response.json()["replayed"] is False
    assert response.headers["cache-control"] == "private, no-store"
    assert post(review_api, json=data).json()["replayed"] is True


def test_http_audit_failure_is_not_success_and_retry_uses_same_id(review_api):
    data = request_data()
    before = snapshot(review_api.writer)
    change(review_api.writer, "CREATE TRIGGER deny_log BEFORE INSERT ON admin_action_log BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    response = post(review_api, json=data)
    assert response.status_code == 503 and response.json()["code"] == "ADMIN_REVIEW_UNAVAILABLE"
    assert snapshot(review_api.writer) == before
    change(review_api.writer, "DROP TRIGGER deny_log")
    assert post(review_api, json=data).status_code == 200
    assert len(rows(review_api.writer, "admin_action_log")) == 1


def test_csrf_checks_cookie_binding_even_when_stored_hash_matches_other_token(review_api):
    other = csrf_token_for_session("different-cookie")
    with review_api.engine.begin() as db:
        db.execute(text("UPDATE auth_session SET csrf_token_hash=:hash"), {"hash": hash_token(other)})
    response = post(review_api, headers={"X-CSRF-Token": other})
    assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    assert not rows(review_api.writer, "admin_action_log")


@pytest.mark.parametrize("headers", [
    {}, {"X-CSRF-Token": "bad"}, {"X-CSRF-Token": csrf_token_for_session("different-cookie")},
    {"X-CSRF-Token": csrf_token_for_session(TOKEN), "Origin": "https://evil.example"},
])
def test_http_csrf_changes_nothing(review_api, headers):
    before = snapshot(review_api.writer)
    response = post(review_api, headers=headers)
    assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    assert snapshot(review_api.writer) == before


@pytest.mark.parametrize("failure", ["anonymous", "revoked_session", "inactive_user", "read_only", "default", "revoked_grant", "unavailable", "wrong_target"])
def test_http_permissions_fail_closed(review_api, failure, monkeypatch):
    if failure == "anonymous": review_api.client.cookies.clear()
    elif failure in {"revoked_session", "inactive_user"}:
        with review_api.engine.begin() as db:
            db.execute(text("UPDATE auth_session SET revoked_at=CURRENT_TIMESTAMP" if failure == "revoked_session"
                            else "UPDATE user_account SET account_status='suspended'"))
    elif failure == "read_only": review_api.writer.permissions.allowed = False
    elif failure == "default": del review_api.app.dependency_overrides[get_admin_review_permission_provider]
    elif failure == "revoked_grant": change(review_api.writer, "UPDATE admin_account SET revoked_at=CURRENT_TIMESTAMP")
    elif failure == "wrong_target": review_api.writer.permissions.targets = set()
    else: monkeypatch.setattr(review_api.writer.permissions, "lock_grant", AsyncMock(side_effect=RuntimeError("secret")))
    before = snapshot(review_api.writer)
    response = post(review_api, headers={"X-CSRF-Token": csrf_token_for_session(TOKEN), "X-Role": "admin", "X-Admin": "true"})
    assert response.status_code == (401 if failure in {"anonymous", "revoked_session", "inactive_user"} else 503 if failure == "unavailable" else 403)
    assert "secret" not in response.text and snapshot(review_api.writer) == before


def test_local_bypass_cannot_skip_admin_csrf(review_api, monkeypatch):
    monkeypatch.setattr(settings, "enable_dev_auth_bypass", True)
    monkeypatch.setattr(settings, "enable_dev_api", True)
    assert post(review_api, headers={}).status_code == 403
    review_api.client.cookies.clear()
    assert post(review_api).status_code == 401
    assert not rows(review_api.writer, "admin_action_log")


def test_http_validation_stale_and_missing_content(review_api):
    assert post(review_api, json=request_data(actorId=7)).status_code == 422
    assert post(review_api, json=request_data(expectedUpdatedAt="2020-01-01T00:00:00Z")).status_code == 409
    review_api.writer.permissions.targets.add(999)
    response = review_api.client.post("/api/v1/admin/contents/999/review", json=request_data(),
                                     headers={"X-CSRF-Token": csrf_token_for_session(TOKEN)})
    assert response.status_code == 404 and response.json()["code"] == "CONTENT_NOT_FOUND"
    assert not rows(review_api.writer, "admin_action_log")


def test_runtime_app_does_not_expose_review_even_with_allowing_overrides(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", None)
    app = create_app(enable_dev_api=False)
    app.dependency_overrides[get_admin_review_permission_provider] = lambda: TestPermissionProvider()
    with TestClient(app) as client:
        assert client.post("/api/v1/admin/contents/2/review", json=request_data()).status_code == 404
        assert not any("/admin/" in path for path in client.get("/openapi.json").json()["paths"])
