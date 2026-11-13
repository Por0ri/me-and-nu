"""Exercise schema upgrades in a database created by this invocation."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from uuid import uuid4


ADMIN_ENV = "CONTENT_JOB_MIGRATION_TEST_ADMIN_URL"
ADMIN_DB = "menu_content_jobs_test"
MIGRATION_DB = "menu_content_jobs_migration_test"
V1_SQL = Path(__file__).resolve().parents[1] / "menu_worker/migrations/001_content_jobs.sql"
V2_SQL = V1_SQL.with_name("002_job_kind.sql")
V3_SQL = V1_SQL.with_name("003_anime_work_input.sql")
V4_SQL = V1_SQL.with_name("004_anime_generation.sql")


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
        self.assertEqual([result["version"] for result in results], [5, 5])
        self.assertCountEqual([result["created"] for result in results], [True, False])
        store = self.store()
        repeated = store.initialize()
        self.assertEqual(repeated["version"], 5)
        self.assertFalse(repeated["created"])
        for kind in ("fake", "anime", "anime_generate"):
            request = {"idempotency_key": kind, "source_id": "fixture", "content_type": "news",
                       "source_url": f"https://example.invalid/success/fresh-{kind}"}
            if kind != "fake":
                request.update(source_id="one_piece_official", content_type="goods", menu_work_id=8,
                               source_url="https://one-piece.com/news/82812/index.html")
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
        self.assertEqual([result["version"] for result in results], [5, 5])
        self.assertFalse(self.store().initialize()["created"])
        jobs, attempts = self.snapshot()
        self.assertEqual(len(jobs), len(old_jobs))
        self.assertEqual(len(attempts), len(old_attempts))
        for before, after in zip(old_jobs, jobs, strict=True):
            self.assertEqual({column: after[column] for column in before}, before)
            self.assertEqual(after["job_kind"], "fake")
            self.assertIsNone(after["menu_work_id"])
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

    def seed_v2_anime_probe(self, connection, phase):
        job_id, attempt_id, token = uuid4(), uuid4(), uuid4()
        url = f"https://example.invalid/success/v2-{phase}"
        payload = {"source_id": "fixture", "source_url": url, "content_type": "news", "job_kind": "anime"}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        connection.execute(
            """INSERT INTO public.content_generation_job
               (job_id,idempotency_key,source_id,source_url,canonical_url,content_type,input_sha256,job_kind)
               VALUES (%s,%s,'fixture',%s,%s,'news',%s,'anime')""",
            (job_id, f"v2-{phase}", url, url, digest),
        )
        if phase == "queued":
            return str(job_id)

        running = phase in ("before_start", "after_start")
        started = phase != "before_start"
        status = "running" if running else phase
        outcome = "routing_verified" if phase == "succeeded" else None
        producer = "anime_probe_completed" if phase == "succeeded" else None
        path = f"jobs/{job_id}/attempts/{attempt_id}/manifest.json" if phase == "succeeded" else None
        result_hash = hashlib.sha256(b"preserved-v2-anime-probe").hexdigest() if path else None
        connection.execute(
            """INSERT INTO public.content_generation_attempt
               (attempt_id,job_id,attempt_no,mode,celery_task_id,claim_token,execution_status,generation_started_at,
                outcome,producer_status,artifact_path,result_sha256,finished_at)
               VALUES (%s,%s,1,'generation',%s,%s,%s,CASE WHEN %s THEN CURRENT_TIMESTAMP END,
                       %s,%s,%s,%s,CASE WHEN %s THEN CURRENT_TIMESTAMP END)""",
            (attempt_id, job_id, f"v2-task-{phase}", token, status, started, outcome, producer, path,
             result_hash, not running),
        )
        connection.execute(
            """UPDATE public.content_generation_job SET execution_status=%s,
               current_attempt_id=%s,generation_attempt_id=%s,attempt_count=1,claim_token=%s,
               lease_expires_at=CASE WHEN %s THEN clock_timestamp()-INTERVAL '1 second' END,
               generation_started_at=CASE WHEN %s THEN CURRENT_TIMESTAMP END,
               outcome=%s,producer_status=%s,artifact_path=%s,result_sha256=%s,
               finished_at=CASE WHEN %s THEN CURRENT_TIMESTAMP END WHERE job_id=%s""",
            (status, attempt_id, attempt_id, token if running else None, running, started,
             outcome, producer, path, result_hash, not running, job_id),
        )
        return str(job_id)

    def test_concurrent_v2_upgrade_preserves_results_and_retires_fixture_delivery(self):
        phases = ("succeeded", "queued", "needs_attention", "before_start", "after_start")
        with self.connect() as connection:
            for migration in (V1_SQL, V2_SQL):
                connection.execute(migration.read_text(encoding="utf-8"), prepare=False)
            ids = {phase: self.seed_v2_anime_probe(connection, phase) for phase in phases}
        old_jobs, old_attempts = self.snapshot()
        self.assertEqual([item["version"] for item in self.initialize_concurrently()], [5, 5])
        store = self.store()
        self.assertFalse(store.initialize()["created"])
        jobs, attempts = self.snapshot()
        self.assertEqual(attempts, old_attempts)
        for before, after in zip(old_jobs, jobs, strict=True):
            updated_fields = set()
            if str(before["job_id"]) == ids["queued"]:
                updated_fields = {"execution_status", "error_code", "updated_at", "finished_at"}
                self.assertEqual(after["execution_status"], "cancelled")
                self.assertEqual(after["error_code"], "LEGACY_ANIME_FIXTURE_RETIRED")
                self.assertIsNotNone(after["finished_at"])
            for column in before.keys() - updated_fields:
                self.assertEqual(after[column], before[column], column)
            self.assertIsNone(after["menu_work_id"])

        self.assertEqual(store.list_pending(expected_kind="anime"), [])
        self.assertIsNone(store.claim(ids["queued"], expected_kind="anime"))
        self.assertIsNone(store.claim_recovery(ids["needs_attention"], expected_kind="anime"))
        store.recover_expired(expected_kind="anime")
        for phase, expected in (("before_start", "cancelled"), ("after_start", "needs_attention")):
            with self.subTest(phase=phase):
                job = store.get(ids[phase])
                self.assertEqual(job["execution_status"], expected)
                self.assertEqual(job["error_code"], "LEGACY_ANIME_FIXTURE_RETIRED")
                self.assertEqual(len(job["attempts"]), 1)
                self.assertIsNone(store.claim(ids[phase], expected_kind="anime"))
                self.assertIsNone(store.claim_recovery(ids[phase], expected_kind="anime"))
        self.assertEqual(store.list_pending(expected_kind="anime"), [])

    def test_concurrent_v3_upgrade_preserves_probe_and_allows_one_generation_for_its_url(self):
        self.seed_v1()
        with self.connect() as connection:
            for migration in (V2_SQL, V3_SQL):
                connection.execute(migration.read_text(encoding="utf-8"), prepare=False)
        probe_id, attempt_id = uuid4(), uuid4()
        request = {"idempotency_key": "v3-article", "source_id": "one_piece_official", "menu_work_id": 8,
                   "source_url": "https://one-piece.com/news/82812/index.html", "content_type": "goods"}
        identity = {key: value for key, value in request.items() if key != "idempotency_key"}
        identity["job_kind"] = "anime"
        input_hash = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        path = f"jobs/{probe_id}/attempts/{attempt_id}/manifest.json"
        result_hash = hashlib.sha256(b"v3-retained-probe").hexdigest()
        with self.connect() as connection:
            self.assertEqual(connection.execute("SELECT version FROM public.menu_worker_schema").fetchone()["version"], 3)
            connection.execute(
                """INSERT INTO public.content_generation_job
                   (job_id,idempotency_key,source_id,source_url,canonical_url,content_type,menu_work_id,input_sha256,
                    job_kind,execution_status,outcome,producer_status,current_attempt_id,generation_attempt_id,
                    attempt_count,generation_started_at,finished_at,artifact_path,result_sha256)
                   VALUES (%s,%s,%s,%s,%s,%s,8,%s,'anime','succeeded','routing_verified','anime_probe_completed',
                           %s,%s,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,%s,%s)""",
                (probe_id, request["idempotency_key"], request["source_id"], request["source_url"], request["source_url"],
                 request["content_type"], input_hash, attempt_id, attempt_id, path, result_hash),
            )
            connection.execute(
                """INSERT INTO public.content_generation_attempt
                   (attempt_id,job_id,attempt_no,mode,celery_task_id,claim_token,execution_status,generation_started_at,
                    outcome,producer_status,artifact_path,result_sha256,finished_at)
                   VALUES (%s,%s,1,'generation','original-probe',%s,'succeeded',CURRENT_TIMESTAMP,
                           'routing_verified','anime_probe_completed',%s,%s,CURRENT_TIMESTAMP)""",
                (attempt_id, probe_id, uuid4(), path, result_hash),
            )
        old_jobs, old_attempts = self.snapshot()
        self.assertEqual([item["version"] for item in self.initialize_concurrently()], [5, 5])
        jobs, attempts = self.snapshot()
        self.assertEqual(attempts, old_attempts)
        for before, after in zip(old_jobs, jobs, strict=True):
            self.assertEqual({column: after[column] for column in before}, before)
            self.assertEqual(after["candidate_url"], before["canonical_url"])
            self.assertIsNone(after["duplicate_of_job_id"])
        store = self.store()
        self.assertFalse(store.initialize()["created"])
        self.assertFalse(store.submit(request, job_kind="anime")["created"])

        probe = store.get(str(probe_id))
        generated = store.submit({**request, "idempotency_key": "new-generation"}, job_kind="anime_generate")
        self.assertTrue(generated["created"])
        self.assertNotEqual(generated["input_sha256"], probe["input_sha256"])
        self.assertEqual(generated["canonical_url"], probe["canonical_url"])
        self.assertEqual(store.get(str(probe_id)), probe)
        storage = importlib.import_module("menu_worker.job_store")
        for kind, owner in (("anime", probe_id), ("anime_generate", generated["job_id"])):
            with self.subTest(kind=kind), self.assertRaises(storage.JobConflict) as conflict:
                store.submit({**request, "idempotency_key": f"duplicate-{kind}"}, job_kind=kind)
            self.assertEqual(conflict.exception.code, "candidate_exists")
            self.assertEqual(conflict.exception.existing_job_id, str(owner))

    def install_v4(self):
        with self.connect() as connection:
            for migration in (V1_SQL, V2_SQL, V3_SQL, V4_SQL):
                connection.execute(migration.read_text(encoding="utf-8"), prepare=False)

    def seed_v4_job(self, connection, key, url, status, started, *, job_kind="anime_generate", error_code=None):
        request = {"source_id": "one_piece_official", "content_type": "goods", "menu_work_id": 8}
        job_id, attempt_id, token = uuid4(), uuid4(), uuid4()
        identity = {**request, "job_kind": job_kind, "source_url": url}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        connection.execute(
            """INSERT INTO public.content_generation_job
               (job_id,idempotency_key,source_id,source_url,canonical_url,content_type,menu_work_id,input_sha256,
                job_kind,execution_status,claim_token,lease_expires_at,generation_started_at,error_code)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       CASE WHEN %s THEN clock_timestamp()+INTERVAL '1 hour' END,
                       CASE WHEN %s THEN CURRENT_TIMESTAMP END, %s)""",
            (job_id, key, request["source_id"], url, url, request["content_type"], request["menu_work_id"],
             digest, job_kind, status, token if status == "running" else None, status == "running", started,
             error_code),
        )
        if status not in {"queued", "cancelled"}:
            connection.execute(
                """INSERT INTO public.content_generation_attempt
                   (attempt_id,job_id,attempt_no,mode,claim_token,execution_status,generation_started_at,error_code)
                   VALUES (%s,%s,1,'generation',%s,%s,CASE WHEN %s THEN CURRENT_TIMESTAMP END,%s)""",
                (attempt_id, job_id, token, status, started, error_code),
            )
            connection.execute(
                """UPDATE public.content_generation_job SET current_attempt_id=%s,generation_attempt_id=%s,
                   attempt_count=1 WHERE job_id=%s""", (attempt_id, attempt_id, job_id),
            )
        return str(job_id), str(token)

    def test_group_retry_blocks_timeout_and_recovery_after_v4_upgrade(self):
        kind = "anime_generate"
        url = "https://one-piece.com/news/82812"
        self.install_v4()
        with self.connect() as connection:
            spawn, _ = self.seed_v4_job(connection, "legacy-spawn", url, "needs_attention", True,
                                        error_code="agent_spawn_failed")
            timeout, _ = self.seed_v4_job(connection, "legacy-timeout", url + "/", "needs_attention", True,
                                          error_code="agent_timeout")
            # Match a v4 database where the older spawn failure became the v5 representative.
            connection.execute("UPDATE public.content_generation_job SET created_at=created_at-INTERVAL '1 day' "
                               "WHERE job_id=%s", (spawn,))
            running = dict(self.seed_v4_job(connection, key, spelling, "running", True) for key, spelling in
                           (("live-a", url + "?id=42"), ("live-b", url + "/?id=42")))
        store = self.store()
        store.initialize()
        self.assertFalse(store.retry_preflight(spawn, expected_kind=kind))
        self.assertFalse(store.retry_preflight(timeout, expected_kind=kind))
        for job_id in (spawn, timeout):
            self.assertIsNone(store.claim(job_id, expected_kind=kind))
            self.assertFalse(store.publish_pending(job_id, self.fail, expected_kind=kind))

        owner, sibling = sorted(running, key=lambda job_id: store.get(job_id)["duplicate_of_job_id"] is not None)
        self.assertEqual(store.get(sibling)["duplicate_of_job_id"], owner)
        # Both were armed at migration time. Only the owner subsequently confirms it never spawned.
        self.assertTrue(store.complete(owner, running[owner], expected_kind=kind, execution_status="failed",
                                       error_code="agent_spawn_failed", agent_not_launched=True))
        self.assertFalse(store.retry_preflight(owner, expected_kind=kind))
        self.assertTrue(store.complete(sibling, running[sibling], expected_kind=kind,
                                       execution_status="needs_attention", error_code="agent_timeout"))
        recovery = store.claim_recovery(sibling, expected_kind=kind)
        self.assertIsNotNone(recovery)
        self.assertFalse(store.retry_preflight(owner, expected_kind=kind))
        self.assertTrue(store.complete(sibling, recovery["claim_token"], expected_kind=kind,
                                       execution_status="needs_attention", error_code="artifact_verification_failed"))
        self.assertFalse(store.retry_preflight(owner, expected_kind=kind))
        self.assertEqual(store.list_pending(expected_kind=kind), [])
        self.assertFalse(store.publish_pending(owner, self.fail, expected_kind=kind))
        self.assertIsNone(store.claim(owner, expected_kind=kind))

    def test_group_with_only_confirmed_prelaunch_failures_requeues_one_owner(self):
        kind = "anime_generate"
        url = "https://one-piece.com/news/82812"
        self.install_v4()
        with self.connect() as connection:
            first, _ = self.seed_v4_job(connection, "env", url, "needs_attention", True,
                                        error_code="invalid_agent_secret")
            second, _ = self.seed_v4_job(connection, "spawn", url + "/", "needs_attention", True,
                                         error_code="agent_spawn_failed")
            self.seed_v4_job(connection, "probe", url, "needs_attention", True, job_kind="anime",
                             error_code="artifact_verification_required")
        store = self.store()
        store.initialize()
        owner, sibling = sorted((first, second), key=lambda job_id: store.get(job_id)["duplicate_of_job_id"] is not None)
        self.assertFalse(store.retry_preflight(sibling, expected_kind=kind))
        barrier = threading.Barrier(2, timeout=10)

        def retry_owner():
            barrier.wait()
            return self.store().retry_preflight(owner, expected_kind=kind)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(retry_owner) for _ in range(2)]
            self.assertCountEqual([future.result(timeout=20) for future in futures], [True, False])
        self.assertEqual([row["job_id"] for row in store.list_pending(expected_kind=kind)], [owner])
        self.assertIsNone(store.claim(sibling, expected_kind=kind))
        self.assertIsNotNone(store.claim(owner, expected_kind=kind))

    def test_v4_slash_collision_preserves_keys_and_fences_unstarted_deliveries(self):
        kind = "anime_generate"
        request = {"source_id": "one_piece_official", "content_type": "goods", "menu_work_id": 8}
        storage = importlib.import_module("menu_worker.job_store")
        self.install_v4()

        base = "https://one-piece.com/news/82812"
        with self.connect() as connection:
            claimed, token = self.seed_v4_job(connection, "claimed", base + "?id=8", "running", False)
            owner, _ = self.seed_v4_job(connection, "owner", base + "/?id=8", "needs_attention", True)
            failed, _ = self.seed_v4_job(connection, "failed", base + "?id=9", "failed", False,
                                         error_code="agent_spawn_failed")
            queued, _ = self.seed_v4_job(connection, "queued", base + "/?id=9", "queued", False)
            running_pair = dict(self.seed_v4_job(connection, key, url, "running", True) for key, url in
                                (("running-a", base + "?id=10"), ("running-b", base + "/?id=10")))
            probe_url = base + "/?id=11"
            probe_id, _ = self.seed_v4_job(connection, "legacy-probe", probe_url, "queued", False, job_kind="anime")
        old_jobs, old_attempts = self.snapshot()
        self.assertEqual([item["version"] for item in self.initialize_concurrently()], [5, 5])
        jobs, attempts = self.snapshot()
        for before, after in zip(old_jobs, jobs, strict=True):
            for column in ("job_id", "canonical_url", "input_sha256", "idempotency_key", "generation_attempt_id"):
                self.assertEqual(after[column], before[column], column)
        for before, after in zip(old_attempts, attempts, strict=True):
            if str(before["job_id"]) == claimed:
                self.assertEqual((after["execution_status"], after["error_code"]), ("cancelled", "DUPLICATE_ARTICLE"))
            else:
                self.assertEqual(after, before)

        store = self.store()
        loser = store.get(claimed)
        self.assertEqual((loser["duplicate_of_job_id"], loser["execution_status"], loser["error_code"]),
                         (owner, "cancelled", "DUPLICATE_ARTICLE"))
        self.assertFalse(store.mark_generation_started(claimed, token, expected_kind=kind))
        self.assertIsNone(store.claim(claimed, expected_kind=kind))
        self.assertEqual(store.get(failed)["duplicate_of_job_id"], queued)
        self.assertFalse(store.retry_preflight(failed, expected_kind=kind))
        for running, claim_token in running_pair.items():
            if store.get(running)["duplicate_of_job_id"] is not None:
                self.assertTrue(store.complete(running, claim_token, expected_kind=kind, execution_status="failed",
                                               error_code="agent_spawn_failed", agent_not_launched=True))
                self.assertFalse(store.retry_preflight(running, expected_kind=kind))
        self.assertEqual([row["job_id"] for row in store.list_pending(expected_kind=kind)], [queued])
        for key, expected in (("owner", owner), ("claimed", claimed)):
            for url in (base + "?id=8", base + "/?id=8"):
                replay = store.submit({**request, "idempotency_key": key, "source_url": url}, job_kind=kind)
                self.assertEqual((replay["job_id"], replay["created"]), (expected, False))
        alternate = {**request, "idempotency_key": "other", "source_url": base + "?id=8"}
        with self.assertRaises(storage.JobConflict) as conflict:
            store.submit(alternate, job_kind=kind)
        self.assertEqual((conflict.exception.code, conflict.exception.existing_job_id), ("candidate_exists", owner))
        with self.assertRaises(storage.JobConflict) as conflict:
            store.submit({**alternate, "idempotency_key": "owner", "content_type": "news"}, job_kind=kind)
        self.assertEqual(conflict.exception.code, "idempotency_conflict")
        self.assertTrue(store.submit(alternate, job_kind="anime")["created"])
        runner = importlib.import_module("menu_worker.job_runner")
        artifacts = importlib.import_module("menu_worker.job_artifacts")
        old_probe = next(row for row in old_jobs if str(row["job_id"]) == probe_id)
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.assertTrue(runner.run_job(store, root, probe_id, expected_kind="anime")["processed"])
            probe = store.get(probe_id)
            self.assertEqual((probe["execution_status"], probe["outcome"]), ("succeeded", "routing_verified"))
            self.assertEqual((probe["canonical_url"], probe["input_sha256"]), (probe_url, old_probe["input_sha256"]))
            self.assertEqual(artifacts.validate_artifacts(root, probe)["result_sha256"], probe["result_sha256"])
            result = json.loads((root / probe["artifact_path"]).with_name("result.json").read_bytes())
            self.assertEqual((result["validated_input"]["canonical_url"], result["input_sha256"], result["llm_calls"]),
                             (probe_url, old_probe["input_sha256"], 0))
        # Exercise the DB's source dimension independently of each registry's path scope.
        with self.connect() as connection:
            second = connection.execute(
                """INSERT INTO public.content_generation_job
                   (job_id,idempotency_key,source_id,source_url,canonical_url,content_type,menu_work_id,input_sha256,job_kind)
                   SELECT %s,'separate-source','other_official',source_url,canonical_url,content_type,menu_work_id,
                          input_sha256,job_kind FROM public.content_generation_job WHERE job_id=%s
                   RETURNING source_id,candidate_url,duplicate_of_job_id""", (uuid4(), owner),
            ).fetchone()
            self.assertEqual(second, {"source_id": "other_official", "candidate_url": base + "?id=8",
                                      "duplicate_of_job_id": None})


if __name__ == "__main__":
    unittest.main()
