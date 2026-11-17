"""Taste edit hints: only the posts the member reacted to, scored like the recommender learns from them."""

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
from sqlalchemy import BigInteger, DefaultClause, Integer, JSON, MetaData, Uuid, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import (
    Content, ContentEntityLink, ContentReaction, Entity, EntityTag, PreferenceTag, SavedItem, Tap, Topic, UserAccount,
    UserEvent,
)
from app.services import taste_hint_service
from test_fe_integration_seed import AsyncAdapter

NOW = datetime(2026, 11, 3, 3, tzinfo=timezone.utc)
MODELS = (Topic, UserAccount, Tap, Content, ContentReaction, SavedItem, UserEvent, Entity, ContentEntityLink, EntityTag,
          PreferenceTag)
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def engine():
    # The SQLite clone of test_recommended_feed.engine for the tables the hints read.
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
        connection.execute(Topic.__table__.insert(), [{"topic_id": 1, "topic_code": "anime", "topic_name": "애니", "is_active": True}])
        connection.execute(UserAccount.__table__.insert(), [
            {"user_id": user_id, "nickname": f"사용자{user_id}", "auth_provider": "kakao", "provider_user_id": str(user_id),
             "onboarding_completed_at": NOW - timedelta(days=40)} for user_id in (7, 8)
        ])
        connection.execute(Tap.__table__.insert(), [{"tap_id": 11, "user_id": 7, "topic_id": 1}, {"tap_id": 22, "user_id": 8, "topic_id": 1}])
        connection.execute(Content.__table__.insert(), [
            {"content_id": content_id, "topic_id": 1, "production_type": "ai", "title": f"글 {content_id}", "content_type": kind,
             "status": "active", "judgment_status": "confirmed", "published_at": NOW - timedelta(days=2)}
            for content_id, kind in [(601, "news"), (602, "music"), (603, "goods"), (604, "guide"), (605, "music"), (606, "news"),
                                     (607, "event"), (608, "character"), (609, "humor"), (610, "review")]
        ])
        connection.execute(Entity.__table__.insert(), [
            {"entity_id": 901, "topic_id": 1, "entity_type": "anime", "name": "진격의 거인", "aliases": [], "is_active": True},
            {"entity_id": 902, "topic_id": 1, "entity_type": "anime", "name": "나루토", "aliases": [], "is_active": True},
            {"entity_id": 903, "topic_id": 1, "entity_type": "anime", "name": "Hidden Work", "aliases": [], "is_active": False},
        ])
        connection.execute(ContentEntityLink.__table__.insert(), [
            {"content_id": content_id, "entity_id": entity_id, "topic_id": 1, "link_method": "carousel", "sort_order": 1}
            for content_id, entity_id in [(601, 901), (602, 901), (603, 902), (604, 902), (605, 903), (606, 901), (607, 901),
                                          (608, 902), (609, 901)]
        ])
        connection.execute(PreferenceTag.__table__.insert(), [
            {"tag_id": tag_id, "topic_id": 1, "kind": kind, "code": code, "display_name": name, "sort_order": order, "is_active": True}
            for tag_id, kind, code, name, order in [(101, "genre", "action", "액션", 1), (102, "genre", "drama", "드라마", 2),
                                                    (103, "genre", "romance", "로맨스", 3), (201, "theme", "growth", "성장", 1),
                                                    (202, "theme", "revenge", "복수", 2)]
        ])
        connection.execute(EntityTag.__table__.insert(), [
            {"entity_id": entity_id, "tag_id": tag_id, "topic_id": 1}
            for entity_id, tag_id in [(901, 101), (901, 102), (901, 201), (902, 101), (902, 202), (903, 103)]
        ])
    yield db_engine
    db_engine.dispose()


def event(connection, content_id, kind, value, at=NOW - timedelta(days=1), tap_id=11, user_id=7):
    connection.execute(UserEvent.__table__.insert(), [{
        "client_event_id": uuid.uuid4(), "user_id": user_id, "tap_id": tap_id, "event_type": kind, "content_id": content_id,
        "event_value": value, "occurred_at": at, "received_at": at, "data_source": "production",
    }])


def reaction(connection, content_id, *, liked=False, ox=None, at=NOW - timedelta(days=1), tap_id=11, user_id=7):
    connection.execute(ContentReaction.__table__.insert(), [{
        "user_id": user_id, "tap_id": tap_id, "content_id": content_id, "is_liked": liked, "liked_at": at if liked else None,
        "ox_feedback": ox, "created_at": at, "updated_at": at,
    }])


def save(connection, content_id, at=NOW - timedelta(days=1), tap_id=11, user_id=7):
    connection.execute(SavedItem.__table__.insert(), [{
        "user_id": user_id, "tap_id": tap_id, "content_id": content_id, "status": "kept", "saved_at": at, "saved_tags": [],
    }])


async def hints(engine, user_id=7):
    with Session(engine) as session:
        return await taste_hint_service.get_taste_hints(AsyncAdapter(session), user_id, 1, now=NOW)


