from io import StringIO
from pathlib import Path

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


def test_alembic_revision_chain_has_single_v1_head():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["7c8e1a9b4d2f"]
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
