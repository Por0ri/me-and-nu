"""An uncertain review stays bound to its original session actor and grant."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import ApiError
from app.core.security import csrf_token_for_session
from app.repositories import admin_content_review_repository as repo
from app.schemas.admin_content_review import AdminContentReviewRequest, AdminContentReviewScope
from app.services import admin_content_review_service as service
from app.services.admin_review_permission_provider import AdminReviewGrant, get_admin_review_permission_provider
from test_admin_content_api import TOKEN, api, login  # noqa: F401
from test_admin_content_review import (  # noqa: F401
    WriteSession, change, post, request_data, review, review_api, review_db, rows, snapshot,
)

HEADERS = {"X-CSRF-Token": csrf_token_for_session(TOKEN),
    "X-Admin-Review-Actor": "7", "X-Admin-Review-Grant": "11"}


@pytest.mark.parametrize("name", ["X-Admin-Review-Actor", "X-Admin-Review-Grant"])
@pytest.mark.parametrize("value", [None, "", "0", "-1", "+7", "1.0", "true", " 7", "7 ", "07",
    "7,7", "9223372036854775808", "1" * 30])
def test_missing_or_malformed_scope_headers_never_write(review_api, name, value):
    headers = dict(HEADERS)
    if value is None:
        del headers[name]
    else:
        headers[name] = value
    before = snapshot(review_api.writer)
    response = review_api.client.post("/api/v1/admin/contents/2/review", json=request_data(), headers=headers)
    assert response.status_code == 422, response.text
    assert snapshot(review_api.writer) == before


@pytest.mark.parametrize("name", ["X-Admin-Review-Actor", "X-Admin-Review-Grant"])
def test_repeated_scope_header_fails_closed_even_when_values_match(review_api, name):
    before = snapshot(review_api.writer)
    headers = list(HEADERS.items()) + [(name.lower(), HEADERS[name])]
    response = review_api.client.post("/api/v1/admin/contents/2/review", json=request_data(), headers=headers)
    assert response.status_code == 422
    assert snapshot(review_api.writer) == before


@pytest.mark.parametrize("headers", [
    {"X-Admin-Review-Actor": "8"}, {"X-Admin-Review-Grant": "12"},
    {"X-Admin-Review-Actor": "8", "X-Admin-Review-Grant": "12"},
])
def test_scope_change_conflicts_before_content_or_replay_lookup(review_api, monkeypatch, headers):
    lock = AsyncMock(wraps=repo.lock_content)
    replay = AsyncMock(wraps=repo.previous_action)
    monkeypatch.setattr(repo, "lock_content", lock)
    monkeypatch.setattr(repo, "previous_action", replay)
    before = snapshot(review_api.writer)
    response = post(review_api, headers={**HEADERS, **headers})
    assert response.status_code == 409 and response.json()["code"] == "REVIEW_SCOPE_CHANGED"
    lock.assert_not_awaited()
    replay.assert_not_awaited()
    assert snapshot(review_api.writer) == before


@pytest.mark.asyncio
async def test_direct_service_scope_is_required_and_new_actor_cannot_reuse_old_scope(review_db):
    payload = AdminContentReviewRequest(**request_data())
    before = snapshot(review_db)
    provider = SimpleNamespace(lock_grant=AsyncMock(return_value=AdminReviewGrant(12, 8)))
    with Session(review_db.engine) as db:
        with pytest.raises(TypeError):
            await service.review_content(WriteSession(db), 2, payload, user_id=8, permissions=provider)
        with pytest.raises(ApiError) as error:
            await service.review_content(WriteSession(db), 2, payload, user_id=8, permissions=provider,
                expected_scope=AdminContentReviewScope(actor_user_id=7, admin_account_id=11))
    assert error.value.code == "REVIEW_SCOPE_CHANGED"
    assert snapshot(review_db) == before


@pytest.mark.parametrize("committed", [False, True])
def test_regrant_cannot_adopt_pending_or_committed_old_intent(review_api, committed):
    payload = request_data()
    assert len(payload) == 6
    if committed:
        assert post(review_api, json=payload).status_code == 200
    change(review_api.writer, "UPDATE admin_account SET revoked_at=CURRENT_TIMESTAMP")
    change(review_api.writer, "INSERT INTO admin_account VALUES (12,7,NULL)")
    before = snapshot(review_api.writer)
    response = post(review_api, json=payload)
    assert response.status_code == 409 and response.json()["code"] == "REVIEW_SCOPE_CHANGED"
    assert snapshot(review_api.writer) == before
    assert len(rows(review_api.writer, "admin_action_log")) == int(committed)


@pytest.mark.parametrize("grant", [None, True, 1, AdminReviewGrant(11, 8), AdminReviewGrant(True, 7)])
def test_invalid_read_grant_never_exposes_a_scope(api, grant):
    login(api)
    api.app.dependency_overrides[get_admin_review_permission_provider] = lambda: SimpleNamespace(
        read_grant=AsyncMock(return_value=grant))
    response = api.client.get("/api/v1/admin/contents/2")
    assert response.status_code == 200
    assert response.json()["review"] == {"allowedActions": [], "disabledReason": "permission_denied", "scope": None}


def test_cors_accepts_scope_headers_from_the_configured_frontend(review_api):
    response = review_api.client.options("/api/v1/admin/contents/2/review", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-csrf-token,x-admin-review-actor,x-admin-review-grant",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-admin-review-grant" in response.headers["access-control-allow-headers"]
