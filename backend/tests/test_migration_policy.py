from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.migration_policy import (
    DEFERRED_BIRTH_REVISION, SERVICE_SCHEMA_REVISION,
    include_schema_object, service_schema_ready,
)


def test_service_target_does_not_include_deferred_birth_removal():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    ancestors = {r.revision for r in script.iterate_revisions(SERVICE_SCHEMA_REVISION, "base")}
    assert "c0e5f3b8a124" in ancestors
    assert DEFERRED_BIRTH_REVISION not in ancestors
    for name in ("compose.labs.yaml", "compose.deploy.yaml"):
        text = (Path("..") / name).read_text(encoding="utf-8")
        command = next(line for line in text.splitlines() if "alembic" in line and "upgrade" in line)
        assert SERVICE_SCHEMA_REVISION in command
        assert "head" not in command


@pytest.mark.parametrize("revisions, expected", [
    ({SERVICE_SCHEMA_REVISION}, True),
    ({SERVICE_SCHEMA_REVISION, DEFERRED_BIRTH_REVISION}, True),
    ({DEFERRED_BIRTH_REVISION}, False),
    ({"c0e5f3b8a124"}, False),
    ({SERVICE_SCHEMA_REVISION, "unknown"}, False),
    (set(), False),
])
def test_readiness_handles_both_preexisting_birth_states(revisions, expected):
    assert service_schema_ready(revisions) is expected


@pytest.mark.parametrize("reflected", [True, False])
def test_autogenerate_ignores_only_deferred_user_birth_column(reflected):
    user_column = SimpleNamespace(table=SimpleNamespace(name="user_account"))
    other_column = SimpleNamespace(table=SimpleNamespace(name="other_table"))
    assert not include_schema_object(user_column, "birth_date", "column", reflected, None)
    assert include_schema_object(user_column, "nickname", "column", reflected, None)
    assert include_schema_object(other_column, "birth_date", "column", reflected, None)


def test_local_preparation_targets_onboarding_without_global_heads(monkeypatch):
    from unittest.mock import MagicMock
    from scripts import run_local_oauth as runner
    from sqlalchemy.engine import make_url

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.first.return_value = (
        SimpleNamespace(marker=runner.DATABASE_MARKER)
    )
    monkeypatch.setattr(runner, "create_engine", MagicMock(return_value=engine))
    upgrade = MagicMock()
    monkeypatch.setattr(runner.command, "upgrade", upgrade)
    runner.prepare_database(make_url("postgresql+psycopg://test@localhost/me_nu_oauth_local"))
    assert upgrade.call_args.args[1] == SERVICE_SCHEMA_REVISION
