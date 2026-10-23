"""Isolation guarantees for the local OAuth helper; no services are contacted."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import make_url

from scripts import run_local_oauth as runner


LOCAL_DATABASE_URL = "postgresql+psycopg://test_user:test_password@localhost:5433/original_db"
LOCAL_REDIS_URL = "redis://127.0.0.1:6379/0"
PROVIDERS = {
    "kakao": ("kakao",),
    "naver": ("naver",),
    "all": ("kakao", "naver"),
}
SYNTHETIC_CREDENTIALS = {
    "KAKAO_CLIENT_ID": "synthetic_kakao_client_id",
    "KAKAO_CLIENT_SECRET": "synthetic_kakao_client_secret",
    "NAVER_CLIENT_ID": "synthetic_naver_client_id",
    "NAVER_CLIENT_SECRET": "synthetic_naver_client_secret",
}


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
    monkeypatch.setattr(
        runner.uvicorn, "Server",
        MagicMock(side_effect=AssertionError("No API server is allowed in these tests.")),
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
@pytest.mark.parametrize("provider", PROVIDERS)
def test_selects_isolated_resources_without_rewriting_original_dotenv(tmp_path, prepare, provider):
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
        "KAKAO_OAUTH_ENABLED=true\n"
        "NAVER_OAUTH_ENABLED=true\n"
        + "".join(f"{name}={value}\n" for name, value in SYNTHETIC_CREDENTIALS.items())
    ).encode("utf-8")
    env_file.write_bytes(original)

    target = runner.configure_environment(prepare=prepare, provider=provider)

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
    for name in ("kakao", "naver"):
        enabled = not prepare and name in PROVIDERS[provider]
        assert runner.os.environ[f"{name.upper()}_OAUTH_ENABLED"] == ("true" if enabled else "false")
    for name, value in SYNTHETIC_CREDENTIALS.items():
        assert runner.os.environ[name] == value
    assert env_file.read_bytes() == original
    runner.create_engine.assert_not_called()
    runner.Redis.from_url.assert_not_called()


@pytest.mark.parametrize("provider", ["", "google", "NAVER"])
@pytest.mark.parametrize("prepare", [False, True])
def test_invalid_provider_is_rejected_before_dotenv_or_environment_changes(monkeypatch, provider, prepare):
    set_connection_urls()
    original = runner.os.environ.copy()
    load_dotenv = MagicMock()
    monkeypatch.setattr(runner, "load_dotenv", load_dotenv)

    with pytest.raises(runner.SetupError):
        runner.configure_environment(prepare=prepare, provider=provider)

    assert runner.os.environ == original
    load_dotenv.assert_not_called()
    runner.create_engine.assert_not_called()
    runner.Redis.from_url.assert_not_called()


@pytest.mark.parametrize("provider", ["", "google", "NAVER"])
def test_readiness_rejects_invalid_provider_before_any_setup_work(monkeypatch, provider):
    set_connection_urls()
    original = runner.os.environ.copy()
    target = make_url(LOCAL_DATABASE_URL).set(database=runner.DATABASE_NAME)
    load_dotenv = MagicMock()
    scripts = MagicMock()
    monkeypatch.setattr(runner, "load_dotenv", load_dotenv)
    monkeypatch.setattr(runner.ScriptDirectory, "from_config", scripts)

    with pytest.raises(runner.SetupError):
        runner.check_readiness(target, provider=provider)

    assert runner.os.environ == original
    load_dotenv.assert_not_called()
    scripts.assert_not_called()
    runner.create_engine.assert_not_called()
    runner.Redis.from_url.assert_not_called()


@pytest.mark.parametrize("provider, missing_name", [
    (provider, f"{name.upper()}_{field}")
    for provider, names in PROVIDERS.items()
    for name in names
    for field in ("CLIENT_ID", "CLIENT_SECRET")
])
@pytest.mark.parametrize("missing_value", [None, "", " \t "])
def test_readiness_reports_missing_selected_credentials_before_services(
    monkeypatch, provider, missing_name, missing_value,
):
    set_connection_urls()
    runner.os.environ.update(SYNTHETIC_CREDENTIALS)
    if missing_value is None:
        del runner.os.environ[missing_name]
    else:
        runner.os.environ[missing_name] = missing_value
    scripts = MagicMock()
    monkeypatch.setattr(runner.ScriptDirectory, "from_config", scripts)
    target = make_url(LOCAL_DATABASE_URL).set(database=runner.DATABASE_NAME)

    with pytest.raises(runner.SetupError, match=missing_name) as error:
        runner.check_readiness(target, provider=provider)

    for name, value in SYNTHETIC_CREDENTIALS.items():
        assert value not in str(error.value)
        if name != missing_name:
            assert name not in str(error.value)
    scripts.assert_not_called()
    runner.create_engine.assert_not_called()
    runner.Redis.from_url.assert_not_called()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_readiness_requires_only_selected_credentials_and_prints_safe_callbacks(
    monkeypatch, capsys, provider,
):
    set_connection_urls()
    selected = PROVIDERS[provider]
    runner.os.environ.update({
        name: value for name, value in SYNTHETIC_CREDENTIALS.items()
        if name.split("_", 1)[0].lower() in selected
    })
    target = make_url(LOCAL_DATABASE_URL).set(database=runner.DATABASE_NAME)
    scripts = MagicMock()
    scripts.get_current_head.return_value = "test_head"
    monkeypatch.setattr(runner.ScriptDirectory, "from_config", MagicMock(return_value=scripts))
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.side_effect = [
        runner.DATABASE_MARKER, "test_head", "oauth_attempt",
    ]
    monkeypatch.setattr(runner, "create_engine", MagicMock(return_value=engine))
    redis = MagicMock()
    client = redis.__enter__.return_value
    monkeypatch.setattr(runner.Redis, "from_url", MagicMock(return_value=redis))

    runner.check_readiness(target, provider=provider)

    client.ping.assert_called_once_with()
    engine.dispose.assert_called_once_with()
    assert connection.execute.call_count == 3
    captured = capsys.readouterr()
    assert captured.err == ""
    output = captured.out.lower()
    for name in ("kakao", "naver"):
        if name in selected:
            assert f"{name} credentials: configured" in output
            assert f"http://localhost:8000/api/v1/auth/login/{name}/callback" in output
        else:
            assert name not in output
    for value in SYNTHETIC_CREDENTIALS.values():
        assert value not in output
    assert "test_password" not in output


@pytest.fixture
def cli_environment(monkeypatch):
    set_connection_urls()
    monkeypatch.setattr(runner.os, "chdir", MagicMock())
    monkeypatch.setattr(runner.sys, "path", runner.sys.path.copy())


@pytest.mark.parametrize("provider_args, expected_provider", [
    ([], "kakao"),
    (["--provider", "kakao"], "kakao"),
    (["--provider", "naver"], "naver"),
    (["--provider", "all"], "all"),
])
def test_cli_check_passes_provider_without_starting_server(
    monkeypatch, cli_environment, provider_args, expected_provider,
):
    monkeypatch.setattr(runner.sys, "argv", ["run_local_oauth.py", "--check", *provider_args])
    configure = MagicMock(wraps=runner.configure_environment)
    check = MagicMock()
    prepare = MagicMock()
    monkeypatch.setattr(runner, "configure_environment", configure)
    monkeypatch.setattr(runner, "check_readiness", check)
    monkeypatch.setattr(runner, "prepare_database", prepare)

    assert runner.main() == 0

    configure.assert_called_once_with(prepare=False, provider=expected_provider)
    expected_target = make_url(LOCAL_DATABASE_URL).set(database=runner.DATABASE_NAME)
    check.assert_called_once_with(expected_target, provider=expected_provider)
    prepare.assert_not_called()
    runner.uvicorn.Server.assert_not_called()
    runner.create_engine.assert_not_called()
    runner.Redis.from_url.assert_not_called()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_cli_prepare_disables_oauth_without_requiring_credentials_or_starting_server(
    monkeypatch, cli_environment, provider,
):
    monkeypatch.setattr(runner.sys, "argv", ["run_local_oauth.py", "--prepare", "--provider", provider])
    configure = MagicMock(wraps=runner.configure_environment)
    check = MagicMock()
    prepare = MagicMock()
    monkeypatch.setattr(runner, "configure_environment", configure)
    monkeypatch.setattr(runner, "check_readiness", check)
    monkeypatch.setattr(runner, "prepare_database", prepare)

    assert runner.main() == 0

    configure.assert_called_once_with(prepare=True, provider=provider)
    expected_target = make_url(LOCAL_DATABASE_URL).set(database=runner.DATABASE_NAME)
    prepare.assert_called_once_with(expected_target)
    check.assert_not_called()
    assert runner.os.environ["KAKAO_OAUTH_ENABLED"] == "false"
    assert runner.os.environ["NAVER_OAUTH_ENABLED"] == "false"
    assert SYNTHETIC_CREDENTIALS.keys().isdisjoint(runner.os.environ)
    runner.uvicorn.Server.assert_not_called()
    runner.create_engine.assert_not_called()
    runner.Redis.from_url.assert_not_called()


def test_cli_rejects_invalid_provider_before_configuration(monkeypatch, cli_environment):
    monkeypatch.setattr(runner.sys, "argv", ["run_local_oauth.py", "--check", "--provider", "google"])
    configure = MagicMock()
    monkeypatch.setattr(runner, "configure_environment", configure)
    original = runner.os.environ.copy()
    original_path = runner.sys.path.copy()

    with pytest.raises(SystemExit) as error:
        runner.main()

    assert error.value.code == 2
    assert runner.os.environ == original
    assert runner.sys.path == original_path
    configure.assert_not_called()
    runner.os.chdir.assert_not_called()
    runner.uvicorn.Server.assert_not_called()
    runner.create_engine.assert_not_called()
    runner.Redis.from_url.assert_not_called()


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
