"""Cover history reads and session fencing in an invocation-owned PostgreSQL DB.

No service DB, agent, LLM, network API or generation is used. Sealed files in
the compatibility test are synthetic metadata written in a temporary directory.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_jobs_migration_postgres as migration_tests


@unittest.skipUnless(os.environ.get(migration_tests.ADMIN_ENV),
                     "Run tests/run_postgres.py on the isolated jobs-db service")
class CoverHistoryPostgresTests(unittest.TestCase):
    def setUp(self):
        self.fixture = migration_tests.JobSchemaMigrationTests()
        self.addCleanup(self.cleanup_fixture)
        self.fixture.setUp()
        self.store = self.fixture.store()
        self.JobStoreError = importlib.import_module("menu_worker.job_store").JobStoreError

    def cleanup_fixture(self):
        self.assertTrue(self.fixture.doCleanups(), "Dedicated migration database cleanup failed")

    def seed(self, urls):
        self.fixture.install_v4()
        with self.fixture.connect() as connection:
            ids = [self.fixture.seed_v4_job(connection, f"history-{number}", url, "running", True)[0]
                   for number, url in enumerate(urls)]
        self.store.initialize()
        return [self.store.get(job_id) for job_id in ids]

    def finish(self, job, outcome="ready_for_staging", **overrides):
        evidence = {"outcome": outcome, "producer_status": {
            "ready_for_staging": "ready_for_feed", "held": "needs_crawler_update",
            "rejected": "rejected_archived", "skipped": "duplicate_skipped",
        }[outcome], "artifact_path": f"jobs/{job['job_id']}/attempts/{job['generation_attempt_id']}/manifest.json",
            "result_sha256": hashlib.sha256(job["job_id"].encode()).hexdigest(), **overrides}
        self.assertTrue(self.store.complete(job["job_id"], job["claim_token"], expected_kind="anime_generate",
                                            execution_status="succeeded", **evidence))
        return self.store.get(job["job_id"])

    def history(self, excluded=None):
        return self.store.cover_history_sources(excluded or str(uuid4()))

    def test_guard_contends_across_connections_and_releases_without_transactions(self):
        self.store.initialize()
        before = self.fixture.snapshot()
        with self.store.generation_history_guard() as check:
            self.assertTrue(callable(check))
            check()

            def contender():
                with self.fixture.store().generation_history_guard() as second:
                    return second is None

            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertTrue(pool.submit(contender).result(timeout=10))
            check()
        with self.store.generation_history_guard() as acquired:
            self.assertTrue(callable(acquired))
            acquired()
        self.assertEqual(self.fixture.snapshot(), before)

    def test_guard_releases_on_failure_and_detects_closed_or_terminated_connection(self):
        with self.assertRaisesRegex(RuntimeError, "caller_failure"):
            with self.store.generation_history_guard() as check:
                check()
                raise RuntimeError("caller_failure")
        opened = []
        connect = self.store._connect

        def capture():
            connection = connect()
            opened.append(connection)
            return connection

        with patch.object(self.store, "_connect", side_effect=capture):
            with self.store.generation_history_guard() as check:
                self.assertTrue(opened[0].autocommit)
                self.assertEqual(opened[0].info.transaction_status.name, "IDLE")
                with self.fixture.connect() as killer:
                    killed = killer.execute("SELECT pg_terminate_backend(%s) AS ok",
                                            (opened[0].info.backend_pid,)).fetchone()["ok"]
                    self.assertTrue(killed)
                with self.assertRaisesRegex(self.JobStoreError, "^cover_history_unavailable$"):
                    check()
        with self.store.generation_history_guard() as recovered:
            recovered()
        with self.assertRaisesRegex(self.JobStoreError, "^cover_history_unavailable$"):
            recovered()

    def test_busy_fence_requires_another_started_unexpired_generation_and_never_mutates(self):
        job, = self.seed(["https://one-piece.com/news/82812/index.html"])
        excluded = str(uuid4())

        def assert_busy(expected, exclude_job_id=excluded):
            before = self.fixture.snapshot()
            self.assertIs(self.store.generation_history_busy(exclude_job_id), expected)
            self.assertEqual(self.fixture.snapshot(), before)

        assert_busy(True)
        assert_busy(False, job["job_id"])
        with self.fixture.connect() as connection:
            connection.execute(
                "UPDATE public.content_generation_job SET generation_started_at=NULL WHERE job_id=%s",
                (job["job_id"],))
        assert_busy(False)
        with self.fixture.connect() as connection:
            connection.execute(
                """UPDATE public.content_generation_job SET generation_started_at=CURRENT_TIMESTAMP,
                   lease_expires_at=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE job_id=%s""", (job["job_id"],))
        assert_busy(False)
        with self.fixture.connect() as connection:
            connection.execute(
                """UPDATE public.content_generation_job SET execution_status='needs_attention',
                   claim_token=NULL,lease_expires_at=NULL WHERE job_id=%s""", (job["job_id"],))
        assert_busy(False)
        probe = self.store.submit({"idempotency_key": "history-busy-probe", "source_id": "one_piece_official",
                                   "source_url": "https://one-piece.com/news/82812/index.html",
                                   "content_type": "goods", "menu_work_id": 8}, job_kind="anime")
        claimed = self.store.claim(probe["job_id"], expected_kind="anime")
        self.assertIsNotNone(claimed)
        self.assertTrue(self.store.mark_generation_started(probe["job_id"], claimed["claim_token"],
                                                          expected_kind="anime"))
        assert_busy(False)

    def test_all_completed_outcomes_legacy_duplicates_and_original_inputs_are_preserved(self):
        urls = ["https://one-piece.com/news/82812/index.html",
                "https://one-piece.com/news/82812/index.html/",
                "https://one-piece.com/news/82813/index.html/?ref=legacy",
                "https://one-piece.com/news/82814/index.html"]
        rows = [self.finish(job, outcome) for job, outcome in zip(
            self.seed(urls), ("ready_for_staging", "held", "rejected", "skipped"), strict=True)]
        before = self.fixture.snapshot()
        results = self.history()
        self.assertEqual(len(results), 4)  # Ledger/attempt copies contribute once.
        self.assertEqual(sum(row["duplicate_of_job_id"] is not None for row in results), 1)
        by_id = {row["job_id"]: row for row in results}
        for job in rows:
            for key in ("source_url", "canonical_url", "input_sha256", "source_id", "menu_work_id",
                        "generation_attempt_id", "artifact_path", "result_sha256", "outcome", "producer_status"):
                self.assertEqual(by_id[job["job_id"]][key], job[key])
        self.assertEqual(len(self.history(rows[0]["job_id"])), 3)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_recovered_result_uses_original_generation_not_recovery_attempt(self):
        job, = self.seed(["https://one-piece.com/news/82812/index.html"])
        self.assertTrue(self.store.complete(job["job_id"], job["claim_token"], expected_kind="anime_generate",
                                            execution_status="needs_attention", error_code="agent_timeout"))
        self.assertEqual(self.history(), [])
        recovered = self.store.claim_recovery(job["job_id"], expected_kind="anime_generate")
        self.assertNotEqual(recovered["attempt_id"], job["generation_attempt_id"])
        self.finish(recovered, "rejected")
        before = self.fixture.snapshot()
        source, = self.history()
        self.assertEqual(source["generation_attempt_id"], job["generation_attempt_id"])
        self.assertEqual(source["outcome"], "rejected")
        self.assertEqual(self.fixture.snapshot(), before)

    def test_old_successful_attempt_survives_later_unresolved_generation(self):
        job, = self.seed(["https://one-piece.com/news/82812/index.html/?ref=preserved"])
        old = self.finish(job, "held")
        later = uuid4()
        with self.fixture.connect() as connection:
            connection.execute(
                """INSERT INTO public.content_generation_attempt
                   (attempt_id,job_id,attempt_no,mode,claim_token,execution_status,generation_started_at)
                   VALUES (%s,%s,2,'generation',%s,'needs_attention',CURRENT_TIMESTAMP)""",
                (later, job["job_id"], uuid4()))
            connection.execute(
                """UPDATE public.content_generation_job SET execution_status='needs_attention',
                   outcome=NULL,producer_status=NULL,artifact_path=NULL,result_sha256=NULL,
                   current_attempt_id=%s,generation_attempt_id=%s,attempt_count=2 WHERE job_id=%s""",
                (later, later, job["job_id"]))
        before = self.fixture.snapshot()
        source, = self.history()
        self.assertEqual(source["generation_attempt_id"], old["generation_attempt_id"])
        self.assertEqual(source["canonical_url"], old["canonical_url"])
        self.assertEqual(source["result_sha256"], old["result_sha256"])
        self.assertEqual(self.fixture.snapshot(), before)

    def test_probe_and_fake_successes_are_not_generation_history(self):
        self.store.initialize()
        for kind in ("fake", "anime"):
            request = {"idempotency_key": f"history-{kind}", "source_id": "fixture", "content_type": "news",
                       "source_url": "https://example.invalid/success/history"}
            if kind == "anime":
                request.update(source_id="one_piece_official", content_type="goods", menu_work_id=8,
                               source_url="https://one-piece.com/news/82812/index.html")
            job = self.store.submit(request, job_kind=kind)
            claimed = self.store.claim(job["job_id"], expected_kind=kind)
            self.assertTrue(self.store.mark_generation_started(job["job_id"], claimed["claim_token"], expected_kind=kind))
            self.assertTrue(self.store.complete(
                job["job_id"], claimed["claim_token"], expected_kind=kind, execution_status="succeeded",
                outcome="routing_verified" if kind == "anime" else "held",
                producer_status="anime_probe_completed" if kind == "anime" else "needs_crawler_update",
                artifact_path=f"jobs/{job['job_id']}/attempts/{claimed['generation_attempt_id']}/manifest.json",
                result_sha256="0" * 64))
        self.assertEqual(self.history(), [])

    def test_missing_invalid_or_conflicting_success_evidence_fails_closed(self):
        job, = self.seed(["https://one-piece.com/news/82812/index.html"])
        finished = self.finish(job)
        mutations = [
            ("content_generation_job", "artifact_path", None),
            ("content_generation_attempt", "artifact_path", None),
            ("content_generation_attempt", "result_sha256", None),
            ("content_generation_attempt", "result_sha256", "1" * 64),
            ("content_generation_attempt", "finished_at", None),
            ("content_generation_attempt", "generation_started_at", None),
            ("content_generation_attempt", "artifact_path", f"jobs/{job['job_id']}/attempts/{uuid4()}/manifest.json"),
            ("content_generation_attempt", "artifact_path", finished["artifact_path"].replace("/manifest", "/../manifest")),
            ("content_generation_attempt", "mode", "recovery"),
        ]
        for table, column, value in mutations:
            with self.subTest(table=table, column=column, value=value):
                with self.fixture.connect() as connection:
                    prior = connection.execute(f"SELECT {column} FROM public.{table} WHERE job_id=%s",
                                               (job["job_id"],)).fetchone()[column]
                    connection.execute(f"UPDATE public.{table} SET {column}=%s WHERE job_id=%s", (value, job["job_id"]))
                before = self.fixture.snapshot()
                with self.assertRaisesRegex(self.JobStoreError, "^cover_history_invalid$"):
                    self.history()
                self.assertEqual(self.fixture.snapshot(), before)
                with self.fixture.connect() as connection:
                    connection.execute(f"UPDATE public.{table} SET {column}=%s WHERE job_id=%s", (prior, job["job_id"]))

    def test_history_limit_fails_closed_before_returning_partial_results(self):
        job, = self.seed(["https://one-piece.com/news/82812/index.html"])
        self.finish(job)
        with self.fixture.connect() as connection:
            connection.execute(
                """INSERT INTO public.content_generation_attempt
                   (attempt_id,job_id,attempt_no,mode,claim_token,execution_status,outcome,producer_status,
                    artifact_path,result_sha256,finished_at)
                   SELECT md5(j.job_id::text || ':attempt:' || n)::uuid,j.job_id,n+1,'recovery',
                          md5(j.job_id::text || ':claim:' || n)::uuid,'succeeded',j.outcome,j.producer_status,
                          j.artifact_path,j.result_sha256,CURRENT_TIMESTAMP
                   FROM public.content_generation_job j CROSS JOIN generate_series(1,10000) n WHERE job_id=%s""",
                (job["job_id"],))
        with self.assertRaisesRegex(self.JobStoreError, "^cover_history_invalid$"):
            self.history()

    def test_history_errors_are_manual_prelaunch_errors_and_never_auto_retries(self):
        storage = importlib.import_module("menu_worker.job_store")
        for code in ("cover_history_invalid", "cover_history_unavailable"):
            self.assertIn(code, storage.PRELAUNCH_ERRORS)
            self.assertNotIn(code, storage.AUTO_RETRY_PRELAUNCH_ERRORS)

    def test_preserved_source_verifies_with_existing_sealed_result_reader(self):
        if importlib.util.find_spec("PIL") is None:
            self.skipTest("Run artifact verification in the generation image")
        from menu_worker import real_generation
        from menu_worker.job_artifacts import artifact_directory

        job, = self.seed(["https://one-piece.com/news/82812/index.html/?ref=original"])
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            folder = artifact_directory(root, job, create=True)
            result = {"status": "needs_crawler_update", "expected_work_request": real_generation._request(job)}
            (folder / "launch.json").write_text(json.dumps(real_generation._identity(job)), encoding="utf-8")
            (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
            (folder / "exit.json").write_text('{"returncode": 0}', encoding="utf-8")
            evidence = real_generation.validate_real_artifacts(root, job)
            self.finish(job, **evidence)
            before = self.fixture.snapshot()
            source, = self.history()
            self.assertEqual(real_generation.read_sealed_staging_result(root, source), result)
            self.assertEqual(self.fixture.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
