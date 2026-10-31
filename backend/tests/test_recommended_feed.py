"""Recommended home feed: EXP-30 visit rules, snapshot paging and newest-first fallback."""

import base64
from datetime import datetime, timedelta, timezone
import struct
from urllib.parse import quote
import uuid

import pytest
from sqlalchemy import BigInteger, DefaultClause, Integer, JSON, MetaData, Uuid, create_engine, delete, text, update
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from app.core.exceptions import ApiError
from app.models import (
    Content, ContentEntityLink, ContentReaction, ContentSlide, ContentSource, ContentTag, Entity,
    RecommendationItem, RecommendationRound, SavedItem, Tap, Topic, UserAccount, UserEvent,
)
from app.services import feed_service
from app.services.cursor import encode_cursor
from app.services.feed_ranking import FeedSlot, order_feed, spread_works
from test_fe_integration_seed import AsyncAdapter


NOW = datetime(2026, 11, 2, 3, tzinfo=timezone.utc)          # 12:00 in Seoul
TODAY, YESTERDAY = NOW.date(), NOW.date() - timedelta(days=1)
REASON = "요즘 보는 하이큐!! 관련 글"
MODELS = (Topic, UserAccount, Tap, Content, ContentSlide, ContentSource, ContentTag, ContentReaction,
          SavedItem, RecommendationRound, RecommendationItem, UserEvent, Entity, ContentEntityLink)


def test_order_keeps_rank_drops_opened_and_disliked_and_moves_seen_to_back():
    ranked = [FeedSlot(503, 1, REASON), FeedSlot(501, 2), FeedSlot(505, 3), FeedSlot(507, 4)]
    ordered = order_feed(ranked, [508, 507, 506, 503], opened={501}, negative={505}, shown_today={503, 506})

    assert [slot.content_id for slot in ordered] == [507, 508, 503, 506]
    assert ordered[0] == FeedSlot(507, 4)
    assert ordered[1] == FeedSlot(508)            # newest-first fallback has no recommendation
    assert ordered[2] == FeedSlot(503, 1, REASON)


def ids(slots):
    return [slot.content_id for slot in slots]


def test_spread_puts_another_work_between_repeats():
    works = {1: 10, 2: 10, 3: 20, 4: 10}
    assert ids(spread_works([FeedSlot(1), FeedSlot(2), FeedSlot(3), FeedSlot(4)], works)) == [1, 3, 2, 4]


def test_spread_never_pulls_a_card_shown_today_forward_but_continues_behind_it():
    works = {1: 10, 2: 10, 3: 20}
    ranked = [FeedSlot(1), FeedSlot(2), FeedSlot(3)]
    # 3 would break the 1-2 repeat, but it was shown today.
    assert ids(order_feed(ranked, [], opened=set(), negative=set(), shown_today={3}, main_work=works)) == [1, 2, 3]
    # Among cards shown today, the spread continues from the last fresh card.
    assert ids(order_feed(ranked, [], opened=set(), negative=set(), shown_today={2, 3}, main_work=works)) == [1, 3, 2]


