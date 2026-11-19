"""토론방 표(a67ddf3dc55f), 토론 개설 알림 종류(94d2da693c10), 토론 설정값 확인.

SQLite에서는 토론 표가 가리키는 기존 표(회원·분야·작품·알림)를 최소 모양으로 만든 뒤, migration과 모델이
같은 규칙을 지키는지 함께 본다. PostgreSQL용 SQL은 실제 DB 없이 문장으로 만들어 확인한다.
"""

from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db.base import Base
from app.models import (
    Debate, DebateMessage, DebateNotification, DebateSearchLog, DebateSide, DebateVote,
    DebateVoteTicket, DebateWork,
)
from app.models.notification import NOTIFICATION_TYPES

PARENT = "47c1a8d6b302"
TABLES_REVISION = "a67ddf3dc55f"
NOTIFICATION_REVISION = "94d2da693c10"
DEBATE_MODELS = (
    Debate, DebateWork, DebateVote, DebateVoteTicket, DebateSide, DebateMessage, DebateNotification,
    DebateSearchLog,
)
DEBATE_TABLES = {model.__tablename__ for model in DEBATE_MODELS}
REFERENCED_TABLES = {"user_account", "topic", "entity", "notification"}
# 토론 표가 가리키는 기존 표의 최소 모양
REFERENCED_SQL = (
    "CREATE TABLE user_account (user_id BIGINT PRIMARY KEY)",
    "CREATE TABLE topic (topic_id BIGINT PRIMARY KEY)",
    "CREATE TABLE entity (entity_id BIGINT PRIMARY KEY, topic_id BIGINT NOT NULL, UNIQUE (entity_id, topic_id))",
    "CREATE TABLE notification (notification_id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL)",
)
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

debate = Debate.__table__
work = DebateWork.__table__
vote = DebateVote.__table__
ticket = DebateVoteTicket.__table__
side = DebateSide.__table__
message = DebateMessage.__table__
link = DebateNotification.__table__
search_log = DebateSearchLog.__table__


def _config(db):
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.attributes["connection"] = db
    return config


def _prepare(db, schema_source):
    """회원 7·8·9, 분야 1·2, 작품 100(분야 1)·200(분야 2), 알림 500(회원 7)·501(회원 8)."""
    db.exec_driver_sql("PRAGMA foreign_keys = ON")
    for statement in REFERENCED_SQL:
        db.exec_driver_sql(statement)
    db.exec_driver_sql("INSERT INTO user_account VALUES (7), (8), (9)")
    db.exec_driver_sql("INSERT INTO topic VALUES (1), (2)")
    db.exec_driver_sql("INSERT INTO entity VALUES (100, 1), (200, 2)")
    db.exec_driver_sql("INSERT INTO notification VALUES (500, 7), (501, 8)")
    if schema_source == "migration":
        config = _config(db)
        command.stamp(config, PARENT)
        command.upgrade(config, TABLES_REVISION)
    else:
        Base.metadata.create_all(db, tables=[model.__table__ for model in DEBATE_MODELS])


def _new_debate(db, **values):
    row = {
        "topic_id": 1, "author_user_id": 7, "title": "나루토와 사스케, 최종 승자는?",
        "side_a_label": "나루토", "side_b_label": "사스케", "voting_ends_at": NOW + timedelta(days=7),
        **values,
    }
    return db.scalar(debate.insert().values(**row).returning(debate.c.debate_id))


def _count(db, table):
    return db.scalar(sa.select(sa.func.count()).select_from(table))


