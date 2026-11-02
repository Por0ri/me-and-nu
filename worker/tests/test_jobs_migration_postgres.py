"""Exercise fresh and v1 schemas in a database created by this invocation."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import json
import os
from pathlib import Path
import threading
import unittest
from uuid import uuid4


ADMIN_ENV = "CONTENT_JOB_MIGRATION_TEST_ADMIN_URL"
ADMIN_DB = "menu_content_jobs_test"
MIGRATION_DB = "menu_content_jobs_migration_test"
V1_SQL = Path(__file__).resolve().parents[1] / "menu_worker/migrations/001_content_jobs.sql"


@unittest.skipUnless(os.environ.get(ADMIN_ENV), "Run tests/run_postgres.py on the isolated jobs-db service")
class JobSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.psycopg = importlib.import_module("psycopg")
        storage = importlib.import_module("menu_worker.job_store")
        self.JobStore = storage.JobStore
        settings = storage.validate_database_url(os.environ[ADMIN_ENV], expected_database=ADMIN_DB)
        if settings["host"] != "jobs-db":
            self.fail("Migration fixtures require the isolated Compose jobs-db service")

        self.admin = self.psycopg.connect(**settings, autocommit=True, connect_timeout=5)
        self.owner_oid = None
        self.addCleanup(self.dispose_database)
        self.assertEqual(self.admin.execute("SELECT current_database()").fetchone()[0], ADMIN_DB)
        acquired = self.admin.execute("SELECT pg_try_advisory_lock(20261102, 902)").fetchone()[0]
        self.assertTrue(acquired, "Another migration test is using the fixture database")
        if self.database_oid() is not None:
            self.fail("Migration fixture database already exists; refusing to alter its data")
        self.admin.execute("CREATE DATABASE menu_content_jobs_migration_test TEMPLATE template0")
        self.owner_oid = self.database_oid()
        self.assertIsNotNone(self.owner_oid)
        settings["dbname"] = MIGRATION_DB
        self.dsn = self.psycopg.conninfo.make_conninfo(**settings)
        with self.connect() as connection:
            self.assertEqual(connection.execute("SELECT current_database() AS name").fetchone()["name"], MIGRATION_DB)

    def database_oid(self):
        row = self.admin.execute("SELECT oid FROM pg_database WHERE datname=%s", (MIGRATION_DB,)).fetchone()
        return row[0] if row else None

    def dispose_database(self):
        try:
            if self.owner_oid is not None:
                self.assertEqual(self.admin.execute("SELECT current_database()").fetchone()[0], ADMIN_DB)
                self.assertEqual(self.database_oid(), self.owner_oid, "Fixture database identity changed")
                self.admin.execute("DROP DATABASE menu_content_jobs_migration_test")
        finally:
            self.admin.close()

    def connect(self):
        return self.psycopg.connect(self.dsn, row_factory=self.psycopg.rows.dict_row, connect_timeout=5)

    def store(self):
        return self.JobStore(self.dsn, expected_database=MIGRATION_DB)

    def initialize_concurrently(self):
        barrier = threading.Barrier(2, timeout=10)

        def initialize():
            store = self.store()
            barrier.wait()
            return store.initialize()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(initialize) for _ in range(2)]
            return [future.result(timeout=20) for future in futures]

    def test_fresh_install_and_concurrent_or_repeated_initialize(self):
        results = self.initialize_concurrently()
        self.assertEqual([result["version"] for result in results], [2, 2])
        self.assertCountEqual([result["created"] for result in results], [True, False])
        store = self.store()
        repeated = store.initialize()
        self.assertEqual(repeated["version"], 2)
        self.assertFalse(repeated["created"])
        for kind in ("fake", "anime"):
            request = {"idempotency_key": kind, "source_id": "fixture", "content_type": "news",
                       "source_url": f"https://example.invalid/success/fresh-{kind}"}
            job = store.submit(request, job_kind=kind)
            self.assertTrue(job["created"])
            self.assertEqual(job["job_kind"], kind)

    def seed_v1(self):
        # Populate the original schema directly, including historical hashes,
        # before opening it with the current JobStore implementation.
        job_ids = [uuid4(), uuid4()]
        attempt = uuid4()
        result_bytes = b'{"producer":"fake_ani_menu_agent","outcome":"ready_for_staging"}'
        digest = hashlib.sha256(result_bytes).hexdigest()
        path = f"jobs/{job_ids[0]}/attempts/{attempt}/manifest.json"
        requests = []
        with self.connect() as connection:
            connection.execute(V1_SQL.read_text(encoding="utf-8"), prepare=False)
            self.assertEqual(connection.execute("SELECT version FROM public.menu_worker_schema").fetchone()["version"], 1)
            for number, job_id in enumerate(job_ids):
                payload = {"source_id": "fixture", "source_url": f"https://example.invalid/success/legacy-{number}",
                           "content_type": "news"}
                encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                request = {**payload, "idempotency_key": f"legacy-{number}"}
                requests.append(request)
                connection.execute(
                    """INSERT INTO public.content_generation_job
                       (job_id, idempotency_key, source_id, source_url, canonical_url,
                        content_type, input_sha256, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,'2026-10-31 00:01:00+00','2026-10-31 00:02:00+00')""",
                    (job_id, request["idempotency_key"], request["source_id"], request["source_url"],
                     request["source_url"], request["content_type"], hashlib.sha256(encoded).hexdigest()),
                )
            connection.execute(
                """INSERT INTO public.content_generation_attempt
                   (attempt_id, job_id, attempt_no, mode, celery_task_id, claim_token, execution_status,
                    generation_started_at, outcome, producer_status, artifact_path, result_sha256,
                    started_at, finished_at)
                   VALUES (%s,%s,1,'generation','legacy-task',%s,'succeeded',
                           '2026-10-31 00:03:00+00','ready_for_staging','ready_for_feed',%s,%s,
                           '2026-10-31 00:02:30+00','2026-10-31 00:05:00+00')""",
                (attempt, job_ids[0], uuid4(), path, digest),
            )
            connection.execute(
                """UPDATE public.content_generation_job SET
                   execution_status='succeeded', outcome='ready_for_staging', producer_status='ready_for_feed',
                   current_attempt_id=%s, generation_attempt_id=%s, attempt_count=1,
                   generation_started_at='2026-10-31 00:03:00+00', dispatch_count=2,
                   last_dispatched_at='2026-10-31 00:02:20+00', last_task_id='legacy-task',
                   artifact_path=%s, result_sha256=%s, updated_at='2026-10-31 00:05:00+00',
                   finished_at='2026-10-31 00:05:00+00' WHERE job_id=%s""",
                (attempt, attempt, path, digest, job_ids[0]),
            )
        return job_ids, requests

    def snapshot(self):
        with self.connect() as connection:
            jobs = connection.execute("SELECT * FROM public.content_generation_job ORDER BY job_id").fetchall()
            attempts = connection.execute("SELECT * FROM public.content_generation_attempt ORDER BY attempt_id").fetchall()
        return jobs, attempts

    def test_concurrent_v1_upgrade_preserves_all_legacy_columns_and_idempotency(self):
        job_ids, inputs = self.seed_v1()
        old_jobs, old_attempts = self.snapshot()
        results = self.initialize_concurrently()
        self.assertEqual([result["version"] for result in results], [2, 2])
        self.assertFalse(self.store().initialize()["created"])
        jobs, attempts = self.snapshot()
        self.assertEqual(len(jobs), len(old_jobs))
        self.assertEqual(len(attempts), len(old_attempts))
        for before, after in zip(old_jobs, jobs, strict=True):
            self.assertEqual({column: after[column] for column in before}, before)
            self.assertEqual(after["job_kind"], "fake")
        for before, after in zip(old_attempts, attempts, strict=True):
            self.assertEqual({column: after[column] for column in before}, before)
        store = self.store()
        for job_id, request in zip(job_ids, inputs, strict=True):
            repeated = store.submit(request)
            self.assertFalse(repeated["created"])
            self.assertEqual(repeated["job_id"], str(job_id))
        self.assertIsNone(store.claim(str(job_ids[0])))
        self.assertIsNone(store.claim(str(job_ids[1]), expected_kind="anime"))
        self.assertIsNotNone(store.claim(str(job_ids[1])))


if __name__ == "__main__":
    unittest.main()
