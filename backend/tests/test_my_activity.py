"""마이페이지 '내 활동': 좋아요한 콘텐츠(GET /users/me/likes) · 최근 본 콘텐츠(GET /users/me/recent-views).

'본 글' = 상세 화면을 연 것 + 홈 카드를 마지막 장까지 넘긴 것, 최근 30일, 글마다 한 번 (11/6 결정).
일회용 SQLite 에 글 · 반응 · 행동 기록을 넣고 서비스를 그대로 부릅니다.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

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
from app.models import (
    Content, ContentReaction, ContentSlide, ContentSource, ContentTag, SourceSite, Subtopic, Tap, Topic, UserEvent,
)
from app.services import my_activity_service as service
from app.services.my_activity_service import counts_as_viewed

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
ME, OTHER = 11, 12
DAY, HOUR = timedelta(days=1), timedelta(hours=1)


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kw):
    return "JSON"


class SessionAdapter:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def get(self, model, key):
        return self.session.get(model, key)


def reaction(reaction_id, user_id, tap_id, content_id, *, liked, at):
    return ContentReaction(
        content_reaction_id=reaction_id, user_id=user_id, tap_id=tap_id, content_id=content_id,
        is_liked=liked, liked_at=at if liked else None, created_at=at, updated_at=at,
    )


def event(event_id, user_id, tap_id, content_id, kind, at, *, source="production", **value):
    return UserEvent(
        user_event_id=event_id, client_event_id=uuid.uuid4(), user_id=user_id, tap_id=tap_id,
        event_type=kind, content_id=content_id, event_value=value, occurred_at=at, received_at=at,
        data_source=source,
    )


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    models = (Topic, Subtopic, Tap, Content, ContentSlide, SourceSite, ContentSource, ContentTag, ContentReaction, UserEvent)
    with monkeypatch.context() as patch:
        patch.setattr(UserEvent.__table__.c.event_value, "server_default", None)   # PostgreSQL 전용 기본값
        Base.metadata.create_all(engine, tables=[model.__table__ for model in models])
    with Session(engine) as session:
        session.add_all([
            Topic(topic_id=1, topic_code="anime", topic_name="애니", is_active=True),
            Topic(topic_id=2, topic_code="music", topic_name="음악", is_active=True),
            Tap(tap_id=7, user_id=ME, topic_id=1), Tap(tap_id=9, user_id=OTHER, topic_id=1),
        ])
        for content_id, status in [(301, "active"), (302, "active"), (303, "active"), (304, "hidden"), (305, "active")]:
            session.add(Content(content_id=content_id, topic_id=1, title=f"글 {content_id}", status=status))
            session.add(ContentSource(
                content_source_id=content_id, content_id=content_id,
                source_url=f"https://example.com/{content_id}", source_title=f"출처 {content_id}",
            ))
        session.add_all([
            reaction(1, ME, 7, 301, liked=True, at=NOW - 3 * DAY),
            reaction(2, ME, 7, 302, liked=True, at=NOW - DAY),
            reaction(3, ME, 7, 303, liked=False, at=NOW - HOUR),     # 좋아요 취소
            reaction(4, ME, 7, 304, liked=True, at=NOW),             # 숨김 글
            reaction(5, OTHER, 9, 305, liked=True, at=NOW),          # 다른 회원
        ])
        session.add_all([
            event(1, ME, 7, 301, "open", NOW - DAY),
            event(2, ME, 7, 301, "card_swipe", NOW - 5 * HOUR, card_index=4, card_count=4),
            event(3, ME, 7, 302, "card_swipe", NOW - 2 * HOUR, card_index=3, card_count=5),   # 덜 넘김
            event(4, ME, 7, 302, "card_swipe", NOW - 3 * HOUR, card_index=5, card_count=5),
            event(5, ME, 7, 303, "open", NOW - 40 * DAY),                                      # 30일 지남
            event(6, ME, 7, 303, "open", NOW - HOUR, source="automated_test"),                 # 자동 시험 기록
            event(7, ME, 7, 304, "open", NOW - HOUR),                                          # 숨김 글
            event(8, ME, 7, 305, "impression", NOW - HOUR),                                    # 보이기만 함
            event(9, OTHER, 9, 303, "open", NOW - HOUR),                                       # 다른 회원
        ])
        session.commit()
        yield SessionAdapter(session)


@pytest.mark.parametrize(("kind", "value", "expected"), [
    ("open", {}, True),
    ("card_swipe", {"card_index": 5, "card_count": 5}, True),
    ("card_swipe", {"card_index": 3, "card_count": 5}, False),
    ("card_swipe", {}, False),
    ("impression", {"visible_ms": 4000}, False),
])
def test_only_detail_opens_and_last_card_swipes_count_as_viewed(kind, value, expected):
    assert counts_as_viewed(kind, value) is expected


@pytest.mark.asyncio
async def test_likes_are_newest_first_without_cancelled_hidden_or_other_members(db):
    page = await service.get_liked_contents(db, ME, topic_id=None, cursor=None, limit=20)
    assert [(item.content_id, item.liked_at) for item in page.items] == [(302, NOW - DAY), (301, NOW - 3 * DAY)]
    assert page.items[0].source_name == "출처 302" and page.next_cursor is None


@pytest.mark.asyncio
async def test_recent_views_keep_the_last_view_of_each_post_within_30_days(db):
    page = await service.get_recent_views(db, ME, topic_id=None, cursor=None, limit=20, now=NOW)
    assert [(item.content_id, item.viewed_at) for item in page.items] == [(302, NOW - 3 * HOUR), (301, NOW - 5 * HOUR)]
    assert page.next_cursor is None


@pytest.mark.asyncio
@pytest.mark.parametrize("list_name", ["likes", "recent_views"])
async def test_lists_page_with_their_own_cursor_and_topic_filter(db, list_name):
    get = (lambda **kw: service.get_liked_contents(db, ME, **kw)) if list_name == "likes" else (
        lambda **kw: service.get_recent_views(db, ME, now=NOW, **kw))
    first = await get(topic_id=1, cursor=None, limit=1)
    assert [item.content_id for item in first.items] == [302] and first.next_cursor
    second = await get(topic_id=1, cursor=first.next_cursor, limit=1)
    assert [item.content_id for item in second.items] == [301] and second.next_cursor is None

    other = service.get_recent_views if list_name == "likes" else service.get_liked_contents
    for bad in (first.next_cursor + "x", "not-a-cursor"):
        with pytest.raises(ApiError) as caught:
            await get(topic_id=1, cursor=bad, limit=1)
        assert caught.value.code == "INVALID_CURSOR"
    with pytest.raises(ApiError) as caught:   # 다른 목록의 cursor 는 못 씀
        await other(db, ME, topic_id=1, cursor=first.next_cursor, limit=1)
    assert caught.value.code == "INVALID_CURSOR"
    with pytest.raises(ApiError) as caught:   # 관심 분야로 고르지 않은 분야
        await get(topic_id=2, cursor=None, limit=1)
    assert caught.value.code == "TOPIC_NOT_FOUND"


def test_routes_need_a_completed_onboarding_and_pass_the_query(monkeypatch):
    app = create_app(enable_dev_api=False)
    user = SimpleNamespace(user_id=ME, onboarding_completed_at=None)
    state = {"session": "anonymous"}

    async def context(_request: Request):
        if state["session"] == "anonymous":
            return None
        return AuthContext(session=SimpleNamespace(session_state=state["session"]), raw_token="test", user=user)

    async def database():
        yield object()

    app.dependency_overrides[get_optional_auth_context] = context
    app.dependency_overrides[get_db] = database
    likes = AsyncMock(return_value={"items": [], "nextCursor": None})
    views = AsyncMock(return_value={"items": [], "nextCursor": None})
    monkeypatch.setattr(service, "get_liked_contents", likes)
    monkeypatch.setattr(service, "get_recent_views", views)
    with TestClient(app) as client:
        for path in ("/api/v1/users/me/likes", "/api/v1/users/me/recent-views"):
            assert client.get(path).json()["code"] == "AUTH_REQUIRED"
        state["session"] = "onboarding_pending"
        assert client.get("/api/v1/users/me/likes").json()["code"] == "ONBOARDING_REQUIRED"
        user.onboarding_completed_at = NOW
        state["session"] = "active"
        assert client.get("/api/v1/users/me/recent-views?limit=0").status_code == 422
        assert client.get("/api/v1/users/me/likes?topicId=0").status_code == 422
        assert client.get("/api/v1/users/me/likes?topicId=1&limit=5").json() == {"items": [], "nextCursor": None}
        assert client.get("/api/v1/users/me/recent-views?cursor=abc").status_code == 200
    assert likes.await_args.kwargs == {"topic_id": 1, "cursor": None, "limit": 5}
    assert views.await_args.kwargs == {"topic_id": None, "cursor": "abc", "limit": 20}