@pytest.mark.parametrize("schema_source", ["migration", "model"])
def test_debate_row_rules(schema_source):
    with sa.create_engine("sqlite://").begin() as db:
        _prepare(db, schema_source)
        debate_id = _new_debate(db)
        row = db.execute(sa.select(debate).where(debate.c.debate_id == debate_id)).mappings().one()
        assert row["status"] == "voting"
        assert (row["vote_count"], row["side_a_count"], row["side_b_count"], row["message_count"]) == (0, 0, 0, 0)
        assert row["created_at"] is not None and row["opened_at"] is None and row["closes_at"] is None

        broken_rows = (
            {"status": "paused"},                                         # 없는 상태
            {"opened_at": NOW},                                           # 투표 중인데 방 시각이 있음
            {"status": "open", "opened_at": NOW},                         # 열린 방인데 종료 시각이 없음
            {"status": "open", "opened_at": NOW, "closes_at": NOW},       # 종료가 개설보다 늦지 않음
            {"status": "closed"},                                         # 종료됐는데 열린 적이 없음
            {"status": "expired", "opened_at": NOW, "closes_at": NOW + timedelta(hours=48)},  # 소멸했는데 열린 적이 있음
            {"side_b_label": "나루토"},                                    # 양쪽 이름이 같음
            {"title": ""},
            {"side_a_label": ""},
            {"vote_count": -1},
            {"message_count": -1},
            {"topic_id": 3},                                              # 없는 분야
            {"author_user_id": 42},                                       # 없는 회원
        )
        for broken in broken_rows:
            with pytest.raises(sa.exc.IntegrityError):
                _new_debate(db, **broken)

        opened = _new_debate(db, status="open", opened_at=NOW, closes_at=NOW + timedelta(hours=48), vote_count=10)
        db.execute(debate.update().where(debate.c.debate_id == opened).values(status="closed", side_a_count=6, side_b_count=4))
        _new_debate(db, status="expired", vote_count=3)
        assert _count(db, debate) == 3


@pytest.mark.parametrize("schema_source", ["migration", "model"])
def test_votes_sides_messages_and_works_follow_their_rules(schema_source):
    with sa.create_engine("sqlite://").begin() as db:
        _prepare(db, schema_source)
        debate_id = _new_debate(db)

        db.execute(vote.insert().values(debate_id=debate_id, user_id=7))
        with pytest.raises(sa.exc.IntegrityError):                       # 한 주제에 1인 1표
            db.execute(vote.insert().values(debate_id=debate_id, user_id=7))
        db.execute(vote.insert().values(debate_id=debate_id, user_id=8))

        db.execute(ticket.insert().values(user_id=7, balance=2, refilled_at=NOW))
        with pytest.raises(sa.exc.IntegrityError):
            db.execute(ticket.insert().values(user_id=8, balance=-1, refilled_at=NOW))

        db.execute(side.insert().values(debate_id=debate_id, user_id=7, side="a"))
        with pytest.raises(sa.exc.IntegrityError):
            db.execute(side.insert().values(debate_id=debate_id, user_id=8, side="c"))
        with pytest.raises(sa.exc.IntegrityError):                       # 한 방에서 진영은 하나
            db.execute(side.insert().values(debate_id=debate_id, user_id=7, side="b"))

        db.execute(message.insert().values(debate_id=debate_id, user_id=7, side="a", body="사스케가 이겨!"))
        # 진영을 고르지 않고도 채팅할 수 있다 (진영 선택과 채팅은 별개).
        db.execute(message.insert().values(debate_id=debate_id, user_id=9, side=None, body="아직 고민 중이에요"))
        for broken in ({"body": ""}, {"side": "c"}, {"user_id": 42}):
            with pytest.raises(sa.exc.IntegrityError):
                db.execute(message.insert().values(**{"debate_id": debate_id, "user_id": 7, "side": "a", "body": "a"} | broken))
        first, second = (
            db.scalar(message.insert().values(debate_id=debate_id, user_id=8, side="b", body=body).returning(message.c.message_id))
            for body in ("아니", "그게 아니라")
        )
        assert first < second                                              # 메시지 번호는 저장 순서대로 커진다

        # 작품은 주제와 같은 분야만 붙고, 같은 순서 자리에는 하나만 들어간다.
        db.execute(work.insert().values(debate_id=debate_id, entity_id=100, topic_id=1, position=0))
        for broken in (
            {"entity_id": 200, "topic_id": 2},                             # 다른 분야 작품 (주제는 분야 1)
            {"entity_id": 200, "topic_id": 1},                             # 작품 200은 분야 2라서 짝이 맞지 않음
            {"entity_id": 100, "position": 1},                             # 같은 작품을 두 번
            {"entity_id": 999},                                            # 없는 작품
            {"position": -1},
        ):
            with pytest.raises(sa.exc.IntegrityError):
                db.execute(work.insert().values(**{"debate_id": debate_id, "entity_id": 100, "topic_id": 1, "position": 0} | broken))

        db.execute(link.insert().values(notification_id=500, debate_id=debate_id, user_id=7))
        with pytest.raises(sa.exc.IntegrityError):                       # 같은 토론으로 같은 회원에게 두 번
            db.execute(link.insert().values(notification_id=501, debate_id=debate_id, user_id=7))

        db.execute(search_log.insert().values(
            topic_id=1, search_text="나루토", picked_entity_id=100, matched_entity_ids=[100], result_count=1,
            data_source="automated_test",
        ))
        for broken in ({"result_count": -1}, {"data_source": "unknown"}):
            with pytest.raises(sa.exc.IntegrityError):
                db.execute(search_log.insert().values(**{
                    "search_text": "루피", "matched_entity_ids": [], "result_count": 0, "data_source": "automated_test",
                } | broken))
        assert db.scalar(sa.select(search_log.c.matched_entity_ids)) == [100]


