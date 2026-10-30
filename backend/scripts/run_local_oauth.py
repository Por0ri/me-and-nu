"""Prepare and run Kakao or Naver OAuth against an isolated database on this PC."""

import argparse
import asyncio
from ipaddress import ip_address
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import load_dotenv
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
import uvicorn


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
from app.db.migration_policy import SERVICE_SCHEMA_REVISION, service_schema_ready

DATABASE_NAME = "me_nu_oauth_local"
DATABASE_MARKER = "me_nu isolated local OAuth test database"
SUPPORTED_PROVIDERS = ("kakao", "naver")


class SetupError(Exception):
    """An actionable setup error whose message contains no credentials."""


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _selected_providers(provider: str) -> tuple[str, ...]:
    if provider == "all":
        return SUPPORTED_PROVIDERS
    if provider not in SUPPORTED_PROVIDERS:
        raise SetupError("Select an OAuth provider: kakao, naver, or all.")
    return (provider,)


def _local_frontend_origin(value: str) -> str:
    """Accept only a canonical localhost HTTP origin with an explicit port."""
    if value == "":
        return value
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme != "http"
            or parsed.hostname != "localhost"
            or port is None
            or not 1 <= port <= 65535
            or value != f"http://localhost:{port}"
        ):
            raise ValueError
    except ValueError:
        raise SetupError(
            "--frontend-origin must be http://localhost:<port> without a path, credentials, query, or fragment."
        ) from None
    return value


def configure_environment(*, prepare: bool = False, provider: str = "kakao", frontend_origin: str = ""):
    selected = _selected_providers(provider)
    frontend_origin = _local_frontend_origin(frontend_origin)
    load_dotenv(BACKEND_ROOT / ".env", override=False)
    try:
        source = make_url(os.environ.get("DATABASE_URL", ""))
        redis_url = urlsplit(os.environ.get("REDIS_URL", ""))
        if source.get_backend_name() != "postgresql" or not is_loopback(source.host) or source.query:
            raise SetupError("DATABASE_URL must point to PostgreSQL on this PC.")
        if source.database == DATABASE_NAME:
            raise SetupError("Keep the original DATABASE_URL in backend/.env; this runner selects the test DB.")
        if (
            redis_url.scheme not in {"redis", "rediss"}
            or not is_loopback(redis_url.hostname)
            or redis_url.query
        ):
            raise SetupError("REDIS_URL must point to Redis on this PC.")
    except SetupError:
        raise
    except Exception:
        raise SetupError("Check the local DATABASE_URL and REDIS_URL in backend/.env.") from None

    target = source.set(database=DATABASE_NAME, drivername="postgresql+psycopg")
    os.environ.update({
        "DATABASE_URL": target.render_as_string(hide_password=False),
        "DATABASE_ECHO": "false",
        "SESSION_BACKEND": "redis",
        "REDIS_KEY_PREFIX": DATABASE_NAME,
        "SESSION_COOKIE_NAME": "menu_oauth_local",
        "SESSION_COOKIE_SECURE": "false",
        "ENABLE_DEV_API": "false",
        "ENABLE_DEV_AUTH_BYPASS": "false",
        "OAUTH_PUBLIC_API_ORIGIN": "http://localhost:8000",
        "OAUTH_FRONTEND_ORIGIN": frontend_origin,
    })
    if frontend_origin:
        os.environ["FRONTEND_ORIGINS"] = ",".join(dict.fromkeys((
            "http://localhost:8000", frontend_origin,
        )))
    for name in SUPPORTED_PROVIDERS:
        os.environ[f"{name.upper()}_OAUTH_ENABLED"] = (
            "true" if not prepare and name in selected else "false"
        )
    return target


