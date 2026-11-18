"""Pin what frontend/lib/admin-content-review.ts relies on, over the real HTTP stack.

Reuses the isolated SQLite review fixtures and test-only allowing provider. Nothing
here registers the route, changes the permission provider or touches a service DB.
"""

import re

import pytest

from test_admin_content_api import api  # noqa: F401 - real auth fixture
from test_admin_content_review import change, post, request_data, review_api, review_db, rows  # noqa: F401

STORED = "2026-11-13 00:12:34.123456"  # Content.updated_at with microseconds (naive = UTC in SQLite).
RESPONSE_KEYS = {"contentId", "draftId", "action", "status", "judgmentStatus", "draftStatus",
                 "publishedAt", "updatedAt", "auditLogId", "replayed"}
# Same shape as contentVersion() in frontend/lib/admin-content.ts.
VERSION = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")


@pytest.fixture
def versioned(review_api):
    change(review_api.writer, f"UPDATE content SET updated_at='{STORED}'")
    return review_api


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("expected", [
    "2026-11-13T00:12:34.123456Z", "2026-11-13T00:12:34.123456+00:00", "2026-11-13T09:12:34.123456+09:00",
])
def test_verbatim_version_in_any_offset_commits_and_matches_fe_parser(versioned, action, expected):
    response = post(versioned, json=request_data(action=action, expectedUpdatedAt=expected))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == RESPONSE_KEYS
    assert (body["contentId"], body["action"], body["replayed"]) == (2, action, False)
    assert (body["status"], body["judgmentStatus"], body["draftStatus"]) == (
        ("active", "confirmed", "published") if action == "approve" else ("hidden", "failed", "discarded"))
    assert VERSION.fullmatch(body["updatedAt"])
    assert VERSION.fullmatch(body["publishedAt"]) if action == "approve" else body["publishedAt"] is None
    assert body["auditLogId"] == rows(versioned.writer, "admin_action_log")[0]["admin_action_log_id"]


@pytest.mark.parametrize("stale", [
    "2026-11-13T00:12:34.123Z",  # What a Date round trip would send.
    "2026-11-13T00:12:34Z", "2026-11-13T00:12:34.123457Z",
])
def test_version_without_exact_microseconds_is_a_state_conflict(versioned, stale):
    response = post(versioned, json=request_data(expectedUpdatedAt=stale))
    assert response.status_code == 409 and response.json()["code"] == "REVIEW_STATE_CONFLICT"
    assert not rows(versioned.writer, "admin_action_log")


def test_retry_must_resend_the_identical_request_text(versioned):
    data = request_data(expectedUpdatedAt="2026-11-13T09:12:34.123456+09:00")
    first = post(versioned, json=data)
    assert first.status_code == 200, first.text
    replay = post(versioned, json=data)
    assert replay.status_code == 200 and set(replay.json()) == RESPONSE_KEYS
    assert replay.json()["replayed"] is True and replay.json()["auditLogId"] == first.json()["auditLogId"]
    assert VERSION.fullmatch(replay.json()["updatedAt"]) and VERSION.fullmatch(replay.json()["publishedAt"])
    # Same instant, different offset text: the replay fingerprint keeps it, so this is another request.
    reformatted = post(versioned, json={**data, "expectedUpdatedAt": "2026-11-13T00:12:34.123456Z"})
    assert reformatted.status_code == 409 and reformatted.json()["code"] == "REVIEW_REQUEST_CONFLICT"
    assert len(rows(versioned.writer, "admin_action_log")) == 1
