"""API-041 contract, pagination, and data-isolation checks."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_optional_auth_context
from app.core.exceptions import ApiError
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import Content, ContentSource, SavedItem, Tap, Topic
from app.repositories import bookmark_repository
from app.services import bookmark_service
from app.services.cursor import encode_cursor


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kw):
    return "JSON"


def _saved(saved_item_id: int, content_id: int, *, status: str = "kept"):
    return SimpleNamespace(
        saved_item_id=saved_item_id, content_id=content_id,
        saved_at=datetime(2026, 9, 29, 9, tzinfo=timezone.utc) + timedelta(minutes=saved_item_id),
        saved_tags=["영화 정보"], status=status,
    )


def _content(content_id: int, *, summary: str | None = None):
    return SimpleNamespace(
        content_id=content_id, topic_id=1, title=f"글 {content_id}",
        summary=summary, image_url=None,
    )


def test_bookmarks_openapi_has_one_get_route_and_v12_fields():
    spec = create_app(enable_dev_api=False).openapi()
    path = spec["paths"]["/api/v1/users/me/bookmarks"]
    assert set(path) == {"get"}
    operation = path["get"]
    assert {param["name"] for param in operation["parameters"] if param["in"] == "query"} == {
        "q", "topicId", "cursor", "limit",
    }
    assert {"200", "401", "403", "404", "422"} <= set(operation["responses"])
    item = spec["components"]["schemas"]["SavedBookmarkItem"]
    assert set(item["properties"]) == {
        "savedItemId", "contentId", "title", "topicId", "tags", "resurfaceEnabled",
        "summary", "imageUrl", "sourceName",
    }


def test_bookmarks_requires_login_and_completed_onboarding(monkeypatch):
    app = create_app(enable_dev_api=False)
    user = SimpleNamespace(user_id=11, onboarding_completed_at=None)
    state = {"session": "anonymous"}

    async def context(_request: Request):
        if state["session"] == "anonymous":
            return None
        return AuthContext(
            session=SimpleNamespace(session_state=state["session"]),
            raw_token="test", user=user,
        )

    async def db():
        yield object()

    app.dependency_overrides[get_optional_auth_context] = context
    app.dependency_overrides[get_db] = db
    list_items = AsyncMock(return_value={"items": [], "nextCursor": None})
    monkeypatch.setattr(bookmark_service, "get_saved_bookmarks", list_items)
    with TestClient(app) as client:
        unauthenticated = client.get("/api/v1/users/me/bookmarks")
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["code"] == "AUTH_REQUIRED"
        state["session"] = "onboarding_pending"
        pending = client.get("/api/v1/users/me/bookmarks")
        assert pending.status_code == 403
        assert pending.json()["code"] == "ONBOARDING_REQUIRED"
        user.onboarding_completed_at = datetime.now(timezone.utc)
        state["session"] = "active"
        assert client.get("/api/v1/users/me/bookmarks?limit=0").status_code == 422
        assert client.get("/api/v1/users/me/bookmarks?topicId=0").status_code == 422
        valid = client.get("/api/v1/users/me/bookmarks?topicId=1&limit=2&q=film")
        assert valid.status_code == 200
        assert valid.json() == {"items": [], "nextCursor": None}
        list_items.assert_awaited_once()
        assert list_items.await_args.kwargs == {
            "topic_id": 1, "q": "film", "cursor": None, "limit": 2,
        }


@pytest.mark.asyncio
async def test_bookmarks_service_cursor_search_and_actual_saved_ids(monkeypatch):
    rows = [
        (_saved(63, 301), _content(301, summary="요약")),
        (_saved(62, 302, status="resurface_off"), _content(302)),
        (_saved(61, 303), _content(303)),
    ]
    calls = []

    async def list_rows(_db, user_id, *, topic_id, q, after, limit):
        calls.append((user_id, topic_id, q, after, limit))
        remaining = rows if after is None else [
            row for row in rows
            if (row[0].saved_at, row[0].saved_item_id) < after
        ]
        return remaining[:limit]

    monkeypatch.setattr(bookmark_service, "require_active_tap", AsyncMock(return_value=object()))
    monkeypatch.setattr(bookmark_service.repo, "list_saved_bookmarks", list_rows)
    monkeypatch.setattr(
        bookmark_service.content_repository, "get_primary_source",
        AsyncMock(return_value=(SimpleNamespace(source_title=None), None)),
    )
    first = await bookmark_service.get_saved_bookmarks(
        object(), 11, topic_id=1, q="  영화  ", cursor=None, limit=2
    )
    body = first.model_dump(by_alias=True)
    assert [item["savedItemId"] for item in body["items"]] == [63, 62]
    assert body["items"][0]["summary"] == "요약"
    assert body["items"][0]["imageUrl"] is None
    assert body["items"][0]["sourceName"] is None
    assert body["items"][1]["resurfaceEnabled"] is False
    assert body["items"][0]["tags"] == ["영화 정보"]
    assert first.next_cursor is not None
    second = await bookmark_service.get_saved_bookmarks(
        object(), 11, topic_id=1, q="영화", cursor=first.next_cursor, limit=2
    )
    assert [item.saved_item_id for item in second.items] == [61]
    assert second.next_cursor is None
    assert calls == [
        (11, 1, "영화", None, 3),
        (11, 1, "영화", (rows[1][0].saved_at, 62), 3),
    ]

    for user_id, topic_id, q in [(12, 1, "영화"), (11, 2, "영화"), (11, 1, "다른 검색")]:
        with pytest.raises(ApiError) as exc:
            await bookmark_service.get_saved_bookmarks(
                object(), user_id, topic_id=topic_id, q=q,
                cursor=first.next_cursor, limit=2,
            )
        assert exc.value.code == "INVALID_CURSOR"
    for cursor in (
        "",
        encode_cursor({"v": 1, "userId": 11, "topicId": 1, "q": "영화", "savedAt": "2026-09-29", "savedItemId": True}),
    ):
        with pytest.raises(ApiError) as exc:
            await bookmark_service.get_saved_bookmarks(
                object(), 11, topic_id=1, q="영화", cursor=cursor, limit=2,
            )
        assert exc.value.code == "INVALID_CURSOR"


@pytest.mark.asyncio
async def test_bookmarks_empty_and_unowned_topic(monkeypatch):
    monkeypatch.setattr(bookmark_service.repo, "list_saved_bookmarks", AsyncMock(return_value=[]))
    missing = AsyncMock(side_effect=ApiError(404, "TOPIC_NOT_FOUND", "활성 Topic을 찾을 수 없습니다."))
    monkeypatch.setattr(bookmark_service, "require_active_tap", missing)
    with pytest.raises(ApiError) as exc:
        await bookmark_service.get_saved_bookmarks(
            object(), 11, topic_id=9, q=None, cursor=None, limit=20,
        )
    assert exc.value.code == "TOPIC_NOT_FOUND"
    bookmark_service.repo.list_saved_bookmarks.assert_not_awaited()
    empty = await bookmark_service.get_saved_bookmarks(
        object(), 11, topic_id=None, q=None, cursor=None, limit=20,
    )
    assert empty.model_dump(by_alias=True) == {"items": [], "nextCursor": None}


@pytest.mark.asyncio
async def test_repository_filters_full_saved_scope_before_pagination(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    tables = [Topic.__table__, Tap.__table__, Content.__table__, ContentSource.__table__, SavedItem.__table__]
    # The production JSONB default has PostgreSQL's :: cast. Only the test
    # SQLite DDL needs a portable default; every fixture supplies saved_tags.
    with monkeypatch.context() as patch:
        patch.setattr(SavedItem.__table__.c.saved_tags, "server_default", None)
        Base.metadata.create_all(engine, tables=tables)
    with engine.begin() as connection:
        # PostgreSQL applies this unique index only to active taps. SQLite
        # ignores postgresql_where, so remove its broader test-only variant.
        connection.exec_driver_sql("DROP INDEX uq_tap_active_user_topic")
        connection.execute(Topic.__table__.insert(), [
            {"topic_id": 1, "topic_code": "movie", "topic_name": "영화", "is_active": True},
            {"topic_id": 2, "topic_code": "music", "topic_name": "음악", "is_active": True},
            {"topic_id": 3, "topic_code": "hidden", "topic_name": "비활성", "is_active": False},
        ])
        connection.execute(Tap.__table__.insert(), [
            {"tap_id": 7, "user_id": 11, "topic_id": 1, "deleted_at": None},
            {"tap_id": 8, "user_id": 11, "topic_id": 2, "deleted_at": None},
            {"tap_id": 9, "user_id": 12, "topic_id": 1, "deleted_at": None},
            {"tap_id": 10, "user_id": 11, "topic_id": 3, "deleted_at": None},
            {"tap_id": 11, "user_id": 11, "topic_id": 1, "deleted_at": datetime(2026, 9, 29)},
        ])
        content_rows = [
            {"content_id": content_id, "topic_id": topic_id, "title": title, "status": status,
             "deleted_at": deleted_at}
            for content_id, topic_id, title, status, deleted_at in [
                (301, 1, "영화 첫글", "active", None),
                (302, 1, "영화 둘째글", "active", None),
                (303, 2, "음악 영화", "active", None),
                (304, 1, "타인 영화", "active", None),
                (305, 1, "삭제 저장", "active", None),
                (306, 1, "비공개 영화", "hidden", None),
                (307, 1, "삭제 글", "active", datetime(2026, 9, 29)),
                (308, 1, "출처 없는 영화", "active", None),
                (309, 3, "비활성 영화", "active", None),
                (310, 1, "해제 Topic 영화", "active", None),
            ]
        ]
        connection.execute(Content.__table__.insert(), content_rows)
        connection.execute(ContentSource.__table__.insert(), [
            {"content_source_id": content_id, "content_id": content_id,
             "source_url": f"https://example.com/{content_id}"}
            for content_id in range(301, 311) if content_id != 308
        ])
        saved_rows = [
            {"saved_item_id": saved_id, "user_id": user_id, "tap_id": tap_id,
             "content_id": content_id, "status": status,
             "saved_at": datetime(2026, 9, 29, 9, saved_id), "saved_tags": ["movie info"]}
            for saved_id, user_id, tap_id, content_id, status in [
                (1, 11, 7, 301, "kept"), (2, 11, 7, 302, "resurface_off"),
                (3, 11, 8, 303, "kept"), (4, 12, 9, 304, "kept"),
                (5, 11, 7, 305, "deleted"), (6, 11, 7, 306, "kept"),
                (7, 11, 7, 307, "kept"), (8, 11, 7, 308, "kept"),
                (9, 11, 10, 309, "kept"), (10, 11, 11, 310, "kept"),
            ]
        ]
        connection.execute(SavedItem.__table__.insert(), saved_rows)
        with Session(bind=connection) as session:
            class SessionAdapter:
                async def execute(self, statement):
                    return session.execute(statement)

            db = SessionAdapter()
            all_rows = await bookmark_repository.list_saved_bookmarks(
                db, 11, topic_id=None, q=None, after=None, limit=20,
            )
            movie_page = await bookmark_repository.list_saved_bookmarks(
                db, 11, topic_id=1, q="영화", after=None, limit=1,
            )
            movie_next = await bookmark_repository.list_saved_bookmarks(
                db, 11, topic_id=1, q="영화",
                after=(movie_page[0][0].saved_at, movie_page[0][0].saved_item_id), limit=2,
            )
            tag_matches = await bookmark_repository.list_saved_bookmarks(
                db, 11, topic_id=1, q="info", after=None, limit=20,
            )
            no_match = await bookmark_repository.list_saved_bookmarks(
                db, 11, topic_id=1, q="없는 검색어", after=None, limit=20,
            )

    assert [saved.saved_item_id for saved, _ in all_rows] == [3, 2, 1]
    assert all_rows[0][0].saved_tags == ["movie info"]
    assert [saved.saved_item_id for saved, _ in movie_page] == [2]
    assert [saved.saved_item_id for saved, _ in movie_next] == [1]
    assert [saved.saved_item_id for saved, _ in tag_matches] == [2, 1]
    assert no_match == []
