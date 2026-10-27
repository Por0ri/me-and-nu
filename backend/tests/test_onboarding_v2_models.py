"""Offline schema and relational-invariant checks for onboarding v2.

SQLite exercises row constraints/FKs with only the JSONB storage syntax adapted.
PostgreSQL migration DDL is separately compiled; live migration coverage belongs
to the isolated PostgreSQL integration tests.
"""

from io import StringIO
import sqlite3

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import BigInteger, Column, DefaultClause, JSON, MetaData, Table, UniqueConstraint, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from app.models import (
    Entity, EntityExternalId, EntityTag, OnboardingAnswersRecord,
    PreferenceTag, Tap, TapEntity, TapPreferenceTag,
)


CATALOG_MODELS = (Entity, EntityExternalId, PreferenceTag, EntityTag, TapEntity, TapPreferenceTag)


@pytest.fixture
def relational_db():
    metadata = MetaData()
    Table("topic", metadata, Column("topic_id", BigInteger, primary_key=True))
    Table(
        "tap", metadata,
        Column("tap_id", BigInteger, primary_key=True),
        Column("topic_id", BigInteger, nullable=False),
        UniqueConstraint("tap_id", "topic_id"),
    )
    for model in (*CATALOG_MODELS, OnboardingAnswersRecord):
        table = model.__table__.to_metadata(metadata)
        if model is Entity:
            table.c.aliases.type = JSON()
            table.c.aliases.server_default = DefaultClause(text("'[]'"))
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for table in metadata.sorted_tables:
            connection.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
        connection.executemany("INSERT INTO topic VALUES (?)", [(1,), (2,)])
        connection.executemany("INSERT INTO tap VALUES (?, ?)", [(11, 1), (22, 2)])
        connection.executemany(
            "INSERT INTO entity(entity_id, topic_id, name) VALUES (?, ?, ?)",
            [(101, 1, "First work"), (202, 2, "Other topic")],
        )
        connection.executemany(
            "INSERT INTO preference_tag(tag_id, topic_id, kind, code, display_name) VALUES (?, ?, ?, ?, ?)",
            [(1001, 1, "genre", "action", "Action"), (2002, 2, "theme", "school", "School")],
        )
        yield connection


def test_old_answer_insert_keeps_v1_defaults_and_values(relational_db):
    relational_db.execute(
        "INSERT INTO onboarding_answers(tap_id, q1_choice, q2_choice) VALUES (11, 'world', 'releases')"
    )
    assert relational_db.execute(
        "SELECT schema_version, q1_choice, q2_choice, consumption_type FROM onboarding_answers"
    ).fetchone() == (1, "world", "releases", None)


@pytest.mark.parametrize("choice", ["world", "relationships", "ost", "goods", "events", "news"])
def test_v2_stores_consumption_without_reinterpreting_legacy_answers(relational_db, choice):
    relational_db.execute(
        "INSERT INTO onboarding_answers(tap_id, schema_version, consumption_type) VALUES (11, 2, ?)",
        (choice,),
    )
    assert relational_db.execute(
        "SELECT schema_version, q1_choice, q2_choice, consumption_type FROM onboarding_answers"
    ).fetchone() == (2, None, None, choice)


@pytest.mark.parametrize("version,q1,q2,consumption", [
    (1, None, "releases", None),
    (1, "world", None, None),
    (1, "world", "releases", "world"),
    (1, "invalid", "releases", None),
    (1, "world", "invalid", None),
    (2, None, None, None),
    (2, "world", None, "world"),
    (2, None, "releases", "world"),
    (2, "world", "releases", "world"),
    (2, None, None, "invalid"),
    (3, None, None, "world"),
    (None, "world", "releases", None),
])
def test_version_shape_rejects_null_loopholes_and_mixed_answers(
    relational_db, version, q1, q2, consumption
):
    with pytest.raises(sqlite3.IntegrityError):
        relational_db.execute(
            "INSERT INTO onboarding_answers(tap_id, schema_version, q1_choice, q2_choice, consumption_type) "
            "VALUES (11, ?, ?, ?, ?)",
            (version, q1, q2, consumption),
        )


@pytest.mark.parametrize("table,columns,values", [
    ("entity_tag", "entity_id,tag_id,topic_id", (101, 2002, 1)),
    ("entity_tag", "entity_id,tag_id,topic_id", (202, 1001, 1)),
    ("tap_entity", "tap_id,entity_id,topic_id", (11, 202, 1)),
    ("tap_entity", "tap_id,entity_id,topic_id", (22, 101, 1)),
    ("tap_preference_tag", "tap_id,tag_id,topic_id", (11, 2002, 1)),
    ("tap_preference_tag", "tap_id,tag_id,topic_id", (22, 1001, 1)),
])
def test_each_side_of_composite_relationship_rejects_other_topic(
    relational_db, table, columns, values
):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        relational_db.execute(
            f"INSERT INTO {table}({columns}) VALUES (?, ?, ?)", values
        )


