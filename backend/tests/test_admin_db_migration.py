from io import StringIO

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from app.models import AdminAccount, AdminActionLog


PARENT = "d7b3e9a1c452"
REVISION = "47c1a8d6b302"


def test_admin_migration_roundtrip_preserves_grants_and_actions():
    config = Config()
    config.set_main_option("script_location", "alembic")
    with sa.create_engine("sqlite://").begin() as db:
        db.exec_driver_sql("PRAGMA foreign_keys = ON")
        db.exec_driver_sql("CREATE TABLE user_account (user_id BIGINT PRIMARY KEY)")
        db.exec_driver_sql("INSERT INTO user_account VALUES (7), (8)")
        config.attributes["connection"] = db
        command.stamp(config, PARENT)
        command.upgrade(config, REVISION)

        accounts, actions = AdminAccount.__table__, AdminActionLog.__table__
        assert db.scalar(sa.select(sa.func.count()).select_from(accounts)) == 0
        grant_id = db.scalar(accounts.insert().values(user_id=7, role="admin").returning(accounts.c.admin_account_id))
        granted_at, revoked_at = db.execute(sa.select(accounts.c.granted_at, accounts.c.revoked_at)).one()
        assert granted_at is not None and revoked_at is None
        db.execute(accounts.insert().values(user_id=8, role="reviewer", granted_by_admin_id=grant_id))
        db.execute(actions.insert().values(
            admin_account_id=grant_id, action_type="review", target_type="content",
            after_value={"reviewed": True}, rationale="test",
        ))
        log = db.execute(sa.select(actions)).mappings().one()
        assert log["admin_action_log_id"] > 0
        assert log["after_value"] == {"reviewed": True}
        assert log["before_value"] is None and log["target_id"] is None
        assert log["is_reverted"] is False and log["acted_at"] is not None
        with pytest.raises(sa.exc.IntegrityError):
            db.execute(accounts.delete().where(accounts.c.admin_account_id == grant_id))

        with pytest.raises(RuntimeError, match="Refusing to discard administrator grants or action history"):
            command.downgrade(config, PARENT)
        assert db.scalar(sa.text("SELECT version_num FROM alembic_version")) == REVISION
        assert db.scalar(sa.select(sa.func.count()).select_from(actions)) == 1
        db.execute(actions.delete())
        with pytest.raises(RuntimeError, match="Refusing to discard administrator grants or action history"):
            command.downgrade(config, PARENT)
        assert db.scalar(sa.select(sa.func.count()).select_from(accounts)) == 2
        db.execute(accounts.delete())
        command.downgrade(config, PARENT)
        assert set(sa.inspect(db).get_table_names()) == {"user_account", "alembic_version"}
        assert db.scalar(sa.text("SELECT count(*) FROM user_account")) == 2


def test_admin_migration_emits_postgres_history_guard_and_native_types(monkeypatch):
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(REVISION).module
    output = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    monkeypatch.setattr(revision, "op", Operations(context))
    revision.upgrade()
    upgrade_sql = output.getvalue()
    expected_columns = (
        "admin_account_id BIGSERIAL", "admin_action_log_id BIGSERIAL", "before_value JSONB", "after_value JSONB",
    )
    for column in expected_columns:
        assert column in upgrade_sql
    assert "TIMESTAMP WITH TIME ZONE" in upgrade_sql

    revision.downgrade()
    sql = output.getvalue()[len(upgrade_sql):]
    assert (
        sql.index("LOCK TABLE admin_account, admin_action_log IN ACCESS EXCLUSIVE MODE")
        < sql.index("RAISE EXCEPTION")
        < sql.index("DROP TABLE admin_action_log")
        < sql.index("DROP TABLE admin_account")
    )