def test_cards_without_a_main_work_never_count_as_repeats():
    assert ids(spread_works([FeedSlot(1), FeedSlot(2), FeedSlot(3)], {3: 10})) == [1, 2, 3]


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
             "provider_user_id": str(user_id), "onboarding_completed_at": NOW - timedelta(days=1)}
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
async def test_feed_spreads_cards_about_the_same_main_work(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    with engine.begin() as connection:
        connection.execute(Entity.__table__.insert(), [
            {"entity_id": 901, "topic_id": 1, "name": "하이큐!!", "aliases": []},
            {"entity_id": 902, "topic_id": 1, "name": "나루토", "aliases": []},
        ])
        connection.execute(ContentEntityLink.__table__.insert(), [
            {"content_id": content_id, "entity_id": entity_id, "topic_id": 1, "link_method": "carousel",
             "sort_order": 1}
            for content_id, entity_id in [(507, 901), (508, 901), (506, 902)]
        ])

    # Without links the order is 507 508 506 504 502 503 (see the paging test).
    assert [card[0] for card in cards(await feed(engine, limit=6))] == [507, 506, 508, 504, 502, 503]


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


def just_onboarded(engine, user_id=8):
    with engine.begin() as connection:
        connection.execute(update(UserAccount).where(UserAccount.user_id == user_id)
                           .values(onboarding_completed_at=NOW - timedelta(seconds=1)))


def record_sleeps(monkeypatch):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(feed_service, "_sleep", sleep)
    return sleeps


@pytest.mark.asyncio
async def test_a_new_account_waits_briefly_for_its_first_round(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    just_onboarded(engine)
    with engine.begin() as connection:
        add_round(connection, 2, tap_id=22, day=TODAY, items=[(502, 1, None)])
    sleeps, calls = record_sleeps(monkeypatch), []
    original = feed_service.recommendation_repo.latest_round

    async def latest_round(db, tap_id, service_dates):      # the recommender writes during the first wait
        calls.append(tap_id)
        return None if len(calls) == 1 else await original(db, tap_id, service_dates)

    monkeypatch.setattr(feed_service.recommendation_repo, "latest_round", latest_round)

    assert cards(await feed(engine, user_id=8, limit=1)) == [(502, 201, None)]
    assert sleeps == [feed_service.FIRST_ROUND_POLL_SECONDS]


@pytest.mark.asyncio
async def test_a_new_account_without_a_round_falls_back_after_the_wait(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    just_onboarded(engine)
    sleeps = record_sleeps(monkeypatch)

    assert cards(await feed(engine, user_id=8)) == [(508, None, None), (507, None, None)]
    assert len(sleeps) == feed_service.FIRST_ROUND_POLLS


@pytest.mark.asyncio
async def test_an_account_that_onboarded_earlier_does_not_wait(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    sleeps = record_sleeps(monkeypatch)

    assert cards(await feed(engine, user_id=8)) == [(508, None, None), (507, None, None)]
    assert sleeps == []


@pytest.mark.asyncio
async def test_recommended_cursor_is_bound_to_its_owner(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    first = await feed(engine)

    for user_id, cursor in [(8, first.next_cursor), (7, first.next_cursor[:-4] + "AAAA")]:
        with pytest.raises(ApiError) as exc:
            await feed(engine, user_id=user_id, cursor=cursor)
        assert exc.value.code == "INVALID_CURSOR"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancel_negative", "like_while_negative"])
async def test_reaction_changes_do_not_reorder_an_existing_visit(engine, monkeypatch, change):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    first = await feed(engine)
    assert [row[0] for row in cards(first)] == [507, 508]
    with engine.begin() as connection:
        statement = delete(ContentReaction) if change == "cancel_negative" else update(ContentReaction).values(
            is_liked=True, updated_at=NOW + timedelta(minutes=1))
        connection.execute(statement.where(ContentReaction.content_id == 505, ContentReaction.user_id == 7))
    second = await feed(engine, cursor=first.next_cursor)
    third = await feed(engine, cursor=second.next_cursor)
    assert [row[0] for row in cards(second)] == [506, 504]
    assert [row[0] for row in cards(third)] == [502, 503]
    assert third.next_cursor is None


@pytest.mark.asyncio
async def test_new_dislike_is_hidden_without_skipping_the_next_card(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    first = await feed(engine)
    with engine.begin() as connection:
        connection.execute(ContentReaction.__table__.insert(), {
            "user_id": 7, "tap_id": 11, "content_id": 506, "ox_feedback": "X",
            "updated_at": NOW + timedelta(minutes=1),
        })
    second = await feed(engine, cursor=first.next_cursor)
    third = await feed(engine, cursor=second.next_cursor)
    assert [row[0] for row in cards(second)] == [504, 502]
    assert [row[0] for row in cards(third)] == [503]
    assert third.next_cursor is None


@pytest.mark.asyncio
async def test_hiding_a_consumed_card_does_not_shift_remaining_pages(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    first = await feed(engine)
    with engine.begin() as connection:
        connection.execute(update(Content).where(Content.content_id == 507).values(status="hidden"))
    second = await feed(engine, cursor=first.next_cursor)
    third = await feed(engine, cursor=second.next_cursor)
    assert [row[0] for row in cards(second)] == [506, 504]
    assert [row[0] for row in cards(third)] == [502, 503]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["hidden", "deleted", "expired", "no_source"])
async def test_snapshot_rechecks_public_visibility_and_fills_the_page(engine, monkeypatch, change):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    first = await feed(engine)
    with engine.begin() as connection:
        if change == "no_source":
            connection.execute(delete(ContentSource).where(ContentSource.content_id == 506))
        else:
            values = {"hidden": {"status": "hidden"}, "deleted": {"deleted_at": NOW},
                      "expired": {"feed_valid_until": datetime(2000, 1, 1, tzinfo=timezone.utc)}}[change]
            connection.execute(update(Content).where(Content.content_id == 506).values(**values))
    second = await feed(engine, cursor=first.next_cursor)
    assert [row[0] for row in cards(second)] == [504, 502]


@pytest.mark.asyncio
async def test_rewritten_snapshot_ids_cannot_read_other_topics_or_hidden_content(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    first = await feed(engine)
    context, _ = feed_service._decode_snapshot_cursor(first.next_cursor, {
        "v": 3, "userId": 7, "topicId": 1, "subtopicId": None,
    })
    with engine.begin() as connection:
        connection.execute(Topic.__table__.insert(), {"topic_id": 2, "topic_code": "other", "topic_name": "다른 분야"})
        connection.execute(Content.__table__.insert(), {
            "content_id": 601, "topic_id": 2, "title": "다른 분야 콘텐츠", "status": "active",
        })
        connection.execute(ContentSource.__table__.insert(), {"content_id": 601, "source_url": "https://example.com/601"})
        connection.execute(update(Content).where(Content.content_id == 507).values(status="hidden"))
    forged = feed_service._snapshot_cursor(context, [601, 507, 506, 504])
    result = await feed(engine, cursor=forged)
    assert [row[0] for row in cards(result)] == [506, 504]
    assert all(row[1] is None for row in cards(result))
    assert result.next_cursor is None


@pytest.mark.asyncio
async def test_snapshot_context_cannot_change_owner_topic_or_round_owner(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    first = await feed(engine)
    context, remaining = feed_service._decode_snapshot_cursor(first.next_cursor, {
        "v": 3, "userId": 7, "topicId": 1, "subtopicId": None,
    })
    for context_change in ({"userId": 8}, {"topicId": 2}, {"roundId": 999}):
        with pytest.raises(ApiError) as exc:
            await feed(engine, cursor=feed_service._snapshot_cursor(context | context_change, remaining))
        assert exc.value.code == "INVALID_CURSOR"
    # Even rewriting the user ID to the caller cannot grant another user's round.
    with pytest.raises(ApiError) as exc:
        await feed(engine, user_id=8, cursor=feed_service._snapshot_cursor(context | {"userId": 8}, remaining))
    assert exc.value.code == "INVALID_CURSOR"


@pytest.mark.asyncio
@pytest.mark.parametrize("packed", [
    b"", b"not-eight", struct.pack(">Q", 0), struct.pack(">Q", 2**63),
    struct.pack(">2Q", 506, 506), struct.pack(">601Q", *range(1, 602)),
], ids=["empty", "misaligned", "zero", "above_bigint", "duplicate", "too_many"])
async def test_snapshot_rejects_invalid_unbounded_or_duplicate_ids(engine, monkeypatch, packed):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    first = await feed(engine)
    header = first.next_cursor.split(".")[0]
    cursor = header + "." + base64.urlsafe_b64encode(packed).rstrip(b"=").decode("ascii")
    with pytest.raises(ApiError) as exc:
        await feed(engine, cursor=cursor)
    assert exc.value.code == "INVALID_CURSOR"


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", [
    "A" * (feed_service.MAX_FEED_CURSOR_LENGTH + 1),
    encode_cursor({"v": 2, "userId": 7, "topicId": 1, "subtopicId": None,
                   "roundId": 1, "asOf": NOW.isoformat(), "offset": 2}),
], ids=["too_long", "legacy_v2"])
async def test_oversized_and_legacy_offset_cursors_require_restart(engine, cursor):
    with pytest.raises(ApiError) as exc:
        await feed(engine, cursor=cursor)
    assert exc.value.code == "INVALID_CURSOR"


def test_snapshot_cursor_and_feed_query_stay_bounded_at_bigint_capacity():
    maximum = 2**63 - 1
    context = {"v": 3, "userId": maximum, "topicId": maximum, "subtopicId": None,
               "roundId": maximum, "asOf": NOW.isoformat()}
    remaining = list(range(maximum - 599, maximum + 1))
    cursor = feed_service._snapshot_cursor(context, remaining)
    assert len(cursor) <= feed_service.MAX_FEED_CURSOR_LENGTH
    request_line = f"GET /api/v1/topics/{maximum}/feed?cursor={quote(cursor)}&limit=100 HTTP/1.1"
    assert len(request_line.encode("ascii")) < 8192
    parsed, restored = feed_service._decode_snapshot_cursor(cursor, {key: context[key] for key in ("v", "userId", "topicId", "subtopicId")})
    assert restored == remaining and parsed == context

    from app.main import create_app
    parameters = create_app(enable_dev_api=False).openapi()["paths"]["/api/v1/topics/{topicId}/feed"]["get"]["parameters"]
    schema = next(p["schema"] for p in parameters if p["name"] == "cursor")
    assert next(option for option in schema["anyOf"] if option["type"] == "string")["maxLength"] == feed_service.MAX_FEED_CURSOR_LENGTH