def test_catalog_restricts_deletion_while_tap_cascades_only_its_preferences(relational_db):
    relational_db.execute("INSERT INTO entity_tag VALUES (101, 1001, 1)")
    relational_db.execute(
        "INSERT INTO tap_entity(tap_id,entity_id,topic_id) VALUES (11,101,1)"
    )
    relational_db.execute(
        "INSERT INTO tap_preference_tag(tap_id,tag_id,topic_id) VALUES (11,1001,1)"
    )
    relational_db.execute(
        "INSERT INTO onboarding_answers(tap_id,schema_version,consumption_type) VALUES (11,2,'ost')"
    )
    relational_db.execute(
        "INSERT INTO onboarding_answers(tap_id,q1_choice,q2_choice) VALUES (22,'news','events')"
    )
    for sql in (
        "DELETE FROM entity WHERE entity_id=101",
        "DELETE FROM preference_tag WHERE tag_id=1001",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            relational_db.execute(sql)
    relational_db.execute("DELETE FROM tap WHERE tap_id=11")
    for table in ("tap_entity", "tap_preference_tag"):
        assert relational_db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
    assert relational_db.execute(
        "SELECT tap_id,schema_version,q1_choice,q2_choice FROM onboarding_answers"
    ).fetchall() == [(22, 1, "news", "events")]
    assert relational_db.execute("SELECT * FROM entity_tag").fetchall() == [(101,1001,1)]
    assert relational_db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_external_ids_are_unique_by_provider_and_one_per_entity(relational_db):
    relational_db.execute("INSERT INTO entity_external_id VALUES ('anilist','500',101)")
    for values in [("anilist","500",202), ("anilist","600",101)]:
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            relational_db.execute("INSERT INTO entity_external_id VALUES (?,?,?)", values)
    relational_db.execute("INSERT INTO entity_external_id VALUES ('mal','700',101)")
    assert relational_db.execute("SELECT COUNT(*) FROM entity_external_id").fetchone() == (2,)


def test_postgresql_metadata_retains_catalog_types_and_topic_keys():
    sql = "\n".join(
        str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        for model in (*CATALOG_MODELS, OnboardingAnswersRecord, Tap)
    )
    assert "aliases JSONB DEFAULT '[]'::jsonb NOT NULL" in sql
    assert "release_year SMALLINT" in sql
    assert "media_format VARCHAR(30)" in sql
    assert "CONSTRAINT uq_tap_id_topic_id UNIQUE (tap_id, topic_id)" in sql
    assert "schema_version SMALLINT DEFAULT '1' NOT NULL" in sql
    assert "ON DELETE RESTRICT" in sql and "ON DELETE CASCADE" in sql
    assert "consumption_type IS NOT NULL" in sql
    assert "signup_channel" not in sql and "birth_date" not in sql


def migration_sql(monkeypatch, direction):
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("e3a7b9c2d451").module
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql":True, "output_buffer":output}
    )
    monkeypatch.setattr(revision, "op", Operations(context))
    getattr(revision, direction)()
    return output.getvalue()


def test_migration_upgrade_preserves_legacy_answers_and_unrelated_schema(monkeypatch):
    sql = migration_sql(monkeypatch, "upgrade")
    assert sql.count("CREATE TABLE ") == 6
    for model in CATALOG_MODELS:
        assert f"CREATE TABLE {model.__tablename__} (" in sql
    assert "ADD COLUMN schema_version SMALLINT DEFAULT '1' NOT NULL" in sql
    assert "ALTER COLUMN q1_choice DROP NOT NULL" in sql
    assert "ALTER COLUMN q2_choice DROP NOT NULL" in sql
    assert "ADD CONSTRAINT ck_onboarding_answers_version_shape" in sql
    for forbidden in ("DROP TABLE", "DROP COLUMN", "UPDATE ", "DELETE FROM", "user_account", "birth_date"):
        assert forbidden not in sql
    assert "DROP CONSTRAINT ck_onboarding_answers_q1_choice" not in sql
    assert "DROP CONSTRAINT ck_onboarding_answers_q2_choice" not in sql
    assert "UNIQUE (tap_id, topic_id)" in sql
    assert sql.count("FOREIGN KEY(") == 9


def test_downgrade_checks_saved_v2_data_before_schema_mutation(monkeypatch):
    sql = migration_sql(monkeypatch, "downgrade")
    assert sql.index("RAISE EXCEPTION") < sql.index("ALTER TABLE")
    assert "WHERE schema_version = 2" in sql
    assert "EXISTS (SELECT 1 FROM tap_entity)" in sql
    assert "EXISTS (SELECT 1 FROM tap_preference_tag)" in sql
    assert "ALTER COLUMN q1_choice SET NOT NULL" in sql
    assert "ALTER COLUMN q2_choice SET NOT NULL" in sql
    assert sql.count("DROP TABLE ") == 6
    for forbidden in ("DELETE FROM", "UPDATE ", "user_account", "birth_date"):
        assert forbidden not in sql
