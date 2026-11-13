"""Input-only checks. No production DB, permission grant or write endpoint."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import ApiError
from app.main import create_app
from app.schemas.admin_content import AdminContentDetail
from app.schemas.admin_content_input import AdminContentCreateRequest, AdminContentUpdateRequest
from app.services import admin_content_input_service as service


def input_data(**changes):
    return {"title": "제목", "summary": None, "body": None, "sources": [], **changes}


def test_minimum_create_and_complete_update_contract():
    request = AdminContentCreateRequest(title="  제목 😀  ")
    assert request.model_dump(by_alias=True) == input_data(title="제목 😀")
    assert AdminContentUpdateRequest(**request.model_dump()).model_dump() == request.model_dump()
    for missing in ("title", "summary", "body", "sources"):
        values = input_data()
        del values[missing]
        with pytest.raises(ValidationError):
            AdminContentUpdateRequest(**values)


@pytest.mark.parametrize("changes", [
    {"title": " \t\n"}, {"title": None}, {"title": 1}, {"title": "a" * 301},
    {"summary": "a" * 10001}, {"body": "a" * 100001}, {"body": False},
    {"body": "x\x00y"}, {"summary": "x\x7fy"}, {"title": "x\ud800y"},
    {"sources": None}, {"sources": [{"url": "https://example.org"}] * 51},
    {"sources": [{"url": "https://example.org"}, {"url": " https://example.org "}]},
])
def test_invalid_text_types_limits_and_duplicates(changes):
    with pytest.raises(ValidationError):
        AdminContentCreateRequest(**input_data(**changes))


@pytest.mark.parametrize("field", [
    "status", "judgmentStatus", "publishedAt", "productionType", "sourceDraftId", "topicId",
    "creatorChannelId", "agentRunId", "slides", "embedding", "aiJudgmentBasis", "role", "contentId",
])
def test_server_owned_fields_are_not_mass_assignable(field):
    with pytest.raises(ValidationError):
        AdminContentCreateRequest(**input_data(**{field: "injected"}))


@pytest.mark.parametrize("field", ["role", "citationOrder", "publisher", "sourceSiteId", "agentRunSourceId", "contentSourceId"])
def test_source_provenance_is_not_client_assignable(field):
    with pytest.raises(ValidationError):
        AdminContentCreateRequest(**input_data(sources=[{"url": "https://example.org", field: "injected"}]))


@pytest.mark.parametrize("url", [
    "", "/relative", "https:example.org", "javascript:alert(1)", "data:text/html,x", "ftp://example.org",
    "https://user:password@example.org", "https://@example.org", "https://example.org/a b",
    "https://example.org/\npath", "https://example.org\\path", "https://example.org:99999",
    "https://example.org:0", "https://[invalid", "https:///missing", "https://example.org/" + "a" * 1000,
    "https://<bad>.org", "https://exa%mple.org", "https://[::gg]",
])
def test_unsafe_or_invalid_source_urls(url):
    with pytest.raises(ValidationError):
        AdminContentCreateRequest(**input_data(sources=[{"url": url}]))


def test_plain_text_normalization_preserves_inner_lines_and_does_not_canonicalize_urls():
    request = AdminContentCreateRequest(**input_data(title="  😀" + "a" * 299 + "  ", summary=" \n ",
        body="  <script>plain text</script>\n\t본문  ",
        sources=[{"url": " https://example.org/a?utm_source=test#quote ", "title": " \n "}]))
    assert len(request.title) == 300
    assert request.summary is None
    assert request.body == "<script>plain text</script>\n\t본문"
    assert request.sources[0].title is None
    assert request.sources[0].url == "https://example.org/a?utm_source=test#quote"


def test_create_plan_has_no_storage_defaults_or_side_effects():
    request = AdminContentCreateRequest(**input_data())
    plan = service.prepare_create(request)
    assert plan.operation == "create" and plan.content_id is None and plan.original is None
    assert plan.author_input.model_dump() == input_data()
    request.title = "changed later"
    assert plan.author_input.title == "제목"


@pytest.mark.asyncio
async def test_update_reuses_read_boundary_preserves_original_and_never_touches_session(monkeypatch):
    original = AdminContentDetail(content_id=7, title="원래 제목", summary=None, body="원래 본문",
        judgment_status="confirmed", status="active", created_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        published_at=None, sources=[{"url": "https://example.org", "title": "원문", "publisher": "발행처",
                                    "role": "primary", "citationOrder": 7}])
    read = AsyncMock(return_value=original)
    monkeypatch.setattr(service, "get_content", read)
    db = SimpleNamespace()  # Has no add/execute/flush/commit; any write fails.
    before = original.model_dump()
    plan = await service.prepare_update(db, 7, AdminContentUpdateRequest(**input_data(title="수정 제목")))
    read.assert_awaited_once_with(db, 7)
    assert plan.operation == "update" and plan.content_id == 7
    assert plan.author_input.title == "수정 제목"
    assert plan.original.model_dump() == before == original.model_dump()


@pytest.mark.asyncio
async def test_missing_content_and_storage_failure_are_not_success(monkeypatch):
    for error in (ApiError(404, "CONTENT_NOT_FOUND", "없음"), RuntimeError("unavailable")):
        monkeypatch.setattr(service, "get_content", AsyncMock(side_effect=error))
        with pytest.raises(type(error)):
            await service.prepare_update(object(), 7, AdminContentUpdateRequest(**input_data()))


@pytest.mark.asyncio
async def test_invalid_id_or_constructed_request_cannot_reach_reads(monkeypatch):
    read = AsyncMock()
    monkeypatch.setattr(service, "get_content", read)
    for content_id in (0, -1, True, "7", 9007199254740992):
        with pytest.raises(ApiError):
            await service.prepare_update(object(), content_id, AdminContentUpdateRequest(**input_data()))
    with pytest.raises(ValidationError):
        await service.prepare_update(object(), 7, AdminContentUpdateRequest.model_construct(**input_data(title="")))
    read.assert_not_awaited()


def test_runtime_app_has_no_admin_write_routes_even_with_claimed_admin_headers(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", None)
    app = create_app(enable_dev_api=False)
    with TestClient(app) as client:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in ("/api/v1/admin/contents", "/api/v1/admin/contents/7"):
                response = client.request(method, path, json=input_data(), headers={"X-Role": "admin", "X-Admin": "true"})
                assert response.status_code == 404
        assert not any(path.startswith("/api/v1/admin") for path in client.get("/openapi.json").json()["paths"])
