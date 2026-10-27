from io import StringIO
from pathlib import Path
import sqlite3

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory


def test_alembic_configuration_is_initialized():
    config_path = Path("alembic.ini")

    assert config_path.is_file()
    assert Path("alembic/env.py").is_file()
    assert Path("alembic/versions").is_dir()

    config = Config(config_path)
    script = ScriptDirectory.from_config(config)

    assert Path(script.dir).resolve() == Path("alembic").resolve()


def test_alembic_revision_chain_has_single_service_head_without_birth_date_removal():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["f4b8c1d9a620"]
    carousel = script.get_revision("f4b8c1d9a620")
    assert carousel.down_revision == "e3a7b9c2d451"
    assert carousel.dependencies is None
    onboarding_v2 = script.get_revision("e3a7b9c2d451")
    assert onboarding_v2.down_revision == "c0e5f3b8a124"
    assert onboarding_v2.dependencies is None
    assert "onboarding_v2" in onboarding_v2.branch_labels
    assert "d2f6a9c4e731" not in {
        revision.revision for revision in script.walk_revisions(base="base", head="f4b8c1d9a620")
    }
    assert script.get_revision("c0e5f3b8a124").down_revision == (
        "b9d4e2a7f013"
    )
    assert script.get_revision("b9d4e2a7f013").down_revision == (
        "a8c3d1f6e902"
    )
    assert script.get_revision("a8c3d1f6e902").down_revision == (
        "e7a4c2d9b812"
    )
    assert script.get_revision("e7a4c2d9b812").down_revision == (
        "b5d2c8f1a7e3"
    )
    assert script.get_revision("b5d2c8f1a7e3").down_revision == (
        "7c8e1a9b4d2f"
    )
    assert script.get_revision("7c8e1a9b4d2f").down_revision == (
        "4e2b1a7c9d30"
    )
    assert script.get_revision("4e2b1a7c9d30").down_revision == (
        "9f3d8b2a7c41"
    )
    assert script.get_revision("9f3d8b2a7c41").down_revision == (
        "d1c61ade4657"
    )


def test_v045_migration_compiles_upgrade_and_downgrade_sql(monkeypatch):
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("9f3d8b2a7c41").module

    upgrade_buffer = StringIO()
    upgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": upgrade_buffer},
    )
    monkeypatch.setattr(revision, "op", Operations(upgrade_context))
    revision.upgrade()
    upgrade_sql = upgrade_buffer.getvalue()

    expected_new_tables = {
        "domain",
        "subtopic",
        "source_site",
        "creator_channel",
        "topic_cluster",
        "agent_run",
        "agent_run_step",
        "agent_run_source",
        "draft",
        "content",
        "judgment_log",
        "content_source",
    }
    assert "CREATE EXTENSION IF NOT EXISTS vector" in upgrade_sql
    assert "ALTER TABLE users RENAME TO user_account" in upgrade_sql
    assert "VECTOR" in upgrade_sql
    for table_name in expected_new_tables:
        assert f"CREATE TABLE {table_name}" in upgrade_sql

    downgrade_buffer = StringIO()
    downgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": downgrade_buffer},
    )
    monkeypatch.setattr(revision, "op", Operations(downgrade_context))
    revision.downgrade()
    downgrade_sql = downgrade_buffer.getvalue()

    assert "ALTER TABLE user_account RENAME TO users" in downgrade_sql
    for table_name in expected_new_tables:
        assert f"DROP TABLE {table_name}" in downgrade_sql


def test_alembic_ini_does_not_contain_database_credentials():
    config_text = Path("alembic.ini").read_text(encoding="utf-8")

    assert "driver://user:pass" not in config_text
    assert "DATABASE_URL" in config_text


def test_carousel_migration_offline_sql_preserves_downgrade_guard(monkeypatch):
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("f4b8c1d9a620").module
    buffer = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer},
    )
    monkeypatch.setattr(revision, "op", Operations(context))
    revision.upgrade()
    revision.downgrade()
    sql = buffer.getvalue()
    assert "CREATE TABLE content_slide" in sql and "image_data BYTEA" in sql
    assert "birth_date" not in sql and "user_account" not in sql
    lock = sql.index("LOCK TABLE content_slide, content IN ACCESS EXCLUSIVE MODE")
    guard = sql.index("RAISE EXCEPTION")
    deletion = sql.index("DROP TABLE content_slide")
    assert lock < guard < deletion


