"""Recommended home feed: EXP-30 visit rules, snapshot paging and the fallback list.

11/25: 피드는 추천 300위까지만 (뒤에 최신순 글을 잇지 않음), 추천이 없으면 Q3 묶음을 섞은 비상 목록 300장.
"""

import base64
from datetime import datetime, timedelta, timezone
import struct
from urllib.parse import quote
import uuid

import pytest
from sqlalchemy import BigInteger, DefaultClause, Integer, JSON, MetaData, Uuid, create_engine, delete, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from app.core.exceptions import ApiError
from app.models import (
    Content, ContentEntityLink, ContentReaction, ContentSlide, ContentSource, ContentTag, Entity, PreferenceTag,
    RecommendationItem, RecommendationRound, SavedItem, SearchLog, Tap, TapPreferenceTag, Topic, UserAccount, UserEvent,
)
from app.services import chat_finder, feed_service
from app.services.cursor import encode_cursor
from app.services.feed_ranking import GROUP_OF, FeedSlot, mix_groups, order_feed, spread_works
from test_fe_integration_seed import AsyncAdapter
from test_taste_hints import REPO, _literal


NOW = datetime(2026, 11, 2, 3, tzinfo=timezone.utc)          # 12:00 in Seoul
TODAY, YESTERDAY = NOW.date(), NOW.date() - timedelta(days=1)
REASON = "요즘 보는 하이큐!! 관련 글"
MODELS = (Topic, UserAccount, Tap, Content, ContentSlide, ContentSource, ContentTag, ContentReaction,
          SavedItem, RecommendationRound, RecommendationItem, UserEvent, Entity, ContentEntityLink, TapPreferenceTag,
          SearchLog)


def test_order_keeps_rank_drops_opened_and_disliked_and_moves_seen_to_back():
    ranked = [FeedSlot(503, 1, REASON), FeedSlot(501, 2), FeedSlot(505, 3), FeedSlot(507, 4)]
    ordered = order_feed(ranked, [508, 507, 506, 503], fully_read={501}, negative={505}, shown_today={503, 506})

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
    assert ids(order_feed(ranked, [], fully_read=set(), negative=set(), shown_today={3}, main_work=works)) == [1, 2, 3]
    # Among cards shown today, the spread continues from the last fresh card.
    assert ids(order_feed(ranked, [], fully_read=set(), negative=set(), shown_today={2, 3}, main_work=works)) == [1, 3, 2]


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
        # 피드는 추천 글만 보여 주니까 (11/25) 8편을 다 순위에 넣음
        add_round(connection, 1, tap_id=11, day=TODAY, items=[(503, 1, {"text": REASON}), (501, 2, None),
                                                              (505, 3, None), (507, 4, None), (508, 5, None),
                                                              (506, 6, None), (504, 7, None), (502, 8, None)])
        add_event(connection, 501, "open", NOW - timedelta(days=1), value={"scroll_pct": 90})   # read to the end: drop
        add_event(connection, 502, "open", NOW - timedelta(days=1), value={"scroll_pct": 50})   # opened, not finished: stays
        add_event(connection, 503, "impression", NOW - timedelta(hours=1), value={"visible_ms": 2500})  # seen today: move back
        add_event(connection, 506, "impression", NOW - timedelta(days=1), value={"visible_ms": 5000})   # seen yesterday: no effect
        connection.execute(ContentReaction.__table__.insert(), [
            {"user_id": 7, "tap_id": 11, "content_id": 505, "ox_feedback": "X",
             "updated_at": NOW - timedelta(hours=2)},
        ])
    yield db_engine
    db_engine.dispose()


