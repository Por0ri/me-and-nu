"""Explicit, audited administrator bootstrap/grant/revoke CLI.

Set ADMIN_DATABASE_URL explicitly; this tool never takes a target from .env.
Unset PGHOSTADDR, PGSERVICE and PGSERVICEFILE; inherited libpq routing is refused.
Mutations preview without --apply. --database-name must match the selected DB.
This script does not choose initial administrators or execute automatically.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class AmbientDatabaseRoutingError(ValueError):
    """Refuse inherited libpq routing; never print the environment values."""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("bootstrap", "grant", "revoke", "list"):
        command = commands.add_parser(name)
        command.add_argument("--database-name", required=True, help="Exact intended PostgreSQL database name")
        if name != "bootstrap":
            command.add_argument("--actor-user-id", type=int, required=True)
        if name in {"bootstrap", "grant", "list"}:
            command.add_argument("--target-user-id", type=int, required=name != "list")
        if name == "grant":
            command.add_argument("--role", choices=("viewer", "operator", "admin"), required=True)
        if name == "revoke":
            command.add_argument("--admin-account-id", type=int, required=True)
        if name == "bootstrap":
            command.add_argument("--confirm-first-admin", action="store_true",
                                 help="Acknowledge explicit one-time bootstrap for the selected service user")
        if name != "list":
            command.add_argument("--rationale", required=True)
            command.add_argument("--apply", action="store_true", help="Commit; otherwise print the plan without connecting")
    return result


async def execute(args):
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    if any(name in os.environ for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")):
        raise AmbientDatabaseRoutingError("Inherited libpq routing is not allowed")
    target = os.environ.get("ADMIN_DATABASE_URL")
    if not target:
        raise ValueError("Set ADMIN_DATABASE_URL explicitly; .env and DATABASE_URL are not used as targets")
    url = make_url(target)
    if (url.get_backend_name() != "postgresql" or url.database != args.database_name
            or not url.host or url.port is None or not url.username):
        raise ValueError("ADMIN_DATABASE_URL must select the exact --database-name PostgreSQL database")
    if set(url.query) - {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "application_name"}:
        raise ValueError("Connection target, credentials and search path must not be overridden by URL query parameters")
    # Application validation imports require DATABASE_URL; overwrite ambient values
    # with the explicit target before importing any application modules.
    url = url.set(drivername="postgresql+psycopg")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    from app.services import admin_grant_service as service

    engine = create_async_engine(url, echo=False, connect_args={"options": "-c search_path=public"})
    try:
        # libpq environment/service defaults must not silently redirect a command.
        # This connection closes its read transaction before the fresh mutation.
        async with engine.connect() as connection:
            actual_database = await connection.scalar(text("SELECT current_database()"))
            if actual_database != args.database_name:
                raise ValueError("Connected database does not match --database-name")
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if args.command == "bootstrap":
                return await service.bootstrap_admin(db, target_user_id=args.target_user_id, rationale=args.rationale)
            if args.command == "grant":
                return await service.grant_admin(db, actor_user_id=args.actor_user_id,
                    target_user_id=args.target_user_id, role=args.role, rationale=args.rationale)
            if args.command == "revoke":
                return await service.revoke_admin(db, actor_user_id=args.actor_user_id,
                    admin_account_id=args.admin_account_id, rationale=args.rationale)
            return await service.list_grants(db, actor_user_id=args.actor_user_id,
                                            target_user_id=args.target_user_id)
    finally:
        await engine.dispose()


def main(argv=None) -> int:
    command_parser = parser()
    args = command_parser.parse_args(argv)
    if args.command == "bootstrap" and not args.confirm_first_admin:
        command_parser.error("bootstrap requires --confirm-first-admin")
    if args.command != "list" and not args.apply:
        print(json.dumps({"applied": False, "plan": vars(args)}, ensure_ascii=False))
        return 0
    try:
        if sys.platform == "win32":
            # Psycopg async connections require a selector loop on Windows.
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                result = runner.run(execute(args))
        else:
            result = asyncio.run(execute(args))
    except Exception as error:
        # Neither exception repr nor a URL is printed (drivers may include secrets).
        # A target/config validation error can occur before app settings load.
        if type(error).__name__ == "ApiError" and hasattr(error, "code"):
            message = {"code": error.code, "message": error.message}
        elif isinstance(error, AmbientDatabaseRoutingError):
            message = {"code": "AMBIENT_DATABASE_ROUTING", "message": "PGHOSTADDR, PGSERVICE, PGSERVICEFILE 환경변수를 해제하고 ADMIN_DATABASE_URL로 접속 대상을 명시해 주세요."}
        elif isinstance(error, ValueError):
            message = {"code": "INVALID_ADMIN_COMMAND", "message": "명령과 명시한 DB 대상을 확인해 주세요."}
        else:
            message = {"code": "ADMIN_COMMAND_UNAVAILABLE", "message": "처리 결과를 확인하지 못했습니다. 권한과 감사 기록을 확인해 주세요."}
        print(json.dumps(message, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