def test_topic_rename_migration_compiles_upgrade_and_downgrade_sql(
    monkeypatch,
):
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("4e2b1a7c9d30").module

    upgrade_buffer = StringIO()
    upgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": upgrade_buffer},
    )
    monkeypatch.setattr(revision, "op", Operations(upgrade_context))
    revision.upgrade()
    upgrade_sql = upgrade_buffer.getvalue()

    assert "ALTER TABLE domain RENAME TO topic" in upgrade_sql
    assert "RENAME domain_id TO topic_id" in upgrade_sql
    assert "RENAME domain_code TO topic_code" in upgrade_sql
    assert "RENAME domain_name TO topic_name" in upgrade_sql
    assert "idx_agent_run_topic_status_created_at" in upgrade_sql

    downgrade_buffer = StringIO()
    downgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": downgrade_buffer},
    )
    monkeypatch.setattr(revision, "op", Operations(downgrade_context))
    revision.downgrade()
    downgrade_sql = downgrade_buffer.getvalue()

    assert "ALTER TABLE topic RENAME TO domain" in downgrade_sql
    assert "RENAME topic_id TO domain_id" in downgrade_sql


def test_v1_migration_compiles_reversible_postgresql_sql(monkeypatch):
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("7c8e1a9b4d2f").module

    upgrade_buffer = StringIO()
    upgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": upgrade_buffer},
    )
    monkeypatch.setattr(revision, "op", Operations(upgrade_context))
    revision.upgrade()
    upgrade_sql = upgrade_buffer.getvalue()

    for table in (
        "auth_session",
        "policy",
        "consent_history",
        "notification_setting",
        "tap",
        "tap_topic",
        "content_tag",
        "content_reaction",
        "saved_item",
    ):
        assert f"CREATE TABLE {table}" in upgrade_sql
    assert "onboarding_completed_at" in upgrade_sql
    assert "image_url" in upgrade_sql
    assert "uq_tap_active_user_topic" in upgrade_sql
    assert "fk_content_reaction_tap_owner" in upgrade_sql
    assert "fk_saved_item_tap_owner" in upgrade_sql

    downgrade_buffer = StringIO()
    downgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": downgrade_buffer},
    )
    monkeypatch.setattr(revision, "op", Operations(downgrade_context))
    revision.downgrade()
    downgrade_sql = downgrade_buffer.getvalue()

    for table in (
        "saved_item",
        "content_reaction",
        "content_tag",
        "tap_topic",
        "tap",
        "notification_setting",
        "consent_history",
        "policy",
        "auth_session",
    ):
        assert f"DROP TABLE {table}" in downgrade_sql
    assert "DROP COLUMN image_url" in downgrade_sql
    assert "DROP COLUMN onboarding_completed_at" in downgrade_sql


def test_chat_migration_compiles_reversible_postgresql_sql(monkeypatch):
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("b5d2c8f1a7e3").module
    chat_tables = (
        "chat_session",
        "chat_message",
        "long_term_memory",
        "user_operation",
        "chat_fact_cache",
        "chat_unanswered_question",
    )

    upgrade_buffer = StringIO()
    upgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": upgrade_buffer},
    )
    monkeypatch.setattr(revision, "op", Operations(upgrade_context))
    revision.upgrade()
    upgrade_sql = upgrade_buffer.getvalue()

    for table_name in chat_tables:
        assert f"CREATE TABLE {table_name}" in upgrade_sql
    assert "fk_chat_session_tap_owner" in upgrade_sql
    assert "WHERE client_message_id IS NOT NULL" in upgrade_sql

    downgrade_buffer = StringIO()
    downgrade_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": downgrade_buffer},
    )
    monkeypatch.setattr(revision, "op", Operations(downgrade_context))
    revision.downgrade()
    downgrade_sql = downgrade_buffer.getvalue()

    for table_name in chat_tables:
        assert f"DROP TABLE {table_name}" in downgrade_sql


def _account_type_migration_sql(monkeypatch, direction):
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("a8c3d1f6e902").module
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(revision, "op", Operations(context))
    getattr(revision, direction)()
    return output.getvalue()


def test_account_type_upgrade_removes_constraint_before_obsolete_column(monkeypatch):
    sql = _account_type_migration_sql(monkeypatch, "upgrade")
    expected_statements = [
        "ALTER TABLE user_account DROP CONSTRAINT ck_user_account_signup_channel",
        "ALTER TABLE user_account DROP COLUMN signup_channel",
    ]
    assert [statement.strip() for statement in sql.split(";") if statement.strip()] == expected_statements


def test_account_type_downgrade_restores_legacy_schema_with_consumer_fallback(monkeypatch):
    sql = _account_type_migration_sql(monkeypatch, "downgrade")
    expected_statements = [
        "ALTER TABLE user_account ADD COLUMN signup_channel VARCHAR(20) DEFAULT 'consumer' NOT NULL",
        "ALTER TABLE user_account ADD CONSTRAINT ck_user_account_signup_channel CHECK (signup_channel IN ('consumer', 'creator'))",
    ]
    assert [statement.strip() for statement in sql.split(";") if statement.strip()] == expected_statements


