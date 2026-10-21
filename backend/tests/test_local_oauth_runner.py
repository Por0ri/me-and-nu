"""Isolation guarantees for the local OAuth helper; no services are contacted."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import make_url

from scripts import run_local_oauth as runner


LOCAL_DATABASE_URL = "postgresql+psycopg://test_user:test_password@localhost:5433/original_db"
LOCAL_REDIS_URL = "redis://127.0.0.1:6379/0"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    # The helper writes directly to os.environ. Replace the mapping so no real
    # credentials are read and every test's process settings are restored.
    monkeypatch.setattr(runner.os, "environ", {})
    monkeypatch.setattr(runner, "BACKEND_ROOT", tmp_path)
    monkeypatch.setattr(
        runner, "create_engine",
        MagicMock(side_effect=AssertionError("A test must supply its own fake DB engine.")),
    )
    monkeypatch.setattr(
        runner.Redis, "from_url",
        MagicMock(side_effect=AssertionError("No Redis connection is allowed in these tests.")),
    )


def set_connection_urls(database_url=LOCAL_DATABASE_URL, redis_url=LOCAL_REDIS_URL):
    runner.os.environ.update({
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
    })


@pytest.mark.parametrize("database_url", [
    "postgresql+psycopg://test_user:test_password@db.example.com/original_db",
    "postgresql+psycopg://test_user:test_password@192.168.1.10/original_db",
    "postgresql+psycopg://test_user:test_password@localhost.example.com/original_db",
    "sqlite:///original_db",
])
def test_rejects_nonlocal_or_nonpostgres_database(database_url):
    set_connection_urls(database_url=database_url)

    with pytest.raises(runner.SetupError, match="PostgreSQL on this PC"):
        runner.configure_environment()

    assert runner.os.environ["DATABASE_URL"] == database_url
    runner.create_engine.assert_not_called()


@pytest.mark.parametrize("query", [
    "dbname=original_db",
    "host=db.example.com",
    "service=remote_profile",
    "options=-csearch_path%3Dother_schema",
])
def test_rejects_database_query_overrides_before_selecting_test_database(query):
    database_url = f"{LOCAL_DATABASE_URL}?{query}"
    set_connection_urls(database_url=database_url)

    with pytest.raises(runner.SetupError, match="PostgreSQL on this PC"):
        runner.configure_environment(prepare=True)

    assert runner.os.environ["DATABASE_URL"] == database_url
    runner.create_engine.assert_not_called()


@pytest.mark.parametrize("redis_url", [
    "redis://redis.example.com:6379/0",
    "redis://192.168.1.10:6379/0",
    "redis://localhost:6379/0?host=redis.example.com",
    "redis://localhost:6379/0?db=1",
])
def test_rejects_remote_or_query_overridden_redis(redis_url):
    set_connection_urls(redis_url=redis_url)

    with pytest.raises(runner.SetupError, match="Redis on this PC"):
        runner.configure_environment()

    assert runner.os.environ["DATABASE_URL"] == LOCAL_DATABASE_URL
    runner.Redis.from_url.assert_not_called()


def test_rejects_test_database_as_the_original_database():
    database_url = make_url(LOCAL_DATABASE_URL).set(database=runner.DATABASE_NAME)
    set_connection_urls(database_url=database_url.render_as_string(hide_password=False))

    with pytest.raises(runner.SetupError, match="Keep the original DATABASE_URL"):
        runner.configure_environment(prepare=True)

    runner.create_engine.assert_not_called()


@pytest.mark.parametrize("prepare", [False, True])
def test_selects_isolated_resources_without_rewriting_original_dotenv(tmp_path, prepare):
    env_file = tmp_path / ".env"
    original = (
        f"DATABASE_URL={LOCAL_DATABASE_URL}\n"
        f"REDIS_URL={LOCAL_REDIS_URL}\n"
        "REDIS_KEY_PREFIX=existing_app\n"
        "SESSION_COOKIE_NAME=existing_cookie\n"
        "ENABLE_DEV_API=true\n"
        "ENABLE_DEV_AUTH_BYPASS=true\n"
        "OAUTH_PUBLIC_API_ORIGIN=https://api.example.com\n"
        "OAUTH_FRONTEND_ORIGIN=https://app.example.com\n"
        "NAVER_OAUTH_ENABLED=true\n"
        "KAKAO_CLIENT_ID=synthetic_client_id\n"
        "KAKAO_CLIENT_SECRET=synthetic_client_secret\n"
    ).encode("utf-8")
    env_file.write_bytes(original)

    target = runner.configure_environment(prepare=prepare)

    assert target.database == runner.DATABASE_NAME
    assert target.host == "localhost"
    assert target.port == 5433
    assert target.drivername == "postgresql+psycopg"
    assert make_url(runner.os.environ["DATABASE_URL"]) == target
    assert runner.os.environ["REDIS_URL"] == LOCAL_REDIS_URL
    assert runner.os.environ["REDIS_KEY_PREFIX"] == runner.DATABASE_NAME
    assert runner.os.environ["SESSION_COOKIE_NAME"] == "menu_oauth_local"
    assert runner.os.environ["SESSION_BACKEND"] == "redis"
    assert runner.os.environ["SESSION_COOKIE_SECURE"] == "false"
    assert runner.os.environ["ENABLE_DEV_API"] == "false"
    assert runner.os.environ["ENABLE_DEV_AUTH_BYPASS"] == "false"
    assert runner.os.environ["DATABASE_ECHO"] == "false"
    assert runner.os.environ["OAUTH_PUBLIC_API_ORIGIN"] == "http://localhost:8000"
    assert runner.os.environ["OAUTH_FRONTEND_ORIGIN"] == ""
    assert runner.os.environ["KAKAO_OAUTH_ENABLED"] == ("false" if prepare else "true")
    assert runner.os.environ["NAVER_OAUTH_ENABLED"] == "false"
    assert env_file.read_bytes() == original
    runner.create_engine.assert_not_called()


@pytest.mark.parametrize("marker", [None, "database managed by another application"])
def test_prepare_refuses_unmanaged_existing_database_before_any_migration(monkeypatch, marker):
    target = make_url(LOCAL_DATABASE_URL).set(database=runner.DATABASE_NAME)
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.first.return_value = SimpleNamespace(marker=marker)
    monkeypatch.setattr(runner, "create_engine", MagicMock(return_value=engine))
    upgrade = MagicMock()
    monkeypatch.setattr(runner.command, "upgrade", upgrade)

    with pytest.raises(runner.SetupError, match="unmanaged database"):
        runner.prepare_database(target)

    upgrade.assert_not_called()
    assert connection.execute.call_count == 1
    statement, parameters = connection.execute.call_args.args
    assert str(statement).lstrip().startswith("SELECT ")
    assert parameters == {"name": runner.DATABASE_NAME}
    assert runner.create_engine.call_args.args[0].database == "postgres"
    engine.dispose.assert_called_once()


def test_readiness_rejects_unmanaged_database_before_schema_or_redis_access(monkeypatch):
    set_connection_urls()
    runner.os.environ.update({
        "KAKAO_CLIENT_ID": "synthetic_client_id",
        "KAKAO_CLIENT_SECRET": "synthetic_client_secret",
    })
    target = make_url(LOCAL_DATABASE_URL).set(database=runner.DATABASE_NAME)
    scripts = MagicMock()
    scripts.get_current_head.return_value = "test_head"
    monkeypatch.setattr(runner.ScriptDirectory, "from_config", MagicMock(return_value=scripts))
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = None
    monkeypatch.setattr(runner, "create_engine", MagicMock(return_value=engine))

    with pytest.raises(runner.SetupError, match="not the test DB managed"):
        runner.check_readiness(target)

    assert connection.execute.call_count == 1
    assert "shobj_description" in str(connection.execute.call_args.args[0])
    runner.Redis.from_url.assert_not_called()
    engine.dispose.assert_called_once()
