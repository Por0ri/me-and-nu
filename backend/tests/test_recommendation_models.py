"""Offline relational checks for the recommendation tables.

SQLite exercises keys, unique/check constraints and FK actions with only the
JSONB storage syntax adapted. PostgreSQL DDL is compiled in test_alembic.
"""

import sqlite3

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import BigInteger, Column, DefaultClause, JSON, MetaData, Table, UniqueConstraint, text
from sqlalchemy.dialects import sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable

from app.models import ContentEntityLink, RecommendationItem, RecommendationRound, UserEvent
from app.models.recommendation import DATA_SOURCES, LINK_METHODS, USER_EVENT_TYPES


MODELS = (ContentEntityLink, RecommendationRound, RecommendationItem, UserEvent)
FIRST_EVENT = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def db():
    metadata = MetaData()
    Table("topic", metadata, Column("topic_id", BigInteger, primary_key=True))
    Table("user_account", metadata, Column("user_id", BigInteger, primary_key=True))
    Table(
        "tap", metadata,
        Column("tap_id", BigInteger, primary_key=True),
        Column("user_id", BigInteger, nullable=False),
        Column("topic_id", BigInteger, nullable=False),
        UniqueConstraint("tap_id", "user_id"),
        UniqueConstraint("tap_id", "topic_id"),
    )
    Table(
        "content", metadata,
        Column("content_id", BigInteger, primary_key=True),
        Column("topic_id", BigInteger, nullable=False),
    )
    Table(
        "entity", metadata,
        Column("entity_id", BigInteger, primary_key=True),
        Column("topic_id", BigInteger, nullable=False),
        UniqueConstraint("entity_id", "topic_id"),
    )
    for model in MODELS:
        table = model.__table__.to_metadata(metadata)
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
        if model is UserEvent:
            table.c.event_value.server_default = DefaultClause(text("'{}'"))
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for table in metadata.sorted_tables:
            connection.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
        connection.executemany("INSERT INTO topic VALUES (?)", [(1,), (2,)])
        connection.executemany("INSERT INTO user_account VALUES (?)", [(7,), (8,)])
        connection.executemany("INSERT INTO tap VALUES (?, ?, ?)", [(11, 7, 1), (22, 8, 1)])
        connection.executemany("INSERT INTO content VALUES (?, ?)", [(501, 1), (502, 1)])
        connection.executemany("INSERT INTO entity VALUES (?, ?)", [(101, 1), (103, 1), (202, 2)])
        yield connection


def insert(db, table, **row):
    names = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    db.execute(f"INSERT INTO {table}({names}) VALUES ({marks})", tuple(row.values()))


def insert_link(db, entity_id, *, topic_id=1, link_method="carousel", sort_order=1):
    insert(
        db, "content_entity_link", content_id=501, entity_id=entity_id,
        topic_id=topic_id, link_method=link_method, sort_order=sort_order,
    )


def insert_event(db, user_event_id=1, client_event_id=FIRST_EVENT, **values):
    row = {
        "user_event_id": user_event_id, "client_event_id": client_event_id,
        "user_id": 7, "tap_id": 11, "event_type": "impression", "content_id": 501,
        "data_source": "automated_test", **values,
    }
    insert(db, "user_event", **row)


def test_link_keeps_one_main_work_per_content(db):
    insert_link(db, 101)

    with pytest.raises(sqlite3.IntegrityError):
        insert_link(db, 103)                  # a second work cannot also be order 1
    insert_link(db, 103, sort_order=2)
    assert db.execute(
        "SELECT entity_id, sort_order FROM content_entity_link ORDER BY sort_order"
    ).fetchall() == [(101, 1), (103, 2)]


@pytest.mark.parametrize("link", [
    {"entity_id": 202},                           # the work belongs to another Topic
    {"entity_id": 103, "link_method": "guess"},   # unknown link method
    {"entity_id": 103, "sort_order": 0},          # order starts at 1 (main work)
])
def test_link_rejects_other_topic_and_unknown_values(db, link):
    with pytest.raises(sqlite3.IntegrityError):
        insert_link(db, **link)


def test_round_is_unique_per_tap_day_and_version(db):
    insert(db, "recommendation_round", recommendation_round_id=1, tap_id=11,
           service_date="2026-11-02", algorithm_version="v1")
    insert(db, "recommendation_round", recommendation_round_id=2, tap_id=11,
           service_date="2026-11-02", algorithm_version="latest")

    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "recommendation_round", recommendation_round_id=3, tap_id=11,
               service_date="2026-11-02", algorithm_version="v1")


@pytest.mark.parametrize("item", [
    {"content_id": 502, "rank": 1},   # rank already used in the round
    {"content_id": 501, "rank": 2},   # content already ranked in the round
    {"content_id": 502, "rank": 0},   # ranks start at 1
])
def test_item_rank_and_content_are_unique_within_round(db, item):
    insert(db, "recommendation_round", recommendation_round_id=1, tap_id=11,
           service_date="2026-11-02", algorithm_version="v1")
    insert(db, "recommendation_item", recommendation_item_id=1, recommendation_round_id=1,
           content_id=501, rank=1)

    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "recommendation_item", recommendation_item_id=2, recommendation_round_id=1, **item)


def test_deleting_round_removes_items_but_keeps_events(db):
    insert(db, "recommendation_round", recommendation_round_id=1, tap_id=11,
           service_date="2026-11-02", algorithm_version="v1")
    insert(db, "recommendation_item", recommendation_item_id=1, recommendation_round_id=1,
           content_id=501, rank=1)
    insert_event(db, recommendation_item_id=1)

    db.execute("DELETE FROM recommendation_round")

    assert db.execute("SELECT count(*) FROM recommendation_item").fetchone() == (0,)
    assert db.execute("SELECT recommendation_item_id FROM user_event").fetchall() == [(None,)]


def test_resent_event_is_stored_once(db):
    insert_event(db)

    with pytest.raises(sqlite3.IntegrityError):
        insert_event(db, user_event_id=2)
    assert db.execute("SELECT count(*) FROM user_event").fetchone() == (1,)


@pytest.mark.parametrize("event", [
    {"tap_id": 22},                # the Tap belongs to another user
    {"event_type": "view"},        # not in the activity event list
    {"data_source": "prod"},       # not a known data source
])
def test_event_rejects_other_users_tap_and_unknown_values(db, event):
    with pytest.raises(sqlite3.IntegrityError):
        insert_event(db, **event)


def test_event_value_defaults_to_empty_object(db):
    insert_event(db)

    assert db.execute("SELECT event_value FROM user_event").fetchone() == ("{}",)


def test_model_value_lists_match_migration():
    migration = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("150a601074af").module

    assert migration.LINK_METHODS == LINK_METHODS
    assert migration.EVENT_TYPES == USER_EVENT_TYPES
    assert migration.DATA_SOURCES == DATA_SOURCES
