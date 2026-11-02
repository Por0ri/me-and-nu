"""Opt-in job-store integration tests against one explicitly named test database.

No service database is accepted. Each test owns a unique candidate/key namespace
and removes only its own jobs. Run separately from other lease-recovery tests.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import sys
import threading
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DATABASE = "menu_content_jobs_test"
TEST_DSN_ENV = "CONTENT_JOB_TEST_DATABASE_URL"


@unittest.skipUnless(os.environ.get(TEST_DSN_ENV), f"Set {TEST_DSN_ENV} for PostgreSQL job tests")
class JobStorePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import the driver and job modules only after the opt-in. Missing B1
        # dependencies must fail, rather than silently skip an explicitly run test.
        cls.psycopg = importlib.import_module("psycopg")
        conninfo = importlib.import_module("psycopg.conninfo")
        cls.dsn = os.environ[TEST_DSN_ENV]
        try:
            parameters = conninfo.conninfo_to_dict(cls.dsn)
        except Exception:
            raise RuntimeError("Invalid PostgreSQL test connection configuration") from None
        if parameters.get("dbname") != TEST_DATABASE:
            raise RuntimeError(f"Job tests require the dedicated {TEST_DATABASE} database")

        # Verify the server's actual database before initialize() can execute DDL.
        with cls.psycopg.connect(cls.dsn) as connection:
            actual = connection.execute("SELECT current_database()").fetchone()[0]
            if actual != TEST_DATABASE:
                raise RuntimeError("Connected database is not the dedicated job test database")

        contract = importlib.import_module("menu_worker.job_contract")
        storage = importlib.import_module("menu_worker.job_store")
        cls.validate_input = staticmethod(contract.validate_input)
        cls.JobConflict = storage.JobConflict
        cls.JobStore = storage.JobStore
        cls.store = cls.new_store()
        cls.store.initialize()

    @classmethod
    def new_store(cls):
        return cls.JobStore(cls.dsn, expected_database=TEST_DATABASE)

    def setUp(self):
        self.namespace = f"pgtest-{uuid.uuid4().hex}"

    @contextmanager
    def connection(self):
        with self.psycopg.connect(self.dsn) as connection:
            self.assertEqual(
                connection.execute("SELECT current_database()").fetchone()[0], TEST_DATABASE
            )
            yield connection

    def tearDown(self):
        with self.connection() as connection:
            connection.execute(
                "DELETE FROM content_generation_job WHERE idempotency_key LIKE %s",
                (self.namespace + "%",),
            )

    def candidate(self, **overrides):
        value = {
            "idempotency_key": f"{self.namespace}-one",
            "source_id": "fixture",
            "source_url": f"https://example.invalid/success/{self.namespace}/article",
            "content_type": "news",
        }
        value.update(overrides)
        return self.validate_input(value)

    @staticmethod
    def job_id(row):
        return str(row["job_id"])

    def expire(self, job_id):
        # Avoid timing-sensitive sleeps while still using PostgreSQL's clock.
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE content_generation_job "
                "SET lease_expires_at = clock_timestamp() - interval '1 second' "
                "WHERE job_id = %s AND idempotency_key LIKE %s",
                (job_id, self.namespace + "%"),
            )
            self.assertEqual(cursor.rowcount, 1)

    def complete(self, job_id, token):
        generation_attempt = self.store.get(job_id)["generation_attempt_id"]
        return self.store.complete(
            job_id,
            token,
            execution_status="succeeded",
            outcome="ready_for_staging",
            producer_status="ready_for_feed",
            artifact_path=f"jobs/{job_id}/attempts/{generation_attempt}/result.json",
            result_sha256="a" * 64,
        )

    def parallel(self, call):
        barrier = threading.Barrier(2, timeout=10)

        def invoke():
            store = self.new_store()
            barrier.wait()
            return call(store)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(invoke) for _ in range(2)]
            return [future.result(timeout=20) for future in futures]

    def test_simultaneous_duplicate_submit_creates_one_job(self):
        candidate = self.candidate()
        rows = self.parallel(lambda store: store.submit(candidate))
        self.assertEqual(sum(bool(row["created"]) for row in rows), 1)
        self.assertEqual(len({self.job_id(row) for row in rows}), 1)
        with self.connection() as connection:
            count = connection.execute(
                "SELECT count(*) FROM content_generation_job WHERE idempotency_key = %s",
                (candidate.idempotency_key,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_same_key_with_different_payload_is_a_conflict(self):
        first = self.store.submit(self.candidate())
        changed = self.candidate(source_url=f"https://example.invalid/success/{self.namespace}/different")
        with self.assertRaises(self.JobConflict) as caught:
            self.store.submit(changed)
        self.assertEqual(caught.exception.code, "idempotency_conflict")
        self.assertEqual(str(caught.exception.existing_job_id), self.job_id(first))

    def test_canonical_source_cannot_be_requeued_with_another_type(self):
        first = self.store.submit(self.candidate())
        changed = self.candidate(
            idempotency_key=f"{self.namespace}-two",
            source_url=f"https://example.invalid/success/{self.namespace}/article?utm_source=unit",
            content_type="event",
        )
        with self.assertRaises(self.JobConflict) as caught:
            self.store.submit(changed)
        self.assertEqual(caught.exception.code, "candidate_exists")
        self.assertEqual(str(caught.exception.existing_job_id), self.job_id(first))

    def test_simultaneous_claim_has_one_owner_and_one_attempt(self):
        job_id = self.job_id(self.store.submit(self.candidate()))
        claims = self.parallel(lambda store: store.claim(job_id, lease_seconds=60))
        owners = [row for row in claims if row is not None]
        self.assertEqual(len(owners), 1)
        self.assertTrue(owners[0]["claim_token"])
        self.assertTrue(owners[0]["attempt_id"])
        with self.connection() as connection:
            count = connection.execute(
                "SELECT count(*) FROM content_generation_attempt WHERE job_id = %s", (job_id,)
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_broker_failure_preserves_committed_pending_job(self):
        dispatcher = importlib.import_module("menu_worker.jobs").dispatch_once
        job_id = self.job_id(self.store.submit(self.candidate()))
        failed_publish = Mock(side_effect=RuntimeError("simulated broker outage"))
        report = dispatcher(self.store, publish=failed_publish)
        self.assertGreaterEqual(report["failed"], 1)
        failed_publish.assert_any_call(job_id)
        pending = {self.job_id(row) for row in self.store.list_pending()}
        self.assertIn(job_id, pending)
        self.assertEqual(self.store.get(job_id)["execution_status"], "queued")

        successful_publish = Mock(return_value="unit-dispatch-task")
        report = dispatcher(self.store, publish=successful_publish)
        self.assertGreaterEqual(report["published"], 1)
        successful_publish.assert_any_call(job_id)
        self.assertEqual(self.store.get(job_id)["execution_status"], "queued")

    def test_live_lease_stays_claimed_but_expired_pre_generation_job_requeues(self):
        job_id = self.job_id(self.store.submit(self.candidate()))
        claim = self.store.claim(job_id, lease_seconds=60)
        self.store.recover_expired()
        self.assertEqual(self.store.get(job_id)["execution_status"], "running")
        self.assertIsNone(self.store.claim(job_id, lease_seconds=60))

        self.expire(job_id)
        report = self.store.recover_expired()
        self.assertGreaterEqual(report["queued"], 1)
        self.assertEqual(self.store.get(job_id)["execution_status"], "queued")
        replacement = self.store.claim(job_id, lease_seconds=60)
        self.assertIsNotNone(replacement)
        self.assertNotEqual(str(replacement["claim_token"]), str(claim["claim_token"]))

    def test_expired_generation_requires_attention_and_cannot_autorun(self):
        job_id = self.job_id(self.store.submit(self.candidate()))
        claim = self.store.claim(job_id, lease_seconds=60)
        self.assertTrue(self.store.mark_generation_started(job_id, claim["claim_token"]))
        self.expire(job_id)
        report = self.store.recover_expired()
        self.assertGreaterEqual(report["needs_attention"], 1)
        self.assertEqual(self.store.get(job_id)["execution_status"], "needs_attention")
        self.assertNotIn(job_id, {self.job_id(row) for row in self.store.list_pending()})
        self.assertIsNone(self.store.claim(job_id, lease_seconds=60))

    def test_expired_or_replaced_token_cannot_mutate_or_finish_job(self):
        job_id = self.job_id(self.store.submit(self.candidate()))
        first = self.store.claim(job_id, lease_seconds=60)
        self.expire(job_id)
        self.assertFalse(self.store.mark_generation_started(job_id, first["claim_token"]))
        self.assertFalse(self.store.renew_lease(job_id, first["claim_token"], lease_seconds=60))
        self.assertFalse(self.complete(job_id, first["claim_token"]))

        self.store.recover_expired()
        current = self.store.claim(job_id, lease_seconds=60)
        self.assertTrue(self.store.mark_generation_started(job_id, current["claim_token"]))
        self.assertFalse(self.complete(job_id, first["claim_token"]))
        self.assertEqual(self.store.get(job_id)["execution_status"], "running")
        self.assertTrue(self.store.renew_lease(job_id, current["claim_token"], lease_seconds=60))
        self.assertTrue(self.complete(job_id, current["claim_token"]))
        self.assertEqual(self.store.get(job_id)["execution_status"], "succeeded")
        self.assertIsNone(self.store.claim(job_id, lease_seconds=60))

    def test_manual_recovery_keeps_original_generation_attempt(self):
        job_id = self.job_id(self.store.submit(self.candidate()))
        generation = self.store.claim(job_id, lease_seconds=60)
        self.assertTrue(self.store.mark_generation_started(job_id, generation["claim_token"]))
        self.expire(job_id)
        self.store.recover_expired()
        recovery = self.store.claim_recovery(job_id, lease_seconds=60)
        self.assertEqual(recovery["mode"], "recovery")
        self.assertEqual(recovery["generation_attempt_id"], generation["generation_attempt_id"])
        self.assertNotEqual(recovery["attempt_id"], generation["attempt_id"])
        self.assertFalse(self.store.mark_generation_started(job_id, recovery["claim_token"]))
        self.assertFalse(self.complete(job_id, generation["claim_token"]))
        self.assertTrue(self.complete(job_id, recovery["claim_token"]))

    def test_fake_runner_preserves_success_hold_skip_and_failure_outcomes(self):
        runner = importlib.import_module("menu_worker.job_runner")
        expected = {
            "success": ("succeeded", "ready_for_staging", "ready_for_feed"),
            "held": ("succeeded", "held", "held_for_quality_review"),
            "skipped": ("succeeded", "skipped", "duplicate_skipped"),
            "fail": ("failed", None, None),
        }
        with TemporaryDirectory(prefix="menu-job-pgtest-") as temporary:
            root = Path(temporary).resolve()
            for scenario, (status, outcome, producer_status) in expected.items():
                with self.subTest(scenario=scenario):
                    request = self.candidate(
                        idempotency_key=f"{self.namespace}-{scenario}",
                        source_url=f"https://example.invalid/{scenario}/{self.namespace}",
                    )
                    job_id = self.job_id(self.store.submit(request))
                    report = runner.run_job(self.store, root, job_id, task_id=f"unit-{scenario}")
                    self.assertTrue(report["processed"])
                    job = self.store.get(job_id)
                    self.assertEqual(job["execution_status"], status)
                    self.assertEqual(job["outcome"], outcome)
                    self.assertEqual(job["producer_status"], producer_status)
                    self.assertEqual(len(job["attempts"]), 1)
                    if scenario == "fail":
                        self.assertEqual(job["error_code"], "fake_generation_failed")
                        self.assertIsNone(job["artifact_path"])
                    else:
                        manifest = root / job["artifact_path"]
                        self.assertTrue(manifest.is_file())
                        result = json.loads((manifest.parent / "result.json").read_text())
                        self.assertEqual(result["llm_calls"], 0)
                        self.assertFalse(result["service_database_changed"])

    def test_duplicate_delivery_does_not_invoke_generator_again(self):
        runner = importlib.import_module("menu_worker.job_runner")
        job_id = self.job_id(self.store.submit(self.candidate()))
        with TemporaryDirectory(prefix="menu-job-pgtest-") as temporary:
            root = Path(temporary).resolve()
            with patch.object(runner, "generate_fake", wraps=runner.generate_fake) as generate:
                first = runner.run_job(self.store, root, job_id, task_id="unit-first")
                second = runner.run_job(self.store, root, job_id, task_id="unit-redelivered")
            self.assertTrue(first["processed"])
            self.assertFalse(second["processed"])
            self.assertEqual(generate.call_count, 1)
        self.assertEqual(len(self.store.get(job_id)["attempts"]), 1)

    def test_completed_artifacts_recover_after_database_write_failure_without_regeneration(self):
        runner = importlib.import_module("menu_worker.job_runner")
        job_id = self.job_id(self.store.submit(self.candidate()))
        with TemporaryDirectory(prefix="menu-job-pgtest-") as temporary:
            root = Path(temporary).resolve()
            with patch.object(self.store, "complete", side_effect=RuntimeError("simulated completion outage")):
                with self.assertRaises(RuntimeError):
                    runner.run_job(self.store, root, job_id, task_id="unit-db-outage")
            before = self.store.get(job_id)
            directory = root / "jobs" / job_id / "attempts" / before["generation_attempt_id"]
            manifest_bytes = (directory / "manifest.json").read_bytes()
            result_bytes = (directory / "result.json").read_bytes()
            self.assertEqual(before["execution_status"], "running")
            self.expire(job_id)
            self.store.recover_expired()
            with patch.object(runner, "generate_fake", side_effect=AssertionError("must not regenerate")) as generate:
                report = runner.run_job(self.store, root, job_id, task_id="unit-recover", recover=True)
                generate.assert_not_called()
            self.assertTrue(report["processed"])
            after = self.store.get(job_id)
            self.assertEqual(after["execution_status"], "succeeded")
            self.assertEqual(after["generation_attempt_id"], before["generation_attempt_id"])
            self.assertEqual(len(after["attempts"]), 2)
            self.assertEqual((directory / "manifest.json").read_bytes(), manifest_bytes)
            self.assertEqual((directory / "result.json").read_bytes(), result_bytes)

    def test_partial_or_corrupt_artifacts_stay_needs_attention_without_regeneration(self):
        runner = importlib.import_module("menu_worker.job_runner")
        artifacts = importlib.import_module("menu_worker.job_artifacts")
        with TemporaryDirectory(prefix="menu-job-pgtest-") as temporary:
            root = Path(temporary).resolve()
            for condition in ("partial", "corrupt_manifest"):
                with self.subTest(condition=condition):
                    job_id = self.job_id(self.store.submit(self.candidate(
                        idempotency_key=f"{self.namespace}-{condition}",
                        source_url=f"https://example.invalid/success/{self.namespace}/{condition}",
                    )))
                    claim = self.store.claim(job_id, lease_seconds=60)
                    self.assertTrue(self.store.mark_generation_started(job_id, claim["claim_token"]))
                    if condition == "partial":
                        directory = artifacts.artifact_directory(root, claim, create=True)
                        (directory / "result.json").write_text("{}")
                    else:
                        artifacts.generate_fake(root, claim)
                        directory = artifacts.artifact_directory(root, claim)
                        (directory / "manifest.json").write_text("{invalid-json")
                    self.expire(job_id)
                    self.store.recover_expired()
                    with patch.object(runner, "generate_fake", side_effect=AssertionError("must not regenerate")) as generate:
                        report = runner.run_job(self.store, root, job_id, task_id="unit-recover-invalid", recover=True)
                        generate.assert_not_called()
                    self.assertTrue(report["processed"])
                    job = self.store.get(job_id)
                    self.assertEqual(job["execution_status"], "needs_attention")
                    self.assertEqual(job["error_code"], "artifact_verification_required")
                    self.assertIsNone(self.store.claim(job_id, lease_seconds=60))

    def test_job_kind_is_immutable_and_part_of_idempotency(self):
        request = self.candidate()
        job = self.store.submit(request)
        job_id = self.job_id(job)
        self.assertEqual(job["job_kind"], "fake")
        with self.assertRaises(self.JobConflict) as conflict:
            self.store.submit(request, job_kind="anime")
        self.assertEqual(conflict.exception.code, "idempotency_conflict")
        self.assertEqual(str(conflict.exception.existing_job_id), job_id)
        # The database also protects old producers and non-CLI writers.
        with self.assertRaises(self.psycopg.errors.CheckViolation):
            with self.connection() as connection:
                connection.execute(
                    "UPDATE public.content_generation_job SET job_kind='anime' WHERE job_id=%s",
                    (job_id,),
                )
        self.assertEqual(self.store.get(job_id)["job_kind"], "fake")

    def test_dispatch_claim_completion_and_recovery_are_fenced_by_kind(self):
        fake_id = self.job_id(self.store.submit(self.candidate()))
        anime_id = self.job_id(self.store.submit(self.candidate(
            idempotency_key=f"{self.namespace}-anime",
            source_url=f"https://example.invalid/success/{self.namespace}/anime",
        ), job_kind="anime"))

        for kind, own, other in (("fake", fake_id, anime_id), ("anime", anime_id, fake_id)):
            with self.subTest(dispatch_kind=kind):
                pending = {self.job_id(row) for row in self.store.list_pending(expected_kind=kind)}
                self.assertIn(own, pending)
                self.assertNotIn(other, pending)
                self.assertFalse(self.store.record_dispatched(other, "incorrect-queue", expected_kind=kind))
                self.assertIsNone(self.store.claim(other, expected_kind=kind))
                self.assertEqual(self.store.get(other)["dispatch_count"], 0)

        fake = self.store.claim(fake_id)  # v1 callers continue to select fake jobs.
        anime = self.store.claim(anime_id, expected_kind="anime")
        for wrong_kind, claim in (("anime", fake), ("fake", anime)):
            with self.subTest(operation_kind=wrong_kind):
                job_id, token = claim["job_id"], claim["claim_token"]
                self.assertFalse(self.store.mark_generation_started(job_id, token, expected_kind=wrong_kind))
                self.assertFalse(self.store.renew_lease(job_id, token, expected_kind=wrong_kind))
                self.assertFalse(self.store.complete(
                    job_id, token, execution_status="failed", error_code="incorrect_consumer",
                    expected_kind=wrong_kind,
                ))
                job = self.store.get(job_id)
                self.assertEqual(job["execution_status"], "running")
                self.assertIsNone(job["generation_started_at"])

        self.assertTrue(self.store.mark_generation_started(anime_id, anime["claim_token"], expected_kind="anime"))
        self.expire(fake_id)
        self.expire(anime_id)
        self.store.recover_expired()
        self.assertEqual(self.store.get(fake_id)["execution_status"], "queued")
        self.assertEqual(self.store.get(anime_id)["execution_status"], "running")
        self.store.recover_expired(expected_kind="anime")
        self.assertEqual(self.store.get(anime_id)["execution_status"], "needs_attention")
        self.assertIsNone(self.store.claim_recovery(anime_id))
        recovered = self.store.claim_recovery(anime_id, expected_kind="anime")
        self.assertEqual(recovered["generation_attempt_id"], anime["generation_attempt_id"])


if __name__ == "__main__":
    unittest.main()