@pytest.mark.parametrize("schema_source", ["migration", "model"])
def test_deleting_rows_keeps_debate_history_and_drops_personal_rows(schema_source):
    with sa.create_engine("sqlite://").begin() as db:
        _prepare(db, schema_source)
        kept, removed = _new_debate(db), _new_debate(db, author_user_id=8)
        for debate_id in (kept, removed):
            db.execute(vote.insert().values(debate_id=debate_id, user_id=7))
            db.execute(side.insert().values(debate_id=debate_id, user_id=7, side="a"))
            db.execute(message.insert().values(debate_id=debate_id, user_id=7, side="a", body="안녕"))
            db.execute(work.insert().values(debate_id=debate_id, entity_id=100, topic_id=1, position=0))
        db.execute(link.insert().values(notification_id=500, debate_id=kept, user_id=7))
        db.execute(link.insert().values(notification_id=501, debate_id=removed, user_id=8))
        db.execute(ticket.insert().values(user_id=7, balance=1, refilled_at=NOW))
        db.execute(search_log.insert().values(
            topic_id=1, search_text="나루토", picked_entity_id=100, matched_entity_ids=[100], result_count=2,
            data_source="automated_test",
        ))

        # 연결된 작품은 함부로 지울 수 없다.
        with pytest.raises(sa.exc.IntegrityError):
            db.exec_driver_sql("DELETE FROM entity WHERE entity_id = 100")

        # 주제를 지우면 그 주제의 투표·진영·채팅·작품 연결·알림 연결이 함께 지워진다.
        db.execute(debate.delete().where(debate.c.debate_id == removed))
        for table in (vote, side, message, work, link):
            assert db.scalar(sa.select(sa.func.count()).select_from(table).where(table.c.debate_id == removed)) == 0
        assert _count(db, vote) == _count(db, side) == _count(db, message) == _count(db, work) == 1

        # 회원을 지우면 그 회원의 투표·진영·채팅·투표권은 지워지고, 올린 주제는 작성자만 비운 채 남는다.
        db.exec_driver_sql("DELETE FROM user_account WHERE user_id = 7")
        assert db.scalar(sa.select(debate.c.author_user_id).where(debate.c.debate_id == kept)) is None
        assert _count(db, vote) == _count(db, side) == _count(db, message) == _count(db, ticket) == _count(db, link) == 0
        assert _count(db, work) == 1

        # 알림을 지우면 연결도 지워지고, 작품이 사라진 검색 기록은 고른 작품만 비운다.
        db.execute(work.delete())
        db.exec_driver_sql("DELETE FROM entity WHERE entity_id = 100")
        assert db.scalar(sa.select(search_log.c.picked_entity_id)) is None


