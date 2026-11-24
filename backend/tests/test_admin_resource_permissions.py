"""Independent member/support read grants and non-URL private searches."""

import asyncio
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update
from starlette.requests import Request

from app.api import admin_deps
from app.api.admin_search import read_admin_search
from app.core.exceptions import ApiError
from app.models import AdminAccount, UserAccount
from app.services.admin_permission_provider import DatabaseAdminPermissionProvider, DenyAdminPermissionProvider
from test_admin_permissions import permission_db, call, seed_grant  # noqa: F401


@pytest.mark.parametrize("method", ["can_read_members", "can_read_support"])
@pytest.mark.parametrize("role,allowed", [("viewer", True), ("operator", True), ("admin", True), ("reviewer", False)])
def test_explicit_resource_grants_use_current_database(permission_db, method, role, allowed):
    seed_grant(permission_db, role)
    operation = getattr(DatabaseAdminPermissionProvider(), method)
    assert call(permission_db, operation, user_id=2) is allowed
    with permission_db.begin() as db:
        from datetime import datetime, timezone
        db.execute(update(AdminAccount).values(revoked_at=datetime.now(timezone.utc)))
    assert call(permission_db, operation, user_id=2) is False


@pytest.mark.parametrize("method", ["can_read_members", "can_read_support"])
@pytest.mark.parametrize("status", ["warned", "suspended", "banned", "withdrawn"])
def test_inactive_admin_cannot_read_resource(permission_db, method, status):
    seed_grant(permission_db, "admin")
    with permission_db.begin() as db:
        db.execute(update(UserAccount).where(UserAccount.user_id == 2).values(account_status=status))
    assert not call(permission_db, getattr(DatabaseAdminPermissionProvider(), method), user_id=2)


@pytest.mark.parametrize("method", ["can_read_members", "can_read_support"])
def test_default_provider_denies_resource(method):
    assert not asyncio.run(getattr(DenyAdminPermissionProvider(), method)(None, user_id=2))


@pytest.mark.parametrize("dependency,method", [
    (admin_deps.require_admin_members, "can_read_members"),
    (admin_deps.require_admin_support, "can_read_support"),
])
def test_content_permission_does_not_imply_other_read_permission(monkeypatch, dependency, method):
    monkeypatch.setattr(admin_deps, "find_auth_session", AsyncMock(return_value=SimpleNamespace(user_id=2, session_state="active")))
    monkeypatch.setattr(admin_deps, "get_user_by_id", AsyncMock(return_value=SimpleNamespace(user_id=2, account_status="active")))
    provider = SimpleNamespace(can_read_contents=AsyncMock(return_value=True))
    setattr(provider, method, AsyncMock(return_value=False))
    with pytest.raises(ApiError) as caught:
        asyncio.run(dependency("local-test-session", None, None, provider))
    assert caught.value.status_code == 403
    provider.can_read_contents.assert_not_awaited()
    getattr(provider, method).assert_awaited_once()
    setattr(provider, method, AsyncMock(side_effect=RuntimeError("private-detail")))
    with pytest.raises(ApiError) as caught:
        asyncio.run(dependency("local-test-session", None, None, provider))
    assert caught.value.status_code == 503
    assert "private-detail" not in caught.value.message


def request(value=None, *, duplicate=False, query=b""):
    headers = [] if value is None else [(b"x-admin-search", value.encode("latin1"))]
    if duplicate:
        headers *= 2
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": query, "headers": headers})


@pytest.mark.parametrize("text", ["", "  김하루  ", "private@example.invalid", "100%_literal", "😀" * 300, "%2F"])
def test_search_decodes_once_with_unicode_and_no_url(text):
    encoded = quote(text, safe="")
    assert read_admin_search(request(encoded), encoded) == text.strip()
    assert read_admin_search(request(), None) == ""


@pytest.mark.parametrize("value", ["%", "%XY", "%FF", "%C0%AF", "%ED%A0%80", "%00", "%0D%0A", "a" * 301, "%61" * 1201, "é"])
def test_malformed_search_is_rejected_without_echo(value):
    with pytest.raises(ApiError) as caught:
        read_admin_search(request(value), value)
    assert caught.value.status_code == 422
    assert caught.value.message == "검색 조건을 확인해 주세요."
    assert caught.value.fields is None


def test_duplicate_header_and_url_search_rejected():
    for req in (request("private", duplicate=True), request(query=b"q=private")):
        with pytest.raises(ApiError) as caught:
            read_admin_search(req, "private")
        assert caught.value.status_code == 422