def add_round(connection, round_id, *, tap_id, day, items):
    # 줄 번호 = round_id × 100 + rank. 100장 넘는 추천은 다른 추천과 번호가 안 겹치게 round_id 를 크게 (9)
    connection.execute(RecommendationRound.__table__.insert(), [
        {"recommendation_round_id": round_id, "tap_id": tap_id, "service_date": day, "algorithm_version": "v1"},
    ])
    connection.execute(RecommendationItem.__table__.insert(), [
        {"recommendation_item_id": round_id * 100 + rank, "recommendation_round_id": round_id,
         "content_id": content_id, "rank": rank, "recommend_reason": reason}
        for content_id, rank, reason in items
    ])


def add_event(connection, content_id, event_type, at, tap_id=11, user_id=7, value=None):
    connection.execute(UserEvent.__table__.insert(), [
        {"client_event_id": uuid.uuid4(), "user_id": user_id, "tap_id": tap_id, "event_type": event_type,
         "content_id": content_id, "event_value": value or {}, "occurred_at": at, "received_at": at,
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


def snapshot(response, user_id=7):
    """다음 쪽 cursor 에 담긴 (roundId, 남은 글 번호). roundId 가 None 이면 비상 목록."""
    context, remaining = feed_service._decode_snapshot_cursor(
        response.next_cursor, {"v": 3, "userId": user_id, "topicId": 1, "subtopicId": None})
    return context["roundId"], remaining


def add_contents(connection, content_ids, kind=lambda content_id: None):
    """공개 글 (출처 있음). 픽스처 501~508 보다 오래된 글이고, 번호가 클수록 새 글."""
    connection.execute(Content.__table__.insert(), [
        {"content_id": content_id, "topic_id": 1, "production_type": "ai", "title": f"글 {content_id}",
         "content_type": kind(content_id), "status": "active", "judgment_status": "confirmed",
         "published_at": NOW - timedelta(days=1) + timedelta(seconds=content_id)}
        for content_id in content_ids
    ])
    connection.execute(ContentSource.__table__.insert(), [
        {"content_id": content_id, "source_url": f"https://example.com/{content_id}", "source_role": "primary"}
        for content_id in content_ids
    ])


# 픽스처 글 유형 → Q3 묶음: 508 507 506 503 인물 쪽 (relationships), 505 소식 (news), 504 음악 (ost), 502 501 세계관 (world)
TYPES = {508: "people", 507: "people", 506: "people", 505: "news", 504: "music", 503: "character",
         502: "guide", 501: "review"}


def set_types(engine):
    with engine.begin() as connection:
        for content_id, kind in TYPES.items():
            connection.execute(update(Content).where(Content.content_id == content_id).values(content_type=kind))


def test_fallback_groups_match_the_model():
    service = REPO / "model" / "algo" / "service.py"
    if not service.exists():
        pytest.skip("model code is not in this checkout")
    answers, types = _literal(service, "Q3_ANSWERS"), _literal(service, "ANSWER_TYPES")
    assert GROUP_OF == {kind: answer for answer in answers for kind in types[answer]}   # 모델 GROUP_OF


def test_feed_cards_match_the_models_daily_count():
    # 피드가 읽는 추천 순위 수 = 모델이 하루에 줄 세우는 수 (모델만 늘리면 피드가 뒤 순위를 못 읽음)
    daily = REPO / "model" / "scripts" / "daily_recommend.py"
    if not daily.exists():
        pytest.skip("model code is not in this checkout")
    assert _literal(daily, "TOP_N") == feed_service.FEED_CARDS


def test_mix_groups_takes_q3_groups_in_turn_newest_first():
    # 최신순 입력. 묶음 순서는 묶음의 가장 새 글 순서 (인물 → 소식 → 음악 → 세계관 → 모르는 유형 → 유형 없음)
    contents = [(10, "people"), (9, "character"), (8, "news"), (7, "people"), (6, "music"), (5, "guide"),
                (4, "review"), (3, "people"), (2, "quiz"), (1, None)]
    # 다른 묶음이 바닥나면 남은 묶음을 최신순으로 이음 (7 · 3)
    assert mix_groups(contents, 300) == [10, 8, 6, 5, 2, 1, 9, 4, 7, 3]
    assert mix_groups(contents, 7) == [10, 8, 6, 5, 2, 1, 9]
    assert mix_groups([], 300) == []


@pytest.mark.asyncio
async def test_recommended_pages_follow_rules_and_ignore_impressions_while_scrolling(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)

    first = await feed(engine)
    assert cards(first) == [(507, 104, None), (508, 105, None)]
    with engine.begin() as connection:              # the user sees 507 while scrolling
        add_event(connection, 507, "impression", NOW + timedelta(minutes=1), value={"visible_ms": 3000})
    second = await feed(engine, cursor=first.next_cursor)
    third = await feed(engine, cursor=second.next_cursor)

    assert cards(second) == [(506, 106, None), (504, 107, None)]
    assert cards(third) == [(502, 108, None), (503, 101, REASON)]
    assert third.next_cursor is None
    assert first.sections[0].type == "recommended"


@pytest.mark.asyncio
async def test_the_ranked_feed_ends_with_the_ranked_cards(engine, monkeypatch):
    # 11/25: 추천 뒤에 최신순 글을 잇지 않음. 공개 글 8편 중 추천된 2편만 보이고 끝
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    with engine.begin() as connection:
        add_round(connection, 2, tap_id=22, day=TODAY, items=[(506, 1, None), (502, 2, None)])

    first = await feed(engine, user_id=8, limit=20)
    assert cards(first) == [(506, 201, None), (502, 202, None)]
    assert first.next_cursor is None


@pytest.mark.asyncio
async def test_the_feed_reads_300_ranks_and_stops_there(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    posts = list(range(1001, 1321))                 # 추천 320장
    with engine.begin() as connection:
        add_contents(connection, posts)
        add_round(connection, 9, tap_id=22, day=TODAY, items=[(post, rank, None) for rank, post in enumerate(posts, 1)])

    first = await feed(engine, user_id=8, limit=100)
    round_id, remaining = snapshot(first, user_id=8)
    assert [card[0] for card in cards(first)] + remaining == posts[:feed_service.FEED_CARDS]
    assert round_id == 9 and cards(first)[0] == (1001, 901, None)
    last = await feed(engine, user_id=8, cursor=feed_service._snapshot_cursor(
        {"v": 3, "userId": 8, "topicId": 1, "subtopicId": None, "roundId": 9, "asOf": NOW.isoformat()}, remaining[-2:]))
    assert cards(last) == [(1299, 1199, None), (1300, 1200, None)]   # 300위까지 추천 번호가 붙음
    assert last.next_cursor is None


@pytest.mark.asyncio
async def test_chat_and_search_still_read_only_the_top_100_ranks(engine):
    # 11/25: 300위까지 보는 건 피드뿐. 챗봇 · 홈 검색은 100위까지라 101위 글은 순위 없는 글처럼 최신순 자리
    link_works(engine)
    fillers = list(range(1001, 1101))
    with engine.begin() as connection:
        add_contents(connection, fillers)
        add_round(connection, 9, tap_id=22, day=datetime.now(feed_service.KST).date(),
                  items=[*((post, rank, None) for rank, post in enumerate(fillers, 1)), (503, 101, None)])

    assert [card.id for card in (await search(engine, "하이큐", user_id=8)).contents] == [508, 507, 505, 503]
    with Session(engine) as session:
        positions = await chat_finder._rank_positions(AsyncAdapter(session), tap_id=22, topic_id=1)
    assert len(positions) == 100 and 503 not in positions


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
async def test_without_a_recent_round_the_feed_mixes_the_newest_cards_by_q3_group(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    set_types(engine)
    with engine.begin() as connection:
        add_round(connection, 2, tap_id=22, day=TODAY - timedelta(days=2), items=[(502, 1, None)])   # too old

    # 묶음을 돌아가며 최신순: 인물 508 · 소식 505 · 음악 504 · 세계관 502, 그다음 인물 507 · 세계관 501, 남은 인물
    first = await feed(engine, user_id=8, limit=3)
    assert cards(first) == [(508, None, None), (505, None, None), (504, None, None)]
    assert snapshot(first, user_id=8) == (None, [502, 507, 501, 506, 503])
    with engine.begin() as connection:              # a round arriving mid-scroll keeps the fallback pages
        add_round(connection, 3, tap_id=22, day=TODAY, items=[(502, 1, None)])
    second = await feed(engine, user_id=8, cursor=first.next_cursor, limit=3)
    third = await feed(engine, user_id=8, cursor=second.next_cursor, limit=3)
    assert cards(second) == [(502, None, None), (507, None, None), (501, None, None)]
    assert cards(third) == [(506, None, None), (503, None, None)]
    assert third.next_cursor is None


@pytest.mark.asyncio
async def test_a_newest_first_cursor_from_before_11_25_still_continues(engine, monkeypatch):
    # 11/25 전 비상 목록 (최신순) 의 v1 cursor: 배포 때 스크롤하던 방문은 최신순 그대로 이어짐
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    # SQLite returns naive times, so build the newest-first cursor PostgreSQL would return.
    latest_cursor = encode_cursor({
        "v": 1, "userId": 8, "topicId": 1, "subtopicId": None,
        "publishedAt": (NOW - timedelta(hours=10) + timedelta(minutes=7)).isoformat(), "contentId": 507,
    })
    assert cards(await feed(engine, user_id=8, cursor=latest_cursor)) == [(506, None, None), (505, None, None)]


@pytest.mark.asyncio
async def test_the_fallback_hides_and_moves_back_like_the_ranked_feed(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    set_types(engine)
    with engine.begin() as connection:
        connection.execute(delete(RecommendationItem))
        connection.execute(delete(RecommendationRound))
        add_event(connection, 504, "impression", NOW - timedelta(minutes=30), value={"visible_ms": 3000})

    # 501 끝까지 읽음 · 505 그만볼래요는 빠지고, 섞은 순서 508 504 502 507 506 503 에서 오늘 본 504 · 503 은 맨 뒤
    result = await feed(engine, limit=10)
    assert cards(result) == [(content_id, None, None) for content_id in (508, 502, 507, 506, 504, 503)]
    assert result.next_cursor is None


@pytest.mark.asyncio
async def test_the_fallback_stops_at_300_cards(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    posts = list(range(1001, 1321))                 # 320장: 4의 배수 80장은 소식, 나머지 240장은 인물

    def kind(content_id):
        return "people" if content_id % 4 else "news"

    with engine.begin() as connection:
        add_contents(connection, posts, kind=kind)

    first = await feed(engine, user_id=8, limit=100)
    round_id, remaining = snapshot(first, user_id=8)
    order = [card[0] for card in cards(first)] + remaining
    assert round_id is None and len(order) == feed_service.FEED_CARDS
    assert all(card[1] is None for card in cards(first))
    # 유형 없는 픽스처 글 (501~508) · 소식 · 인물이 돌아가며 나오고, 소식이 바닥난 168장째부터 인물만 남음
    assert order[:6] == [508, 1320, 1319, 507, 1316, 1318]
    groups = [GROUP_OF[kind(content_id)] if content_id in posts else None for content_id in order]
    assert all(left != right for left, right in zip(groups[:168], groups[1:168]))
    assert set(groups[168:]) == {"relationships"}


@pytest.mark.asyncio
async def test_a_round_without_a_visible_card_falls_back(engine, monkeypatch):
    # 추천 글이 다 숨겨지면 (끝까지 읽음 · 그만볼래요 · 내려감) 빈 피드 대신 비상 목록
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    with engine.begin() as connection:              # 오늘 추천이 501 (끝까지 읽음) · 505 (그만볼래요) · 507 (내려감) 뿐
        connection.execute(delete(RecommendationItem))
        connection.execute(RecommendationItem.__table__.insert(), [
            {"recommendation_item_id": 100 + rank, "recommendation_round_id": 1, "content_id": content_id, "rank": rank}
            for rank, content_id in enumerate([501, 505, 507], 1)
        ])
        connection.execute(update(Content).where(Content.content_id == 507).values(status="hidden"))

    result = await feed(engine, limit=10)
    assert cards(result) == [(content_id, None, None) for content_id in (508, 506, 504, 502, 503)]


@pytest.mark.asyncio
async def test_only_cards_seen_for_2_seconds_today_move_back(engine, monkeypatch):
    # 11/25: 오늘 노출의 visible_ms 합이 2초 이상인 카드만 맨 뒤로. 1초쯤 스쳐 간 카드는 제자리
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    with engine.begin() as connection:
        add_event(connection, 508, "impression", NOW - timedelta(minutes=9), value={"visible_ms": 1200})
        add_event(connection, 508, "impression", NOW - timedelta(minutes=5), value={"visible_ms": 900})    # 합 2.1초: 뒤로
        add_event(connection, 506, "impression", NOW - timedelta(minutes=5), value={"visible_ms": 1900})   # 1.9초: 제자리
        add_event(connection, 504, "impression", NOW - timedelta(minutes=5))                               # 시간 없음: 제자리
        add_event(connection, 502, "impression", NOW - timedelta(minutes=5), tap_id=22, user_id=8,
                  value={"visible_ms": 9000})                                                               # 다른 Tap

    assert [card[0] for card in cards(await feed(engine, limit=10))] == [507, 506, 504, 502, 503, 508]


@pytest.mark.asyncio
async def test_cards_opened_or_swiped_today_move_back_whatever_the_time(engine, monkeypatch):
    # 11/25 사용자 결정: 열었거나 카드를 넘긴 글은 노출 시간과 상관없이 본 카드
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    with engine.begin() as connection:
        add_event(connection, 506, "impression", NOW - timedelta(minutes=5), value={"visible_ms": 800})    # 0.8초만 보고
        add_event(connection, 506, "open", NOW - timedelta(minutes=4), value={"active_ms": 3000, "scroll_pct": 30})   # 열었음
        add_event(connection, 504, "card_swipe", NOW - timedelta(minutes=4), value={"card_index": 2, "card_count": 5})  # 노출 없이 넘김
        add_event(connection, 502, "open", NOW - timedelta(minutes=4), tap_id=22, user_id=8)                # 다른 Tap

    order = [card[0] for card in cards(await feed(engine, limit=10))]
    assert set(order[:4]) == {508, 507, 502, 503}   # 앞쪽 순서는 같은 작품 띄우기가 정함
    assert set(order[4:]) == {506, 504}             # 열었거나 넘긴 카드는 맨 뒤


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
async def test_a_taste_edit_waits_briefly_for_the_rebuilt_round(engine, monkeypatch):
    # A My Page taste edit re-inserts every chip row and drops the recent rounds.
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    with engine.begin() as connection:
        connection.execute(PreferenceTag.__table__.insert(), [
            {"tag_id": 101, "topic_id": 1, "kind": "genre", "code": "action", "display_name": "액션", "is_active": True},
        ])
        connection.execute(TapPreferenceTag.__table__.insert(), [
            {"tap_id": 22, "tag_id": 101, "topic_id": 1, "created_at": NOW - timedelta(seconds=1)},
        ])
        add_round(connection, 2, tap_id=22, day=TODAY, items=[(502, 1, None)])
    sleeps, calls = record_sleeps(monkeypatch), []
    original = feed_service.recommendation_repo.latest_round

    async def latest_round(db, tap_id, service_dates):      # the recommender rebuilds during the first wait
        calls.append(tap_id)
        return None if len(calls) == 1 else await original(db, tap_id, service_dates)

    monkeypatch.setattr(feed_service.recommendation_repo, "latest_round", latest_round)

    assert cards(await feed(engine, user_id=8, limit=1)) == [(502, 201, None)]
    assert sleeps == [feed_service.FIRST_ROUND_POLL_SECONDS]


def link_works(engine):
    with engine.begin() as connection:
        connection.execute(Entity.__table__.insert(), [
            {"entity_id": 901, "topic_id": 1, "entity_type": "anime", "name": "하이큐!!", "aliases": ["Haikyu!!"], "is_active": True},
            {"entity_id": 902, "topic_id": 1, "entity_type": "anime", "name": "나루토", "aliases": [], "is_active": True},
            {"entity_id": 903, "topic_id": 1, "entity_type": "anime", "name": "장송의 프리렌", "aliases": ["Sousou no Frieren"], "is_active": True},
        ])
        connection.execute(ContentEntityLink.__table__.insert(), [
            {"content_id": content_id, "entity_id": entity_id, "topic_id": 1, "link_method": "carousel", "sort_order": 1}
            for content_id, entity_id in [(503, 901), (505, 901), (507, 901), (508, 901), (506, 902)]
        ])


async def search(engine, q, user_id=7, work_id=None):
    with Session(engine) as session:
        return await feed_service.search_feed(AsyncAdapter(session), user_id, 1, q=q, work_id=work_id)


async def suggest(engine, q, user_id=7, limit=feed_service.SUGGEST_WORKS):
    with Session(engine) as session:
        return await feed_service.suggest_works(AsyncAdapter(session), user_id, 1, q=q, limit=limit)


@pytest.mark.asyncio
async def test_search_shows_the_works_cards_in_todays_rank_then_newest(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    link_works(engine)

    result = await search(engine, " 하이큐 ")
    # 503, 507 and 508 are ranked today (1st, 4th, 5th); 505 was marked "그만볼래요".
    assert [card.id for card in result.contents] == [503, 507, 508]
    assert (result.query, result.works) == ("하이큐", ["하이큐!!"])
    assert {card.recommendation_reason for card in result.contents} == {"‘하이큐!!’ 검색 결과예요"}
    assert all(card.recommendation_id is None for card in result.contents)


def search_logs(engine):
    with Session(engine) as session:
        rows = session.scalars(select(SearchLog).order_by(SearchLog.search_log_id)).all()
        return [(r.user_id, r.tap_id, r.search_text, r.picked_work_id, r.matched_work_ids, r.result_count) for r in rows]


@pytest.mark.asyncio
async def test_search_keeps_what_ran_and_what_it_found_but_not_typing(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    link_works(engine)

    await suggest(engine, "하이")                 # 입력 중 미리보기는 남기지 않음
    await search(engine, " 하이큐 ")               # 엔터: 맞은 작품과 보여 준 글 수
    await search(engine, "하이", work_id=901)      # 미리보기에서 고른 작품
    await search(engine, "프리렌")                 # 작품은 있는데 글이 없음
    await search(engine, "없는작품")               # 작품 목록에 없음
    await search(engine, "   ")                    # 빈 검색어는 검색이 아님

    assert search_logs(engine) == [
        (7, 11, "하이큐", None, [901], 3),
        (7, 11, "하이", 901, [901], 3),
        (7, 11, "프리렌", None, [903], 0),
        (7, 11, "없는작품", None, [], 0),
    ]
    with Session(engine) as session:
        assert {row.data_source for row in session.scalars(select(SearchLog))} == {feed_service.settings.event_data_source}


@pytest.mark.asyncio
async def test_search_still_answers_when_its_log_cannot_be_saved(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    link_works(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE search_log"))

    assert [card.id for card in (await search(engine, "하이큐")).contents] == [503, 507, 508]


@pytest.mark.asyncio
async def test_search_matches_aliases_and_returns_nothing_for_other_works(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    link_works(engine)

    assert [card.id for card in (await search(engine, "haikyu")).contents] == [503, 507, 508]
    assert [card.id for card in (await search(engine, "나루토", user_id=8)).contents] == [506]
    assert (await search(engine, "원피스")).works == (await search(engine, "원피스")).contents == []
    assert (await search(engine, "   ")).contents == []


@pytest.mark.asyncio
async def test_search_ignores_spaces_and_names_a_work_without_cards(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    link_works(engine)

    assert [card.id for card in (await search(engine, "하이 큐")).contents] == [503, 507, 508]
    frieren = await search(engine, "장송의프리렌")
    assert (frieren.works, frieren.contents) == (["장송의 프리렌"], [])
    assert (await search(engine, "sousounofrieren")).works == ["장송의 프리렌"]


@pytest.mark.asyncio
async def test_search_for_a_picked_work_shows_only_that_works_cards(engine, monkeypatch):
    # Picking a suggested work narrows the result to it, whatever the typed words match.
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    link_works(engine)

    naruto = await search(engine, "하이큐", user_id=8, work_id=902)
    assert ([card.id for card in naruto.contents], naruto.works) == ([506], ["나루토"])
    assert naruto.contents[0].recommendation_reason == "‘나루토’ 검색 결과예요"
    assert [card.id for card in (await search(engine, "하이큐!!", work_id=901)).contents] == [503, 507, 508]
    unknown = await search(engine, "하이큐", work_id=999)
    assert (unknown.works, unknown.contents) == ([], [])


@pytest.mark.asyncio
async def test_suggestions_match_like_onboarding_and_include_works_without_cards(engine, monkeypatch):
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    link_works(engine)
    with engine.begin() as connection:
        connection.execute(Entity.__table__.insert(), [
            {"entity_id": entity_id, "topic_id": 1, "entity_type": "anime", "name": name, "aliases": [], "is_active": active}
            for entity_id, name, active in [(904, "하이큐!! 극장판", True), (905, "하이큐!! 2기", True),
                                             (906, "하이큐!! 3기", True), (907, "하이큐!! 옛판", False)]
        ])

    found = await suggest(engine, " 하이 큐 ")
    assert (found.query, [(work.entity_id, work.name) for work in found.works]) == (
        "하이 큐", [(901, "하이큐!!"), (904, "하이큐!! 극장판"), (905, "하이큐!! 2기")])
    assert [work.entity_id for work in (await suggest(engine, "하이큐", limit=10)).works] == [901, 904, 905, 906]
    assert [work.name for work in (await suggest(engine, "sousou")).works] == ["장송의 프리렌"]
    assert (await suggest(engine, "   ")).works == []


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
    assert cards(result) == [(506, 106, None), (504, 107, None)]     # numbers come from the caller's round
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
    struct.pack(">2Q", 506, 506),
    struct.pack(f">{feed_service.MAX_SNAPSHOT_ITEMS + 1}Q", *range(1, feed_service.MAX_SNAPSHOT_ITEMS + 2)),
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
async def test_a_cursor_made_before_11_25_with_600_ids_still_reads(engine, monkeypatch):
    # 11/25 전 cursor 는 추천 100 + 최신순 500 = 600개까지 담았고 기한이 없음. 새 스냅샷은 300장이어도 계속 풀림
    monkeypatch.setattr(feed_service, "_now", lambda: NOW)
    context = {"v": 3, "userId": 7, "topicId": 1, "subtopicId": None, "roundId": 1, "asOf": NOW.isoformat()}
    old = [506, 504, 502, 503] + list(range(10_001, 10_597))       # 600개 (뒤 596개는 지금 없는 글)

    second = await feed(engine, cursor=feed_service._snapshot_cursor(context, old))
    third = await feed(engine, cursor=second.next_cursor)
    assert cards(second) == [(506, 106, None), (504, 107, None)]
    assert cards(third) == [(502, 108, None), (503, 101, REASON)]
    assert len(snapshot(third)[1]) == 596


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
    remaining = list(range(maximum - feed_service.MAX_SNAPSHOT_ITEMS + 1, maximum + 1))
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