def _structure(db):
    inspector = sa.inspect(db)
    structure = {}
    for table in sorted(DEBATE_TABLES):
        structure[table] = {
            "columns": [
                (column["name"], str(column["type"]), column["nullable"], column.get("default"))
                for column in inspector.get_columns(table)
            ],
            "primary_key": inspector.get_pk_constraint(table)["constrained_columns"],
            "foreign_keys": sorted(
                (fk["name"], tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]),
                 (fk.get("options") or {}).get("ondelete"))
                for fk in inspector.get_foreign_keys(table)
            ),
            "unique": sorted((unique["name"], tuple(unique["column_names"])) for unique in inspector.get_unique_constraints(table)),
            "indexes": sorted((index["name"], tuple(index["column_names"])) for index in inspector.get_indexes(table)),
            "checks": sorted((check["name"], check["sqltext"]) for check in inspector.get_check_constraints(table)),
        }
    return structure


def test_migration_builds_the_same_tables_as_the_models():
    built = {}
    for schema_source in ("migration", "model"):
        with sa.create_engine("sqlite://").begin() as db:
            _prepare(db, schema_source)
            built[schema_source] = _structure(db)
    assert built["migration"] == built["model"]
    assert set(built["model"]) == DEBATE_TABLES
    assert built["model"]["debate_work"]["foreign_keys"] == [
        ("fk_debate_work_debate_topic", ("debate_id", "topic_id"), "debate", ("debate_id", "topic_id"), "CASCADE"),
        ("fk_debate_work_entity_topic", ("entity_id", "topic_id"), "entity", ("entity_id", "topic_id"), "RESTRICT"),
    ]


def test_debate_migration_roundtrip_refuses_to_discard_data():
    with sa.create_engine("sqlite://").begin() as db:
        _prepare(db, "migration")
        config = _config(db)
        assert DEBATE_TABLES <= set(sa.inspect(db).get_table_names())

        def refuses_downgrade():
            with pytest.raises(RuntimeError, match="Refusing to discard debate data"):
                command.downgrade(config, PARENT)
            assert db.scalar(sa.text("SELECT version_num FROM alembic_version")) == TABLES_REVISION

        _new_debate(db)
        refuses_downgrade()
        db.execute(debate.delete())
        db.execute(ticket.insert().values(user_id=7, balance=2, refilled_at=NOW))
        refuses_downgrade()
        db.execute(ticket.delete())
        db.execute(search_log.insert().values(
            search_text="결말", matched_entity_ids=[], result_count=0, data_source="automated_test",
        ))
        refuses_downgrade()
        db.execute(search_log.delete())

        command.downgrade(config, PARENT)
        assert set(sa.inspect(db).get_table_names()) == REFERENCED_TABLES | {"alembic_version"}
        assert db.scalar(sa.text("SELECT count(*) FROM user_account")) == 3
        assert db.scalar(sa.text("SELECT count(*) FROM notification")) == 2


def _offline_sql(monkeypatch, revision_id):
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(revision_id).module
    buffer = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer})
    monkeypatch.setattr(revision, "op", Operations(context))
    revision.upgrade()
    upgrade_sql = buffer.getvalue()
    revision.downgrade()
    return upgrade_sql, buffer.getvalue()[len(upgrade_sql):]