@pytest.mark.asyncio
async def test_hints_count_only_reacted_posts_and_split_them_by_work_chip_and_q3_answer(engine):
    with engine.begin() as connection:
        event(connection, 601, "card_swipe", {"card_index": 5, "card_count": 5})   # every card 0.8
        reaction(connection, 601, liked=True)                                       # + like 0.8
        event(connection, 602, "open", {"scroll_pct": 85})                          # 80%+ of the body 0.8
        event(connection, 603, "card_swipe", {"card_index": 2, "card_count": 5})   # part of the cards 0.2
        save(connection, 603)                                                       # + save 1.0
        event(connection, 604, "open", {"scroll_pct": 50})                          # 30-80% 0.3
        reaction(connection, 604, ox="O")                                           # + 더볼래요 1.0
        reaction(connection, 605, liked=True)                                       # inactive work: Q2 · Q3 only
        event(connection, 606, "impression", {"visible_ms": 4000})                 # only shown
        event(connection, 607, "open", {"scroll_pct": 10})                          # bounce
        reaction(connection, 608, liked=True, ox="X")                               # 그만볼래요 wins
        event(connection, 609, "card_swipe", {"card_index": 3, "card_count": 3}, at=NOW - timedelta(days=31))
        save(connection, 610)                                                       # no work: Q3 only

    result = await hints(engine)

    assert (result.enough, result.contents, result.days) == (True, 6, 30)
    # 나루토 1.2 + 1.3, 진격의 거인 1.6 + 0.8 of 4.9; the hidden work is not offered
    assert [(w.entity_id, w.name, w.percent) for w in result.works] == [(902, "나루토", 51), (901, "진격의 거인", 49)]
    # 액션 gets half of each 진격의 거인 post (two genres) and all of each 나루토 post
    assert [t.name for t in result.genres] == ["액션", "드라마", "로맨스"]
    assert [t.name for t in result.themes] == ["복수", "성장"]
    assert [(t.answer, t.percent) for t in result.types] == [
        ("world", 34), ("ost", 24), ("news", 24), ("goods", 18), ("relationships", 0), ("events", 0)]
    assert sum(t.percent for t in result.types) == 100


@pytest.mark.asyncio
async def test_one_reacted_post_is_enough_and_none_gives_no_hints(engine):
    with engine.begin() as connection:
        event(connection, 603, "impression", {"visible_ms": 4000}, tap_id=22, user_id=8)   # only shown
        reaction(connection, 608, liked=True, ox="X", tap_id=22, user_id=8)               # 그만볼래요

    none = await hints(engine, user_id=8)

    assert (none.enough, none.contents) == (False, 0)
    assert none.works == none.genres == none.themes == none.types == []

    with engine.begin() as connection:
        reaction(connection, 601, liked=True, tap_id=22, user_id=8)                       # one 진격의 거인 news post

    one = await hints(engine, user_id=8)

    assert (one.enough, one.contents) == (True, 1)
    assert [(w.name, w.percent) for w in one.works] == [("진격의 거인", 100)]
    assert ([t.name for t in one.genres], [t.name for t in one.themes]) == (["액션", "드라마"], ["성장"])
    # the 0% answers stay in the list and the edit shows them as they are (decided 2026-11-04)
    assert [(t.answer, t.percent) for t in one.types] == [
        ("news", 100), ("world", 0), ("relationships", 0), ("ost", 0), ("goods", 0), ("events", 0)]


def test_reads_score_like_the_agreed_card_and_body_rules():
    score = taste_hint_service.read_score
    assert score("card_swipe", {"card_index": 2, "card_count": 10}) == 0.2      # one card of a long post is still interest
    assert score("card_swipe", {"card_index": 3, "card_count": 3}) == 0.8
    assert [score("open", {"scroll_pct": pct}) for pct in (29, 30, 79, 80, 100)] == [0.0, 0.3, 0.3, 0.8, 0.8]
    assert score("open", {}) == score("card_swipe", {}) == 0.0


def test_percents_add_up_to_exactly_100_and_ties_round_up_in_shown_order():
    # Equal shares: the one shown first gets the extra point, so 16% never comes before 17%.
    assert taste_hint_service.percents({"a": 1, "b": 1, "c": 1}, ["c", "a", "b"]) == {"c": 34, "a": 33, "b": 33}
    shares = taste_hint_service.percents({"world": 2, "goods": 2, "news": 2, "events": 6}, ["events", "world", "goods", "news"])
    assert shares == {"events": 50, "world": 17, "goods": 17, "news": 16} and sum(shares.values()) == 100
    assert taste_hint_service.percents({"a": 0.0, "b": 0.0}, ["a", "b"]) == {"a": 0, "b": 0}


def _literal(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets))


def test_strengths_and_q3_types_match_the_recommender():
    model = REPO / "model" / "algo"
    if not (model / "content_v1.py").exists():
        pytest.skip("model code is not in this checkout")
    signal = _literal(model / "content_v1.py", "SIGNAL")
    for key, value in taste_hint_service.SIGNAL.items():
        if key in signal:   # card swipe strengths reach the recommender with the agreed rule change
            assert signal[key] == value, key
    answers = _literal(model / "service.py", "ANSWER_TYPES")
    assert {answer: tuple(answers[answer]) for answer in taste_hint_service.ANSWER_TYPES} == taste_hint_service.ANSWER_TYPES


def test_the_hints_route_is_published():
    assert "/api/v1/me/taste/hints" in create_app(enable_dev_api=False).openapi()["paths"]