def prepare_database(target) -> None:
    # The fixed name and marker prevent migrations against the original DB or
    # an unrelated database that happens to use the same name.
    engine = create_engine(
        target.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT shobj_description(oid, 'pg_database') AS marker "
                "FROM pg_database WHERE datname = :name"
            ), {"name": DATABASE_NAME}).first()
            if row is None:
                connection.execute(text("CREATE DATABASE me_nu_oauth_local"))
                connection.execute(text(
                    "COMMENT ON DATABASE me_nu_oauth_local "
                    "IS 'me_nu isolated local OAuth test database'"
                ))
            elif row.marker != DATABASE_MARKER:
                raise SetupError("The test DB name is already in use by an unmanaged database; no migration was run.")
    finally:
        engine.dispose()
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), SERVICE_SCHEMA_REVISION)
    print(f"Prepared isolated database: {DATABASE_NAME}")


def check_readiness(target, *, provider: str = "kakao") -> None:
    selected = _selected_providers(provider)
    missing = [
        name
        for name in (
            f"{selected_provider.upper()}_{suffix}"
            for selected_provider in selected
            for suffix in ("CLIENT_ID", "CLIENT_SECRET")
        )
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        raise SetupError("Set these values in backend/.env: " + ", ".join(missing))

    expected = ScriptDirectory.from_config(
        Config(str(BACKEND_ROOT / "alembic.ini"))
    ).get_revision(SERVICE_SCHEMA_REVISION).revision
    engine = create_engine(target, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            marker = connection.execute(text(
                "SELECT shobj_description(oid, 'pg_database') "
                "FROM pg_database WHERE datname = current_database()"
            )).scalar_one()
            if marker != DATABASE_MARKER:
                raise SetupError("This is not the test DB managed by the local OAuth runner.")
            revisions = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
            table = connection.execute(text("SELECT to_regclass('public.oauth_attempt')")).scalar_one()
            if not service_schema_ready(revisions, expected) or table is None:
                raise SetupError("The local test DB needs migration. Run this script with --prepare.")
    finally:
        engine.dispose()
    with Redis.from_url(
        os.environ["REDIS_URL"], socket_connect_timeout=2, socket_timeout=2,
    ) as client:
        client.ping()
    print(f"Database: {DATABASE_NAME} ({', '.join(sorted(revisions))}); Redis: OK")
    for name in selected:
        print(f"{name.capitalize()} credentials: configured")
        print(f"{name.capitalize()} console callback: http://localhost:8000/api/v1/auth/login/{name}/callback")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider", choices=(*SUPPORTED_PROVIDERS, "all"), default="kakao",
        help="OAuth provider to enable and check (default: kakao); ignored during --prepare.",
    )
    parser.add_argument(
        "--frontend-origin", default="",
        help="Optional http://localhost:<port> for FE callbacks and CORS; default returns to the API session endpoint.",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--prepare", action="store_true", help="Create/migrate only the dedicated local test DB.")
    action.add_argument("--check", action="store_true", help="Check local credentials, database and Redis without starting the API.")
    args = parser.parse_args()
    os.chdir(BACKEND_ROOT)
    sys.path.insert(0, str(BACKEND_ROOT))
    try:
        target = configure_environment(
            prepare=args.prepare, provider=args.provider, frontend_origin=args.frontend_origin,
        )
        if args.prepare:
            prepare_database(target)
            return 0
        check_readiness(target, provider=args.provider)
        if args.frontend_origin:
            print(f"Local OAuth frontend: {args.frontend_origin}")
        if args.check:
            return 0
        print("Local OAuth Swagger: http://localhost:8000/docs")
        # Callback URLs contain a short-lived code and state; omit access logs.
        server = uvicorn.Server(uvicorn.Config(
            "app.main:app", host="localhost", port=8000, access_log=False,
        ))
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                runner.run(server.serve())
        else:
            asyncio.run(server.serve())
        return 0
    except SetupError as error:
        print(f"Local OAuth setup: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Driver/configuration errors can contain connection details.
        print(
            f"Local OAuth setup failed ({type(error).__name__}). "
            "Check Docker and backend/.env; use --prepare for a new test DB.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
