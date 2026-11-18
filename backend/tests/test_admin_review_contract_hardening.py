"""FE capabilities and precise, timezone-aware review HTTP responses."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.schemas.admin_content_review import AdminContentReviewResponse
from app.services.admin_permission_provider import get_admin_permission_provider
from app.services.admin_review_permission_provider import get_admin_review_permission_provider
from test_admin_content_api import api, login  # noqa: F401
from test_admin_content_review import (  # noqa: F401
    change, post, request_data, review_api, review_db, rows,
)


def grant(api, role):
    with api.engine.begin() as db:
        db.execute(text("INSERT INTO admin_account (user_id, role) VALUES (7, :role)"), {"role": role})


@pytest.mark.parametrize(("role", "actions"), [
    ("viewer", []), ("operator", ["approve", "reject"]), ("admin", ["approve", "reject"]),
])
def test_detail_uses_actual_grant_for_actions_and_reads_again_after_revocation(api, role, actions):
    login(api)
    grant(api, role)
    del api.app.dependency_overrides[get_admin_permission_provider]
    response = api.client.get("/api/v1/admin/contents/2")
    assert response.status_code == 200, response.text
    assert response.json()["review"] == {
        "allowedActions": actions, "disabledReason": None if actions else "permission_denied",
    }
    with api.engine.begin() as db:
        db.execute(text("UPDATE admin_account SET revoked_at=CURRENT_TIMESTAMP"))
    assert api.client.get("/api/v1/admin/contents/2").status_code == 403


@pytest.mark.parametrize(("sql", "reason"), [
    ("UPDATE content SET deleted_at=CURRENT_TIMESTAMP WHERE content_id=2", "deleted"),
    ("UPDATE content SET status='hidden' WHERE content_id=2", "content_not_reviewable"),
    ("UPDATE content SET judgment_status='confirmed' WHERE content_id=2", "content_not_reviewable"),
    ("UPDATE draft SET status='published'", "draft_not_reviewable"),
    ("UPDATE content SET source_draft_id=NULL WHERE content_id=2", "draft_not_reviewable"),
])
def test_detail_does_not_offer_actions_for_ineligible_state(api, sql, reason):
    login(api)
    grant(api, "operator")
    with api.engine.begin() as db:
        db.execute(text(sql))
    response = api.client.get("/api/v1/admin/contents/2")
    assert response.status_code == 200, response.text
    assert response.json()["review"] == {"allowedActions": [], "disabledReason": reason}


def test_unavailable_write_permission_does_not_return_enabled_metadata(api):
    login(api)
    check = AsyncMock(side_effect=RuntimeError("secret store failure"))
    api.app.dependency_overrides[get_admin_review_permission_provider] = lambda: SimpleNamespace(can_review_content=check)
    response = api.client.get("/api/v1/admin/contents/2")
    assert response.status_code == 503
    assert response.json()["code"] == "ADMIN_PERMISSION_UNAVAILABLE"
    assert "secret" not in response.text


@pytest.mark.parametrize("stamp", [
    "2026-11-13T00:00:00.1234567Z", "2026-11-13T00:00:00", "2026-11-13T00:00:00-00:00",
    "2026-11-13 00:00:00Z", "2026-11-13T00:00:00+00:60", "2026-11-13T00:00:00+24:00",
])
def test_http_rejects_lossy_or_ambiguous_version_without_writing(review_api, stamp):
    response = post(review_api, json=request_data(expectedUpdatedAt=stamp))
    assert response.status_code == 422
    assert rows(review_api.writer, "admin_action_log") == []


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_existing_publication_timestamp_has_offset_on_success_and_replay(review_api, action):
    change(review_api.writer, "UPDATE content SET published_at='2026-11-12 23:42:10.123456'")
    request = request_data(action=action)
    first = post(review_api, json=request)
    assert first.status_code == 200, first.text
    replay = post(review_api, json=request)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    for response in (first, replay):
        body = response.json()
        assert body["publishedAt"] == "2026-11-12T23:42:10.123456Z"
        assert datetime.fromisoformat(body["updatedAt"].replace("Z", "+00:00")).utcoffset() is not None
        assert body["auditLogId"] == first.json()["auditLogId"]


@pytest.mark.parametrize("field", ["published_at", "updated_at"])
def test_review_response_cannot_serialize_naive_datetime(field):
    values = dict(content_id=2, draft_id=3, action="approve", status="active", judgment_status="confirmed",
                  draft_status="published", published_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
                  audit_log_id=1)
    values[field] = datetime(2026, 11, 18)
    with pytest.raises(ValidationError):
        AdminContentReviewResponse(**values)
