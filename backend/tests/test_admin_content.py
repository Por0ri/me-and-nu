"""Execute real read SQL on an isolated in-memory table; no operating DB writes."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql

from app.api.deps import AuthContext, get_optional_auth_context
from app.api.session_store import get_session_store
from app.core.exceptions import ApiError
from app.db.session import get_db
from app.main import create_app
from app.models import UserAccount
from app.repositories import admin_content_repository as repo
from app.schemas.admin_content import AdminContentItem, AdminContentQuery
from app.services import admin_content_service as service


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(text("""CREATE TABLE content (
            content_id BIGINT PRIMARY KEY, title TEXT NOT NULL,
            judgment_status TEXT NOT NULL, status TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL, published_at TIMESTAMP,
            summary TEXT, body TEXT
        )"""))
        # Same timestamps, all review states and publication states, literal
        # wildcard characters, and a visible-but-unreviewed preview combination.
        rows = [
            (1, "달빛 OST", "pending", "active", "2026-11-10 00:00:00"),
            (2, "달빛 소식", "needs_review", "pending_review", "2026-11-11 00:00:00"),
            (3, "달빛 미리보기", "needs_review", "active", "2026-11-11 00:00:00"),
            (4, "100%_OST/특집", "confirmed", "active", "2026-11-12 00:00:00"),
            (5, "100XXOST 특집", "failed", "hidden", "2026-11-12 00:00:00"),
            (6, "삭제 콘텐츠", "failed", "deleted", "2026-11-09 00:00:00"),
        ]
        connection.execute(text("""INSERT INTO content
            (content_id, title, judgment_status, status, created_at, summary, body)
            VALUES (:id, :title, :judgment, :status, :created, NULL, '본문')"""),
            [dict(id=id, title=title, judgment=judgment, status=status, created=created)
             for id, title, judgment, status, created in rows])

        class ReadSession:
            statements = []

            async def scalar(self, statement):
                self.statements.append(statement)
                return connection.scalar(statement)

            async def execute(self, statement):
                self.statements.append(statement)
                return connection.execute(statement)

        yield ReadSession()
    engine.dispose()


@pytest.mark.asyncio
async def test_real_sql_stable_order_pagination_and_total(db):
    ids = []
    for page in (1, 2, 3, 4):
        result = await service.list_contents(db, AdminContentQuery(page=page, pageSize=2))
        assert result.total == 6
        assert result.page == page
        assert result.page_size == 2
        ids.extend(item.content_id for item in result.items)
        if page == 4:
            assert result.items == []
    assert ids == [5, 4, 3, 2, 1, 6]
    assert len(set(ids)) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(("filters", "ids"), [
    ({"q": " 달빛 "}, [3, 2, 1]),
    ({"q": "ost"}, [5, 4, 1]),
    ({"q": "%_"}, [4]),
    ({"q": "/특집"}, [4]),
    ({"q": "' OR 1=1 --"}, []),
    ({"judgmentStatus": "needs_review"}, [3, 2]),
    ({"status": "pending_review"}, [2]),
    ({"judgmentStatus": "needs_review", "status": "active"}, [3]),
    ({"q": "달빛", "judgmentStatus": "needs_review", "status": "pending_review"}, [2]),
    ({"judgmentStatus": "confirmed", "status": "hidden"}, []),
    ({"q": "   "}, [5, 4, 3, 2, 1, 6]),
])
async def test_real_sql_search_independent_filters_and_count(db, filters, ids):
    result = await service.list_contents(db, AdminContentQuery(**filters))
    assert [item.content_id for item in result.items] == ids
    assert result.total == len(ids)


@pytest.mark.asyncio
async def test_postgres_queries_bind_search_and_use_same_filters_for_count(db):
    await repo.list_contents(db, AdminContentQuery(q="50%_", judgmentStatus="pending", status="active", page=3, pageSize=20))
    count, page = [statement.compile(dialect=postgresql.dialect()) for statement in db.statements]
    assert "ILIKE" in str(page)
    assert "ESCAPE '/'" in str(page)
    assert "50%_" not in str(page)
    assert "50/%/_" in page.params.values()
    for key, value in count.params.items():
        assert page.params[key] == value
    assert "content.created_at DESC, content.content_id DESC" in str(page)
    assert 40 in page.params.values()


@pytest.mark.parametrize("data", [
    {"judgmentStatus": "approved"}, {"status": "published"},
    {"page": 0}, {"page": 1_000_001}, {"pageSize": 0}, {"pageSize": 101}, {"q": "a" * 301},
])
def test_query_rejects_invalid_bounds_and_unconfirmed_status_names(data):
    with pytest.raises(ValidationError):
        AdminContentQuery(**data)


def test_dto_contract_keeps_two_axes_and_no_derived_publication_state():
    query = AdminContentQuery()
    assert query.judgment_status is None and query.status is None
    item = AdminContentItem(content_id=3, title="title", judgment_status="needs_review", status="active",
                            created_at=datetime(2026, 11, 12, tzinfo=timezone.utc), published_at=None)
    assert item.model_dump(mode="json", by_alias=True) == {
        "contentId": 3, "title": "title", "judgmentStatus": "needs_review", "status": "active",
        "createdAt": "2026-11-12T00:00:00Z", "publishedAt": None,
    }


@pytest.mark.asyncio
async def test_detail_reuses_sources_and_returns_only_allowlisted_fields(db, monkeypatch):
    source = SimpleNamespace(source_url="https://example.com/source", source_title="출처", source_role="primary", citation_order=1)
    sources = AsyncMock(return_value=[(source, None)])
    monkeypatch.setattr(service, "list_content_sources", sources)
    detail = await service.get_content(db, 2)
    assert detail.status == "pending_review"
    assert detail.body == "본문"
    assert detail.sources[0].publisher is None
    assert detail.sources[0].url == source.source_url
    assert set(detail.model_dump(by_alias=True)) == {
        "contentId", "title", "judgmentStatus", "status", "createdAt", "publishedAt", "summary", "body", "sources",
    }
    sources.assert_awaited_once_with(db, 2)
    with pytest.raises(ApiError) as exc:
        await service.get_content(db, 999)
    assert exc.value.status_code == 404
    assert exc.value.code == "CONTENT_NOT_FOUND"
    assert sources.await_count == 1


@pytest.mark.asyncio
async def test_storage_failure_is_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(repo, "list_contents", AsyncMock(side_effect=RuntimeError("storage unavailable")))
    with pytest.raises(RuntimeError):
        await service.list_contents(object(), AdminContentQuery())


@pytest.mark.parametrize("logged_in", [False, True])
def test_unregistered_api_is_closed_to_anonymous_and_real_user_session(monkeypatch, logged_in):
    app = create_app(enable_dev_api=False)
    user = UserAccount(user_id=7, nickname="회원", account_status="active", auth_provider="kakao", provider_user_id="fixture",
                       onboarding_completed_at=datetime(2026, 11, 1, tzinfo=timezone.utc))
    context = AuthContext(session=SimpleNamespace(session_state="active"), raw_token="test-session", user=user)
    app.dependency_overrides[get_optional_auth_context] = lambda: context if logged_in else None
    app.dependency_overrides[get_session_store] = lambda: None

    async def fake_db():
        yield SimpleNamespace()

    app.dependency_overrides[get_db] = fake_db
    read = AsyncMock(side_effect=AssertionError("Operational data must be unreachable"))
    monkeypatch.setattr(service, "list_contents", read)
    monkeypatch.setattr(service, "get_content", read)
    with TestClient(app) as client:
        if logged_in:
            session = client.get("/api/v1/auth/session")
            assert session.status_code == 200
            assert session.json()["user"] == {"id": 7, "accountType": "user"}
        for path in ("/api/v1/admin/contents", "/api/v1/admin/contents/2"):
            response = client.get(path, headers={"X-Role": "admin", "X-Admin": "true"}, params={"role": "admin"})
            assert response.status_code == 404
            assert "items" not in response.json() and "title" not in response.json()
        assert not any(path.startswith("/api/v1/admin") for path in client.get("/openapi.json").json()["paths"])
    read.assert_not_awaited()