def test_account_type_column_removal_preserves_accounts_and_references(monkeypatch):
    upgrade_sql = _account_type_migration_sql(monkeypatch, "upgrade")
    downgrade_sql = _account_type_migration_sql(monkeypatch, "downgrade")

    # PostgreSQL CHECK-constraint DDL is checked above. Execute the actual
    # emitted DROP/ADD COLUMN statements against isolated in-memory data;
    # SQLite cannot directly DROP/ADD a named table-level CHECK constraint.
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "CREATE TABLE user_account (user_id INTEGER PRIMARY KEY, nickname TEXT, signup_channel TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE auth_session (session_id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES user_account(user_id))"
        )
        connection.executemany(
            "INSERT INTO user_account VALUES (?, ?, ?)",
            [(11, "기존 소비자", "consumer"), (22, "기존 창작자", "creator")],
        )
        connection.executemany("INSERT INTO auth_session VALUES (?, ?)", [(101, 11), (102, 22)])

        for sql in [upgrade_sql, downgrade_sql]:
            for statement in sql.split(";"):
                if " COLUMN signup_channel" in statement:
                    connection.execute(statement)
            assert connection.execute(
                "SELECT user_id, nickname FROM user_account ORDER BY user_id"
            ).fetchall() == [(11, "기존 소비자"), (22, "기존 창작자")]
            assert connection.execute(
                "SELECT session_id, user_id FROM auth_session ORDER BY session_id"
            ).fetchall() == [(101, 11), (102, 22)]
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            columns = {row[1] for row in connection.execute("PRAGMA table_info(user_account)")}
            assert ("signup_channel" in columns) == (sql == downgrade_sql)

        assert connection.execute(
            "SELECT signup_channel FROM user_account ORDER BY user_id"
        ).fetchall() == [("consumer",), ("consumer",)]
        connection.execute("INSERT INTO user_account (user_id, nickname) VALUES (33, '새 회원')")
        assert connection.execute(
            "SELECT signup_channel FROM user_account WHERE user_id = 33"
        ).fetchone() == ("consumer",)


def test_oauth_attempt_migration_compiles_postgresql_sql(monkeypatch):
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("b9d4e2a7f013").module
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    monkeypatch.setattr(revision, "op", Operations(context))
    revision.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE oauth_attempt" in sql
    assert "state_hash VARCHAR(64) NOT NULL" in sql
    assert "browser_session_hash VARCHAR(64) NOT NULL" in sql
    assert "PRIMARY KEY (state_hash)" in sql
    assert "CHECK (provider IN ('kakao', 'naver'))" in sql
    assert "CHECK (expires_at > created_at)" in sql
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in sql
    assert "expires_at TIMESTAMP WITH TIME ZONE NOT NULL" in sql
    assert "consumed_at TIMESTAMP WITH TIME ZONE" in sql
    assert "CREATE INDEX idx_oauth_attempt_browser_created" in sql
    assert "CREATE INDEX idx_oauth_attempt_expires_at" in sql
    assert "FOREIGN KEY" not in sql

    output.truncate(0)
    output.seek(0)
    revision.downgrade()
    assert [part.strip() for part in output.getvalue().split(";") if part.strip()] == [
        "DROP INDEX idx_oauth_attempt_expires_at",
        "DROP INDEX idx_oauth_attempt_browser_created",
        "DROP TABLE oauth_attempt",
    ]


def test_onboarding_answers_migration_is_additive_with_database_constraints(monkeypatch):
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("c0e5f3b8a124").module
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(revision, "op", Operations(context))
    revision.upgrade()
    statements = [part.strip() for part in output.getvalue().split(";") if part.strip()]
    assert len(statements) == 1 and statements[0].startswith("CREATE TABLE onboarding_answers")
    sql = statements[0]
    assert "tap_id BIGINT NOT NULL" in sql
    assert "PRIMARY KEY (tap_id)" in sql
    assert "FOREIGN KEY(tap_id) REFERENCES tap (tap_id) ON DELETE CASCADE" in sql
    assert "q1_choice VARCHAR(32) NOT NULL" in sql
    assert "q2_choice VARCHAR(32) NOT NULL" in sql
    assert "CHECK (q1_choice IN ('world', 'relationships', 'ost', 'goods', 'events', 'news'))" in sql
    assert "CHECK (q2_choice IN ('releases', 'characters', 'restock', 'events'))" in sql
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in sql
    output.truncate(0)
    output.seek(0)
    revision.downgrade()
    assert [part.strip() for part in output.getvalue().split(";") if part.strip()] == [
        "DROP TABLE onboarding_answers",
    ]
