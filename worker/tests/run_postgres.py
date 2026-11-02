"""Run integration tests only in the named test database on jobs-db."""
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg.conninfo import make_conninfo
from menu_worker.job_store import validate_database_url


def main():
    settings = validate_database_url(os.environ.get("CONTENT_JOB_DATABASE_URL", ""))
    # Local Compose only. No test provisioning against other configured servers.
    if settings["host"] != "jobs-db":
        raise RuntimeError("Use the isolated jobs-db Compose service")
    with psycopg.connect(**settings, autocommit=True) as connection:
        if connection.execute("SELECT current_database()").fetchone()[0] != "menu_content_jobs":
            raise RuntimeError("Unexpected database")
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname='menu_content_jobs_test'").fetchone():
            connection.execute("CREATE DATABASE menu_content_jobs_test")
    settings["dbname"] = "menu_content_jobs_test"
    os.environ["CONTENT_JOB_TEST_DATABASE_URL"] = make_conninfo(**settings)
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern="test_jobs_postgres.py")
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
