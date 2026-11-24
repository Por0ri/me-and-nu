"""Registered member HTTP routes with real sessions and current DB grants."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
from sqlalchemy.orm import Session

from app.models import AdminAccount
from app.schemas.admin_members import AdminMemberDetail, AdminMemberListResponse, AdminMemberSummary
from app.services import admin_member_service as service
from app.services.admin_permission_provider import get_admin_permission_provider
from test_admin_content_api import api, login  # noqa: F401
from test_admin_members import BIG_ID, NOW


@pytest.fixture
def members_api(api, monkeypatch):
    summary = AdminMemberSummary(id=str(BIG_ID), nickname="합성 회원", masked_email="pe***@example.test", provider="apple",
                                 domain="애니메이션", joined_at=NOW, last_login_at=NOW, status="warned")
    listing = AsyncMock(return_value=AdminMemberListResponse(items=[summary], page=1, page_size=5, total=1))
    detail = AsyncMock(return_value=AdminMemberDetail(**summary.model_dump(), saved_count=0, liked_count=0))
    monkeypatch.setattr(service, "list_members", listing)
    monkeypatch.setattr(service, "get_member", detail)
    api.member_reads = (listing, detail)
    return api


def grant(api, role):
    del api.app.dependency_overrides[get_admin_permission_provider]
    with Session(api.engine) as db:
        db.add(AdminAccount(admin_account_id=17, user_id=7, role=role))
        db.commit()
    login(api)


def test_anonymous_reads_reject_fake_client_roles_before_service(members_api):
    for path in ("/api/v1/admin/members", f"/api/v1/admin/members/{BIG_ID}"):
        response = members_api.client.get(path, headers={"X-Role": "admin", "X-Admin-Search": "private%40example.test"})
        assert response.status_code == 401
        assert response.headers["Cache-Control"] == "private, no-store"
    for read in members_api.member_reads:
        read.assert_not_awaited()


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_real_roles_read_masked_members_and_revocation_is_immediate(members_api, role):
    grant(members_api, role)
    search = "김하루 private@example.test"
    response = members_api.client.get("/api/v1/admin/members?pageSize=5&status=warned&provider=apple",
                                      headers={"X-Admin-Search": quote(search, safe="")})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "private, no-store"
    assert response.json()["items"][0]["id"] == str(BIG_ID)
    query, passed_search = members_api.member_reads[0].await_args.args[1:]
    assert query.page_size == 5 and query.provider == "apple" and passed_search == search
    assert search not in str(response.request.url)
    detail = members_api.client.get(f"/api/v1/admin/members/{BIG_ID}")
    assert detail.status_code == 200 and detail.json()["history"] is None and detail.json()["notes"] is None
    assert members_api.member_reads[1].await_args.args[1] == BIG_ID
    with Session(members_api.engine) as db:
        db.get(AdminAccount, 17).revoked_at = NOW
        db.commit()
    response = members_api.client.get("/api/v1/admin/members")
    assert response.status_code == 403 and response.json()["code"] == "ADMIN_REQUIRED"
    assert members_api.member_reads[0].await_count == 1


def test_content_capability_alone_cannot_grant_member_reads(members_api):
    login(members_api)
    members_api.app.dependency_overrides[get_admin_permission_provider] = lambda: SimpleNamespace(
        can_read_contents=AsyncMock(return_value=True), can_read_members=AsyncMock(return_value=False))
    response = members_api.client.get("/api/v1/admin/members")
    assert response.status_code == 403
    members_api.member_reads[0].assert_not_awaited()


@pytest.mark.parametrize("params,headers", [
    ({"q": "private@example.test"}, {}),
    ({}, {"X-Admin-Search": "%GG-private@example.test"}),
    ({}, {"X-Admin-Search": "%FF"}),
    ({}, {"X-Admin-Search": "%00"}),
    ({}, {"X-Admin-Search": "x" * 301}),
    ({"status": "dormant"}, {}),
    ({"pageSize": 101}, {}),
])
def test_invalid_search_and_filters_never_reach_member_service(members_api, params, headers):
    grant(members_api, "viewer")
    response = members_api.client.get("/api/v1/admin/members", params=params, headers=headers)
    assert response.status_code == 422
    assert "private@example.test" not in response.text and "%FF" not in response.text
    members_api.member_reads[0].assert_not_awaited()


@pytest.mark.parametrize("identifier", ["0", "-1", "9223372036854775808", "fixture-1"])
def test_invalid_member_identifier_never_reaches_service(members_api, identifier):
    grant(members_api, "viewer")
    response = members_api.client.get("/api/v1/admin/members/" + identifier)
    assert response.status_code == 422
    members_api.member_reads[1].assert_not_awaited()