def test_debate_tables_migration_is_additive_and_guards_downgrade(monkeypatch):
    upgrade_sql, downgrade_sql = _offline_sql(monkeypatch, TABLES_REVISION)
    for table in DEBATE_TABLES:
        assert f"CREATE TABLE {table} (" in upgrade_sql
    for column in ("debate_id BIGSERIAL", "message_id BIGSERIAL", "search_log_id BIGSERIAL", "matched_entity_ids JSONB NOT NULL"):
        assert column in upgrade_sql
    assert "TIMESTAMP WITH TIME ZONE" in upgrade_sql
    assert "FOREIGN KEY(debate_id, topic_id) REFERENCES debate (debate_id, topic_id) ON DELETE CASCADE" in upgrade_sql
    assert "FOREIGN KEY(entity_id, topic_id) REFERENCES entity (entity_id, topic_id) ON DELETE RESTRICT" in upgrade_sql
    assert "FOREIGN KEY(author_user_id) REFERENCES user_account (user_id) ON DELETE SET NULL" in upgrade_sql
    # 새 표만 만든다. 기존 표(알림 포함)와 보류 중인 생년월일 칼럼은 건드리지 않는다.
    assert "ALTER TABLE" not in upgrade_sql and "birth_date" not in upgrade_sql
    for model in DEBATE_MODELS:
        for index in model.__table__.indexes:
            assert str(sa.schema.CreateIndex(index).compile(dialect=postgresql.dialect())) in upgrade_sql

    lock = downgrade_sql.index(
        "LOCK TABLE debate_search_log, debate_notification, debate_message, debate_side, debate_vote_ticket, "
        "debate_vote, debate_work, debate IN ACCESS EXCLUSIVE MODE"
    )
    guard = downgrade_sql.index("RAISE EXCEPTION 'Refusing to discard debate data'")
    first_drop = downgrade_sql.index("DROP TABLE debate_search_log")
    last_drop = downgrade_sql.index("DROP TABLE debate;")
    assert lock < guard < first_drop < last_drop


def test_debate_opened_migration_only_widens_the_type_rule_and_guards_downgrade(monkeypatch):
    upgrade_sql, downgrade_sql = _offline_sql(monkeypatch, NOTIFICATION_REVISION)
    assert "DROP CONSTRAINT ck_notification_type" in upgrade_sql
    # 넓힌 규칙은 모델의 알림 종류 목록과 같다.
    model_rule = "notification_type IN (" + ", ".join(f"'{kind}'" for kind in NOTIFICATION_TYPES) + ")"
    assert f"CHECK ({model_rule})" in upgrade_sql
    assert "'recommended_post', 'debate_opened'" in upgrade_sql
    assert "CREATE TABLE" not in upgrade_sql and "ADD COLUMN" not in upgrade_sql

    assert (
        downgrade_sql.index("LOCK TABLE notification")
        < downgrade_sql.index("RAISE EXCEPTION 'Refusing to discard debate_opened notifications'")
        < downgrade_sql.index("DROP CONSTRAINT")
    )
    previous = ", ".join(f"'{kind}'" for kind in NOTIFICATION_TYPES if kind != "debate_opened")
    assert f"CHECK (notification_type IN ({previous}))" in downgrade_sql


def test_debate_feature_is_off_by_default_with_the_meeting_rules():
    config = Settings(_env_file=None, database_url="postgresql://unused")
    assert config.debate_enabled is False
    assert (
        config.debate_vote_threshold, config.debate_voting_period_minutes, config.debate_room_duration_minutes,
    ) == (10, 7 * 24 * 60, 48 * 60)
    assert (config.debate_vote_ticket_max, config.debate_vote_ticket_refill_minutes, config.debate_active_per_user) == (3, 60, 1)
    assert (config.debate_message_rate_count, config.debate_message_rate_window_seconds) == (5, 10)
    assert (config.debate_stream_per_user, config.debate_stream_heartbeat_seconds) == (3, 20)


@pytest.mark.parametrize("override", [
    {"debate_vote_threshold": 0}, {"debate_room_duration_minutes": 0}, {"debate_vote_ticket_max": 0},
    {"debate_vote_ticket_max": 101}, {"debate_message_rate_count": 0}, {"debate_stream_per_user": 0},
])
def test_debate_rules_reject_values_that_would_break_the_feature(override):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql://unused", **override)
