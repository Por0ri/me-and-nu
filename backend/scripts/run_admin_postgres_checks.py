"""Run admin integration checks in an isolated, automatically removed PG container.

Uses an existing local pgvector image; does not connect to application DB settings,
mount application secrets, publish beyond loopback, or modify any existing container.
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from uuid import uuid4


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def cli_smoke(backend, env, container):
    """Exercise the actual CLI process/driver against this fresh public schema."""
    setup = """
import os
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from app.db.migration_policy import SERVICE_SCHEMA_REVISION
command.upgrade(Config('alembic.ini'), SERVICE_SCHEMA_REVISION)
with create_engine(os.environ['ADMIN_DATABASE_URL']).begin() as db:
    db.execute(text("INSERT INTO user_account(user_id,nickname,auth_provider,provider_user_id) VALUES (700001,'CLI synthetic test','kakao','admin-cli-smoke')"))
"""
    subprocess.run([sys.executable, "-c", setup], cwd=backend, env=env, check=True, capture_output=True, text=True)
    script = str(backend / "scripts" / "manage_admin_grants.py")
    bootstrap = subprocess.run([sys.executable, script, "bootstrap", "--database-name", "admin_be_test",
        "--target-user-id", "700001", "--confirm-first-admin", "--rationale", "Disposable CLI integration test", "--apply"],
        cwd=backend, env=env, check=True, capture_output=True, text=True, encoding="utf-8")
    result = json.loads(bootstrap.stdout)
    assert result["userId"] == 700001 and result["role"] == "admin"
    assert result["adminAccountId"] > 0 and result["auditLogId"] > 0
    listed = subprocess.run([sys.executable, script, "list", "--database-name", "admin_be_test",
        "--actor-user-id", "700001"], cwd=backend, env=env, check=True, capture_output=True, text=True, encoding="utf-8")
    grants = json.loads(listed.stdout)
    assert len(grants) == 1 and grants[0]["user_id"] == 700001
    assert grants[0]["role"] == "admin" and grants[0]["revoked_at"] is None
    persisted = docker("exec", container, "psql", "-U", "admin_test", "-d", "admin_be_test", "-Atc",
        "SELECT (SELECT count(*) FROM admin_account WHERE user_id=700001 AND revoked_at IS NULL),"
        "(SELECT count(*) FROM admin_action_log WHERE action_type='admin.bootstrap'),"
        "(SELECT count(*) FROM user_account WHERE user_id=700001)")
    assert persisted == "1|1|1"
    print("CLI bootstrap --apply -> committed grant/audit -> authenticated list: PASS", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="pgvector/pgvector:pg17")
    parser.add_argument("--cli-smoke-only", action="store_true",
                        help="Run only the real CLI bootstrap/list checks in the disposable DB")
    parser.add_argument("--test-filter", help="Optional pytest -k selection for a targeted isolated regression")
    parser.add_argument("--test-file", action="append", choices=[
        "tests/test_admin_postgres_integration.py", "tests/test_admin_analytics_postgres.py",
        "tests/test_admin_notices_postgres.py", "tests/test_notice_delivery_postgres.py",
    ], help="Repeat to select isolated admin suites (default: admin integration)")
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    name = "menu-admin-test-" + uuid4().hex
    password = secrets.token_hex(24)
    created = False
    try:
        docker("image", "inspect", args.image)  # No implicit download or image build.
        docker("run", "--detach", "--name", name, "--label", "menu.test=admin-be-integration",
               "--env", "POSTGRES_USER=admin_test", "--env", f"POSTGRES_PASSWORD={password}",
               "--env", "POSTGRES_DB=admin_be_test", "--publish", "127.0.0.1::5432",
               "--tmpfs", "/var/lib/postgresql/data", args.image)
        created = True
        for _ in range(120):
            try:
                docker("exec", name, "pg_isready", "-U", "admin_test", "-d", "admin_be_test")
                break
            except subprocess.CalledProcessError:
                time.sleep(0.25)
        else:
            raise RuntimeError("Isolated PostgreSQL did not become ready")
        binding = docker("port", name, "5432/tcp")
        match = re.fullmatch(r"127\.0\.0\.1:(\d+)", binding)
        if not match:
            raise RuntimeError("Unexpected PostgreSQL test port binding")
        url = f"postgresql+psycopg://admin_test:{password}@127.0.0.1:{match[1]}/admin_be_test"
        env = {**os.environ, "DATABASE_URL": url, "ADMIN_POSTGRES_TEST_URL": url, "ADMIN_DATABASE_URL": url,
               "ENABLE_DEV_AUTH_BYPASS": "false", "SESSION_BACKEND": "postgres",
               "CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED": "false", "PYTHONIOENCODING": "utf-8"}
        version = docker("exec", name, "psql", "-U", "admin_test", "-d", "admin_be_test", "-Atc", "SHOW server_version")
        print(f"Isolated PostgreSQL {version}; disposable schemas; real migration and AsyncSession checks.", flush=True)
        if args.cli_smoke_only:
            return cli_smoke(backend, env, name)
        selection = ["-k", args.test_filter] if args.test_filter else []
        test_files = args.test_file or ["tests/test_admin_postgres_integration.py"]
        return subprocess.run([sys.executable, "-m", "pytest", *test_files, "-q", "-p", "no:cacheprovider", *selection],
                              cwd=backend, env=env).returncode
    finally:
        if created:
            assert re.fullmatch(r"menu-admin-test-[0-9a-f]{32}", name)
            docker("rm", "--force", "--volumes", name)


if __name__ == "__main__":
    raise SystemExit(main())
