"""Run synthetic withdrawal checks in a disposable, loopback-only PostgreSQL.

No application env file, mounted credentials, existing-container changes or
network image downloads. The unique container and its tmpfs are removed finally.
"""

import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from uuid import uuid4


def docker(*args):
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("DOCKER_", "COMPOSE_"))}
    return subprocess.run(["docker", "--context", "default", *args], env=environment,
                          check=True, text=True, capture_output=True, timeout=120).stdout.strip()


def main():
    backend = Path(__file__).resolve().parents[1]
    # Pydantic must not read private files even if someone later reuses this runner.
    if (backend / ".env").exists():
        raise SystemExit("Run from the isolated checkout without backend/.env")
    name = "menu-main-withdrawal-test-" + uuid4().hex
    password = secrets.token_hex(24)
    created = False
    results = []
    try:
        image = "pgvector/pgvector:pg17"
        docker("image", "inspect", image)
        docker("run", "--detach", "--name", name, "--label", "menu.test=main-withdrawal",
            "--env", "POSTGRES_USER=withdrawal_test", "--env", f"POSTGRES_PASSWORD={password}",
            "--env", "POSTGRES_DB=main_withdrawal_test", "--publish", "127.0.0.1::5432",
            "--tmpfs", "/var/lib/postgresql/data", image)
        created = True
        for _ in range(120):
            try:
                docker("exec", name, "pg_isready", "-U", "withdrawal_test", "-d", "main_withdrawal_test")
                break
            except subprocess.CalledProcessError:
                time.sleep(0.25)
        else:
            raise RuntimeError("Disposable PostgreSQL did not start")
        match = re.fullmatch(r"127\.0\.0\.1:(\d+)", docker("port", name, "5432/tcp"))
        if not match:
            raise RuntimeError("Unexpected test port")
        url = f"postgresql+psycopg://withdrawal_test:{password}@127.0.0.1:{match[1]}/main_withdrawal_test"
        docker("exec", name, "createdb", "-U", "withdrawal_test", "notification_settings_test")
        env = {**os.environ, "DATABASE_URL": url, "MAIN_WITHDRAWAL_TEST_URL": url,
            "NOTIFICATION_SETTINGS_TEST_DATABASE_URL": url.replace("/main_withdrawal_test", "/notification_settings_test"),
            "SESSION_BACKEND": "postgres", "REDIS_URL": "", "ENABLE_DEV_AUTH_BYPASS": "false",
            "DEBATE_ENABLED": "false", "CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED": "false",
            "OPENAI_API_KEY": "", "LLM_KEY": "", "TMDB_KEY": "", "DICT_DATABASE_URL": "",
            "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        tests = ["tests/test_withdrawal.py", "tests/test_oauth_postgres_flow.py", "tests/test_reports.py",
            "tests/test_redis_auth_api.py", "tests/test_main_withdrawal_postgres.py", "tests/test_notification_settings_api.py"]
        print("Running isolated SQLite API regression and PostgreSQL row-lock checks.", flush=True)
        result = subprocess.run([sys.executable, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider"],
            cwd=backend, env=env)
        results.append({"tests": tests, "exit_code": result.returncode})
        return result.returncode
    finally:
        removed = not created
        if created:
            assert re.fullmatch(r"menu-main-withdrawal-test-[0-9a-f]{32}", name)
            docker("rm", "--force", "--volumes", name)
            removed = True
        print(json.dumps({"checks": results, "temporary_container_removed": removed,
            "live_database_access": False, "application_env_read": False}), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
