"""Recommended home feed: EXP-30 visit rules, snapshot paging and newest-first fallback."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import BigInteger, DefaultClause, Integer, JSON, MetaData, Uuid, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from app.core.exceptions import ApiError
from app.models import (
    Content, ContentReaction, ContentSlide, ContentSource, ContentTag, RecommendationItem,
    RecommendationRound, SavedItem, Tap, Topic, UserAccount, UserEvent,
)
from app.services import feed_service
from app.services.cursor import encode_cursor
from app.services.feed_ranking import FeedSlot, order_feed
from test_fe_integration_seed import AsyncAdapter


NOW = datetime(2026, 11, 2, 3, tzinfo=timezone.utc)          # 12:00 in Seoul
TODAY, YESTERDAY = NOW.date(), NOW.date() - timedelta(days=1)
REASON = "요즘 보는 하이큐!! 관련 글"
MODELS = (Topic, UserAccount, Tap, Content, ContentSlide, ContentSource, ContentTag, ContentReaction,
          SavedItem, RecommendationRound, RecommendationItem, UserEvent)


def test_order_keeps_rank_drops_opened_and_disliked_and_moves_seen_to_back():
    ranked = [FeedSlot(503, 1, REASON), FeedSlot(501, 2), FeedSlot(505, 3), FeedSlot(507, 4)]
    ordered = order_feed(ranked, [508, 507, 506, 503], opened={501}, negative={505}, shown_today={503, 506})

    assert [slot.content_id for slot in ordered] == [507, 508, 503, 506]
    assert ordered[0] == FeedSlot(507, 4)
    assert ordered[1] == FeedSlot(508)            # newest-first fallback has no recommendation
    assert ordered[2] == FeedSlot(503, 1, REASON)


@pytest.fixture
def engine():
    # Clone DDL for SQLite as in test_fe_integration_seed.memory_db, plus the
    # recommendation tables. A column declared UUID gets numeric affinity in
    # SQLite, so the clone stores client_event_id as text.
    metadata = MetaData()
    tables = {model.__table__ for model in MODELS}
    pending = list(tables)
    while pending:
        for foreign_key in pending.pop().foreign_keys:
            if foreign_key.column.table not in tables:
                tables.add(foreign_key.column.table)
                pending.append(foreign_key.column.table)
    for table in tables:
        for column in table.to_metadata(metadata).columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                if column.server_default is not None:
                    column.server_default = DefaultClause(text(str(column.server_default.arg).replace("::jsonb", "")))
            elif isinstance(column.type, UUID):
                column.type = Uuid()
            elif isinstance(column.type, BigInteger):
                column.type = Integer()
    db_engine = create_engine("sqlite:///:memory:")
    metadata.create_all(db_engine)
    with db_engine.begin() as connection:
        connection.execute(Topic.__table__.insert(), [
            {"topic_id": 1, "topic_code": "anime", "topic_name": "애니", "is_active": True},
        ])
        connection.execute(UserAccount.__table__.insert(), [
            {"user_id": user_id, "nickname": f"사용자{user_id}", "auth_provider": "kakao",
             "provider_user_id": str(user_id), "onboarding_completed_at": NOW}
            for user_id in (7, 8)
        ])
        connection.execute(Tap.__table__.insert(), [
            {"tap_id": 11, "user_id": 7, "topic_id": 1}, {"tap_id": 22, "user_id": 8, "topic_id": 1},
        ])
        connection.execute(Content.__table__.insert(), [
            {"content_id": content_id, "topic_id": 1, "production_type": "ai", "title": f"글 {content_id}",
             "status": "active", "judgment_status": "confirmed",
             "published_at": NOW - timedelta(hours=10) + timedelta(minutes=content_id - 500)}
            for content_id in range(501, 509)
        ])
        connection.execute(ContentSource.__table__.insert(), [
            {"content_id": content_id, "source_url": f"https://example.com/{content_id}",
             "source_role": "primary"}
            for content_id in range(501, 509)
        ])
        add_round(connection, 1, tap_id=11, day=TODAY, items=[(503, 1, {"text": REASON}), (501, 2, None),
                                                              (505, 3, None), (507, 4, None)])
        add_event(connection, 501, "open", NOW - timedelta(days=1))         # opened before: drop
        add_event(connection, 503, "impression", NOW - timedelta(hours=1))  # shown today: move back
        add_event(connection, 506, "impression", NOW - timedelta(days=1))   # shown yesterday: no effect
        connection.execute(ContentReaction.__table__.insert(), [
            {"user_id": 7, "tap_id": 11, "content_id": 505, "ox_feedback": "X",
             "updated_at": NOW - timedelta(hours=2)},
        ])
    yield db_engine
    db_engine.dispose()


def add_round(connection, round_id, *, tap_id, day, items):
    connection.execute(RecommendationRound.__table__.insert(), [
        {"recommendation_round_id": round_id, "tap_id": tap_id, "service_date": day, "algorithm_version": "v1"},
    ])
    connection.execute(RecommendationItem.__table__.insert(), [
        {"recommendation_item_id": round_id * 100 + rank, "recommendation_round_id": round_id,
         "content_id": content_id, "rank": rank, "recommend_reason": reason}
        for content_id, rank, reason in items
    ])


def add_event(connection, content_id, event_type, at, tap_id=11, user_id=7):
    connection.execute(UserEvent.__table__.insert(), [
        {"client_event_id": uuid.uuid4(), "user_id": user_id, "tap_id": tap_id, "event_type": event_type,
         "content_id": content_id, "event_value": {}, "occurred_at": at, "received_at": at,
         "data_source": "automated_test"},
    ])


async def feed(engine, user_id=7, cursor=None, limit=2):
    with Session(engine) as session:
        return await feed_service.get_feed(
            AsyncAdapter(session), user_id, 1, subtopic_id=None, cursor=cursor, limit=limit,
        )


def cards(response):
    return [(card.id, card.recommendation_id, card.recommendation_reason)
            for section in response.sections for card in section.contents]


@pytest.mark.asyncio
async def test_recommended_pages_follow_rules_and_ignore_impressions_while_scrolling(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)

    first = await feed(engine)
    assert cards(first) == [(507, 104, None), (508, None, None)]
    with engine.begin() as connection:              # the user sees 507 while scrolling
        add_event(connection, 507, "impression", NOW + timedelta(minutes=1))
    second = await feed(engine, cursor=first.next_cursor)
    third = await feed(engine, cursor=second.next_cursor)

    assert cards(second) == [(506, None, None), (504, None, None)]
    assert cards(third) == [(502, None, None), (503, 101, REASON)]
    assert third.next_cursor is None
    assert first.sections[0].type == "recommended"


@pytest.mark.asyncio
async def test_without_a_recent_round_the_feed_stays_newest_first(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    with engine.begin() as connection:
        add_round(connection, 2, tap_id=22, day=TODAY - timedelta(days=2), items=[(502, 1, None)])

    first = await feed(engine, user_id=8)
    assert cards(first) == [(508, None, None), (507, None, None)]
    with engine.begin() as connection:              # a round arriving mid-scroll keeps the latest pages
        add_round(connection, 3, tap_id=22, day=TODAY, items=[(502, 1, None)])
    # SQLite returns naive times, so build the newest-first cursor PostgreSQL would return.
    latest_cursor = encode_cursor({
        "v": 1, "userId": 8, "topicId": 1, "subtopicId": None,
        "publishedAt": (NOW - timedelta(hours=10) + timedelta(minutes=7)).isoformat(), "contentId": 507,
    })
    second = await feed(engine, user_id=8, cursor=latest_cursor)
    assert cards(second) == [(506, None, None), (505, None, None)]


@pytest.mark.asyncio
async def test_yesterdays_round_covers_the_hours_before_todays_batch(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    with engine.begin() as connection:
        add_round(connection, 2, tap_id=22, day=YESTERDAY, items=[(502, 1, None)])

    assert cards(await feed(engine, user_id=8, limit=1)) == [(502, 201, None)]


@pytest.mark.asyncio
async def test_recommended_cursor_is_bound_to_its_owner(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    first = await feed(engine)

    for user_id, cursor in [(8, first.next_cursor), (7, first.next_cursor[:-4] + "AAAA")]:
        with pytest.raises(ApiError) as exc:
            await feed(engine, user_id=user_id, cursor=cursor)
        assert exc.value.code == "INVALID_CURSOR"
