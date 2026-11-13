"""Opt-in job-store integration tests against one explicitly named test database.

No service database is accepted. Each test owns a unique candidate/key namespace
and removes only its own jobs. Run separately from other lease-recovery tests.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from io import StringIO
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

    def anime_candidate(self, **overrides):
        # Each test gets a unique registry-compatible path. No request is made.
        article_id = int(self.namespace.removeprefix("pgtest-")[:12], 16)
        return {
            "idempotency_key": f"{self.namespace}-anime",
            "source_id": "one_piece_official",
            "source_url": f"https://one-piece.com/news/{article_id}/index.html",
            "content_type": "goods",
            "menu_work_id": 8,
            **overrides,
        }

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
        failed = self.store.get(job_id)
        self.assertEqual(failed["execution_status"], "queued")
        self.assertEqual(failed["dispatch_count"], 0)
        self.assertIsNone(failed["last_dispatched_at"])

        successful_publish = Mock(return_value="unit-dispatch-task")
        report = dispatcher(self.store, publish=successful_publish)
        self.assertGreaterEqual(report["published"], 1)
        successful_publish.assert_any_call(job_id)
        sent = self.store.get(job_id)
        self.assertEqual(sent["execution_status"], "queued")
        self.assertEqual(sent["dispatch_count"], 1)
        self.assertEqual(sent["last_task_id"], "unit-dispatch-task")

    def test_backlogged_delivery_is_not_republished_and_lost_receipt_can_be_requeued_once(self):
        dispatcher = importlib.import_module("menu_worker.jobs").dispatch_once
        job_id = self.job_id(self.store.submit(self.candidate()))
        first = Mock(return_value="first-delivery")
        self.assertTrue(self.store.publish_pending(job_id, first))
        first.assert_called_once_with(job_id)
        with self.connection() as connection:
            connection.execute(
                "UPDATE content_generation_job SET last_dispatched_at=clock_timestamp() - interval '1 day' "
                "WHERE job_id=%s", (job_id,),
            )
        resend = Mock(return_value="second-delivery")
        dispatcher(self.store, publish=resend)
        self.assertNotIn(job_id, [call.args[0] for call in resend.call_args_list])
        self.assertFalse(self.store.publish_pending(job_id, resend))
        self.assertEqual(self.store.get(job_id)["dispatch_count"], 1)

        self.assertFalse(self.store.requeue_delivery(job_id, "first-delivery", expected_kind="anime"))
        self.assertFalse(self.store.requeue_delivery(job_id, "stale-receipt"))
        self.assertCountEqual(self.parallel(lambda store: store.requeue_delivery(job_id, "first-delivery")),
                              [True, False])
        self.assertTrue(self.store.publish_pending(job_id, resend))
        self.assertFalse(self.store.requeue_delivery(job_id, "first-delivery"))
        current = self.store.get(job_id)
        self.assertEqual((current["dispatch_count"], current["last_task_id"]), (2, "second-delivery"))
        self.assertIsNotNone(self.store.claim(job_id, "second-delivery"))
        self.assertFalse(self.store.requeue_delivery(job_id, "second-delivery"))
        self.assertIsNone(self.store.claim(job_id, "first-delivery"))

    def test_overlapping_dispatchers_send_one_message(self):
        job_id = self.job_id(self.store.submit(self.candidate()))
        sending, release = threading.Event(), threading.Event()
        worker_ready = threading.Event()

        def publish(candidate_id):
            self.assertEqual(candidate_id, job_id)
            sending.set()
            self.assertTrue(release.wait(timeout=10))
            return "only-delivery"

        def receive():
            worker_ready.set()
            return self.new_store().claim(job_id, "only-delivery")

        competing = Mock()
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.store.publish_pending, job_id, publish)
            try:
                self.assertTrue(sending.wait(timeout=10))
                self.assertFalse(self.new_store().publish_pending(job_id, competing))
                claim = executor.submit(receive)
                self.assertTrue(worker_ready.wait(timeout=10))
            finally:
                release.set()
            self.assertTrue(first.result(timeout=10))
            self.assertEqual(claim.result(timeout=10)["last_task_id"], "only-delivery")
        competing.assert_not_called()
        self.assertEqual(self.store.get(job_id)["dispatch_count"], 1)

    def test_ambiguous_broker_acknowledgement_retries_but_only_one_delivery_can_claim(self):
        job_id = self.job_id(self.store.submit(self.candidate()))
        deliveries = []

        def lost_ack(candidate_id):
            deliveries.append((candidate_id, "ack-lost"))
            raise ConnectionError("connection closed after broker acceptance")

        with self.assertRaises(ConnectionError):
            self.store.publish_pending(job_id, lost_ack)

        def acknowledged(candidate_id):
            deliveries.append((candidate_id, "ack-received"))
            return "ack-received"

        self.assertTrue(self.store.publish_pending(job_id, acknowledged))
        self.assertEqual(deliveries, [(job_id, "ack-lost"), (job_id, "ack-received")])
        self.assertIsNotNone(self.store.claim(*deliveries[0]))
        self.assertIsNone(self.store.claim(*deliveries[1]))
        self.assertEqual(self.store.get(job_id)["attempt_count"], 1)

    def test_live_lease_stays_claimed_but_expired_pre_generation_job_requeues(self):
        job_id = self.job_id(self.store.submit(self.candidate()))
        self.assertTrue(self.store.publish_pending(job_id, Mock(return_value="original")))
        claim = self.store.claim(job_id, "original", lease_seconds=60)
        self.store.recover_expired()
        self.assertEqual(self.store.get(job_id)["execution_status"], "running")
        self.assertIsNone(self.store.claim(job_id, lease_seconds=60))

        self.expire(job_id)
        report = self.store.recover_expired()
        self.assertGreaterEqual(report["queued"], 1)
        self.assertEqual(self.store.get(job_id)["execution_status"], "queued")
        self.assertTrue(self.store.publish_pending(job_id, Mock(return_value="replacement")))
        replacement = self.store.claim(job_id, "replacement", lease_seconds=60)
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
            self.store.submit(self.anime_candidate(idempotency_key=request.idempotency_key), job_kind="anime")
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
        anime_id = self.job_id(self.store.submit(self.anime_candidate(), job_kind="anime"))

        for kind, own, other in (("fake", fake_id, anime_id), ("anime", anime_id, fake_id)):
            with self.subTest(dispatch_kind=kind):
                pending = {self.job_id(row) for row in self.store.list_pending(expected_kind=kind)}
                self.assertIn(own, pending)
                self.assertNotIn(other, pending)
                wrong_queue = Mock()
                self.assertFalse(self.store.publish_pending(other, wrong_queue, expected_kind=kind))
                wrong_queue.assert_not_called()
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

    def test_anime_source_url_work_and_hash_survive_round_trip(self):
        request = self.anime_candidate()
        row = self.store.submit(request, job_kind="anime")
        self.assertTrue(row["created"])
        saved = self.store.get(row["job_id"])
        for field in ("source_id", "source_url", "menu_work_id", "content_type"):
            self.assertEqual(saved[field], request[field])
        self.assertEqual(saved["canonical_url"], request["source_url"])
        self.assertEqual(saved["job_kind"], "anime")
        repeated = self.store.submit({**request, "source_url": request["source_url"].replace("https:", "HTTPS:")},
                                     job_kind="anime")
        self.assertFalse(repeated["created"])
        self.assertEqual(repeated["job_id"], row["job_id"])
        self.assertEqual(repeated["input_sha256"], saved["input_sha256"])

    def test_article_query_directly_after_registered_root_host(self):
        raw_url = f"https://www.natsume-anime.jp?p={uuid.uuid4().int}"
        request = self.anime_candidate(source_id="natsume_official", source_url=raw_url,
                                       content_type="news", menu_work_id=377)
        job = self.store.submit(request, job_kind="anime")
        self.assertEqual(job["source_url"], raw_url)
        self.assertEqual(job["canonical_url"], raw_url.replace("?p=", "/?p="))

    def test_invalid_anime_inputs_leave_no_registered_job(self):
        cases = (
            {"source_id": "fixture", "source_url": "https://example.invalid/success/retired"},
            {"source_id": "not_a_registered_source"},
            {"source_url": "https://example.org/news/82812/index.html"},
            {"menu_work_id": None}, {"menu_work_id": 0}, {"menu_work_id": True}, {"menu_work_id": 2 ** 63},
        )
        for invalid in cases:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.submit(self.anime_candidate(**invalid), job_kind="anime")
        with self.connection() as connection:
            count = connection.execute(
                "SELECT count(*) FROM content_generation_job WHERE idempotency_key LIKE %s",
                (self.namespace + "%",),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_sql_insert_rejects_new_legacy_anime_fixture_even_when_terminal(self):
        with self.assertRaises(self.psycopg.errors.CheckViolation):
            with self.connection() as connection:
                connection.execute(
                    """INSERT INTO public.content_generation_job
                       (job_id,idempotency_key,source_id,source_url,canonical_url,content_type,
                        input_sha256,job_kind,execution_status)
                       VALUES (%s,%s,'fixture',%s,%s,'news',%s,'anime','cancelled')""",
                    (uuid.uuid4(), self.namespace, "https://example.invalid/success/retired",
                     "https://example.invalid/success/retired", "f" * 64),
                )

    def test_tampered_anime_input_fails_before_generation_or_artifacts(self):
        runner = importlib.import_module("menu_worker.job_runner")
        row = self.store.submit(self.anime_candidate(), job_kind="anime")
        with self.connection() as connection:
            connection.execute("UPDATE public.content_generation_job SET input_sha256=%s WHERE job_id=%s",
                               ("f" * 64, row["job_id"]))
        with TemporaryDirectory(prefix="menu-job-pgtest-") as temporary:
            root = Path(temporary).resolve()
            runner.run_job(self.store, root, row["job_id"], task_id="unit-tampered", expected_kind="anime")
            self.assertEqual(list(root.iterdir()), [])
        job = self.store.get(row["job_id"])
        self.assertEqual(job["execution_status"], "failed")
        self.assertEqual(job["error_code"], "input_identity_mismatch")
        self.assertIsNone(job["generation_started_at"])
        self.assertIsNone(job["attempts"][0]["generation_started_at"])

    def test_concurrent_generation_submissions_share_one_url_independently_of_probe(self):
        request = self.anime_candidate()
        probe = self.store.submit(request, job_kind="anime")

        barrier = threading.Barrier(2, timeout=10)

        def submit(suffix):
            store = self.new_store()
            attempt = {**request, "source_url": request["source_url"] + suffix,
                       "idempotency_key": f"{self.namespace}-{uuid.uuid4().hex}"}
            barrier.wait()
            try:
                row = store.submit(attempt, job_kind="anime_generate")
                return row["job_id"], row["created"], attempt
            except self.JobConflict as error:
                self.assertEqual(error.code, "candidate_exists")
                return error.existing_job_id, False, attempt

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, ("", "/?UTM_extra=campaign&fbclid=x&gclid=y#images")))
        self.assertCountEqual([created for _, created, _ in results], [True, False])
        self.assertEqual(len({job_id for job_id, _, _ in results}), 1)
        owner, _, admitted = next(result for result in results if result[1])
        self.assertNotEqual(owner, probe["job_id"])
        replay = self.store.submit({**admitted, "source_url": request["source_url"] + "/#retry"}, job_kind="anime_generate")
        self.assertFalse(replay["created"])
        self.assertEqual(replay["job_id"], owner)
        self.assertNotEqual(replay["input_sha256"], probe["input_sha256"])
        with self.assertRaises(self.JobConflict) as conflict:
            self.store.submit(request, job_kind="anime_generate")
        self.assertEqual(conflict.exception.code, "idempotency_conflict")
        self.assertEqual(conflict.exception.existing_job_id, probe["job_id"])

    def test_article_path_and_identifying_query_select_distinct_jobs(self):
        request = self.anime_candidate()
        base = request["source_url"]
        urls = (base + "?story=one", base + "?story=two", base.replace("index.html", "next.html") + "?story=one")
        jobs = [self.store.submit({**request, "source_url": url, "idempotency_key": f"{self.namespace}-{n}"},
                                  job_kind="anime_generate") for n, url in enumerate(urls)]
        self.assertEqual(len({row["job_id"] for row in jobs}), 3)
        self.assertEqual([row["canonical_url"] for row in jobs], list(urls))

    def test_discovery_slot_is_route_scoped_and_released_on_failure(self):
        second = self.new_store()
        with self.assertRaisesRegex(RuntimeError, "scan interrupted"):
            with self.store.discovery_slot("prtimes_rss", "news") as first:
                self.assertTrue(first)
                with second.discovery_slot("prtimes_rss", "news") as overlapping:
                    self.assertFalse(overlapping)
                with second.discovery_slot("prtimes_rss", "goods") as independent:
                    self.assertTrue(independent)
                raise RuntimeError("scan interrupted")
        with second.discovery_slot("prtimes_rss", "news") as retry:
            self.assertTrue(retry)

    def test_generation_reuses_historical_one_piece_ref_identity_without_mutating_sealed_input(self):
        from menu_worker.job_contract import job_input_hash

        request = self.anime_candidate()
        base = request["source_url"]
        legacy_url = base + "?ref=homepage&story=one"
        legacy = self.validate_input({**request, "source_url": legacy_url})
        kind = "anime_generate"
        old_hash = job_input_hash(legacy, kind, legacy_url)
        with self.connection() as conn:
            owner = conn.execute(
                """INSERT INTO content_generation_job
                   (job_id,idempotency_key,source_id,source_url,canonical_url,content_type,input_sha256,job_kind,menu_work_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING job_id""",
                (uuid.uuid4(), legacy.idempotency_key, legacy.source_id, legacy_url, legacy_url,
                 legacy.content_type, old_hash, kind, legacy.menu_work_id),
            ).fetchone()[0]
        clean = {**request, "source_url": base + "?story=one"}
        replay = self.store.submit(clean, job_kind=kind)
        self.assertEqual((replay["job_id"], replay["created"], replay["canonical_url"], replay["input_sha256"]),
                         (str(owner), False, legacy_url, old_hash))
        with self.assertRaises(self.JobConflict) as conflict:
            self.store.submit({**clean, "idempotency_key": self.namespace + "-auto"}, job_kind=kind)
        self.assertEqual((conflict.exception.code, conflict.exception.existing_job_id), ("candidate_exists", str(owner)))

        def simultaneous_submit(store):
            variant = uuid.uuid4().hex
            try:
                row = store.submit({**request, "source_url": base + "?ref=" + variant + "&story=two",
                                    "idempotency_key": self.namespace + variant}, job_kind=kind)
                return (row["job_id"], row["created"])
            except self.JobConflict as exc:
                self.assertEqual(exc.code, "candidate_exists")
                return (exc.existing_job_id, False)

        pair = self.parallel(simultaneous_submit)
        self.assertCountEqual([created for _, created in pair], [True, False])
        self.assertEqual(pair[0][0], pair[1][0])
        self.assertNotEqual(str(owner), pair[0][0])
        self.assertEqual(self.store.get(pair[0][0])["canonical_url"], base + "?story=two")

    def test_generation_kind_fences_and_preflight_failure_before_start(self):
        request = self.anime_candidate()
        probe = self.store.submit(request, job_kind="anime")
        kind = "anime_generate"
        job = self.store.submit({**request, "idempotency_key": self.namespace}, job_kind=kind)
        job_id = job["job_id"]
        own = {row["job_id"] for row in self.store.list_pending(expected_kind=kind)}
        self.assertIn(job_id, own)
        self.assertNotIn(probe["job_id"], own)
        for other in ("fake", "anime"):
            with self.subTest(other=other):
                self.assertNotIn(job_id, {row["job_id"] for row in self.store.list_pending(expected_kind=other)})
                wrong_queue = Mock()
                self.assertFalse(self.store.publish_pending(job_id, wrong_queue, expected_kind=other))
                wrong_queue.assert_not_called()
                self.assertFalse(self.store.cancel(job_id, expected_kind=other))
                self.assertIsNone(self.store.claim(job_id, expected_kind=other))
        self.assertFalse(self.store.cancel(probe["job_id"], expected_kind=kind))
        self.assertTrue(self.store.cancel(probe["job_id"], expected_kind="anime"))
        self.assertTrue(self.store.publish_pending(job_id, Mock(return_value="live-task"), expected_kind=kind))
        claim = self.store.claim(job_id, expected_kind=kind)
        token = claim["claim_token"]
        for other in ("fake", "anime"):
            with self.subTest(stolen_token=other):
                self.assertFalse(self.store.mark_generation_started(job_id, token, expected_kind=other))
                self.assertFalse(self.store.renew_lease(job_id, token, expected_kind=other))
                self.assertFalse(self.store.complete(job_id, token, expected_kind=other, execution_status="failed",
                                                     error_code="wrong_consumer"))
        self.assertTrue(self.store.complete(job_id, token, expected_kind=kind, execution_status="failed",
                                            error_code="preflight_failed"))
        result = self.store.get(job_id)
        self.assertEqual(result["execution_status"], "failed")
        self.assertIsNone(result["generation_started_at"])
        self.assertIsNone(result["attempts"][0]["generation_started_at"])
        self.assertIsNone(self.store.claim_recovery(job_id, expected_kind=kind))

    def test_generation_interruption_can_recover_only_original_attempt(self):
        kind = "anime_generate"
        job_id = self.store.submit(self.anime_candidate(), job_kind=kind)["job_id"]
        first = self.store.claim(job_id, expected_kind=kind)
        self.assertTrue(self.store.mark_generation_started(job_id, first["claim_token"], expected_kind=kind))
        self.assertFalse(self.store.cancel(job_id, expected_kind=kind))
        self.expire(job_id)
        self.assertFalse(self.store.complete(job_id, first["claim_token"], expected_kind=kind, execution_status="failed",
                                             error_code="agent_spawn_failed", agent_not_launched=True))
        for other in ("fake", "anime"):
            self.store.recover_expired(expected_kind=other)
            self.assertEqual(self.store.get(job_id)["execution_status"], "running")
        self.store.recover_expired(expected_kind=kind)
        self.assertEqual(self.store.get(job_id)["execution_status"], "needs_attention")
        self.assertFalse(self.store.retry_preflight(job_id, expected_kind=kind))
        self.assertNotIn(job_id, {row["job_id"] for row in self.store.list_pending(expected_kind=kind)})
        self.assertIsNone(self.store.claim(job_id, expected_kind=kind))
        self.assertIsNone(self.store.claim_recovery(job_id, expected_kind="anime"))
        recovery = self.store.claim_recovery(job_id, expected_kind=kind)
        self.assertEqual(recovery["generation_attempt_id"], first["generation_attempt_id"])
        self.assertFalse(self.store.mark_generation_started(job_id, recovery["claim_token"], expected_kind=kind))
        self.assertFalse(self.store.complete(job_id, recovery["claim_token"], expected_kind=kind, execution_status="failed",
                                             error_code="agent_spawn_failed", agent_not_launched=True))

    def test_generation_completion_preserves_original_producer_status(self):
        kind = "anime_generate"
        dispositions = (("ready_for_staging", "ready_for_feed"), ("held", "needs_crawler_update"),
                        ("skipped", "duplicate_skipped"), ("rejected", "rejected_archived"))
        for outcome, original in dispositions:
            with self.subTest(outcome=outcome):
                article = int(uuid.uuid4().hex[:12], 16)
                request = self.anime_candidate(idempotency_key=f"{self.namespace}-{outcome}",
                                              source_url=f"https://one-piece.com/news/{article}/index.html")
                job_id = self.store.submit(request, job_kind=kind)["job_id"]
                claim = self.store.claim(job_id, expected_kind=kind)
                self.assertTrue(self.store.mark_generation_started(job_id, claim["claim_token"], expected_kind=kind))
                path = f"jobs/{job_id}/attempts/{claim['generation_attempt_id']}/manifest.json"
                self.assertTrue(self.store.complete(job_id, claim["claim_token"], expected_kind=kind,
                                                    execution_status="succeeded", outcome=outcome,
                                                    producer_status=original, artifact_path=path, result_sha256="a" * 64))
                result = self.store.get(job_id)
                self.assertEqual((result["outcome"], result["producer_status"]), (outcome, original))
                attempt = result["attempts"][0]
                self.assertEqual((attempt["outcome"], attempt["producer_status"]), (outcome, original))

    @contextmanager
    def substituted_live_child(self, root, *, timeout=False):
        """Keep the real command builder and verifier. Substitute external process I/O."""
        runner = importlib.import_module("menu_worker.job_runner")
        adapter = importlib.import_module("menu_worker.real_generation")
        test_key = root / "test-secret"
        test_key.write_text("sk-manual-queue-test-never-sent", encoding="ascii")

        def launch(argv, *, cwd, env, **kwargs):
            self.assertIn("crawl-generate", argv)
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(env["ANI_MENU_DATABASE_BACKEND"], "sqlite")
            self.assertNotIn("CONTENT_JOB_DATABASE_URL", env)
            self.assertNotIn("DATABASE_URL", env)
            output = Path(argv[argv.index("--output") + 1])
            self.assertEqual(output, Path(cwd) / "result.json")
            if not timeout:
                binding = {
                    "source_id": argv[argv.index("--source") + 1],
                    "source_url": argv[argv.index("--url") + 1],
                    "content_type": argv[argv.index("--content-type") + 1],
                    "menu_work_id": int(argv[argv.index("--expected-work-id") + 1]),
                }
                # A producer hold exercises manifest finalization without making
                # any claim about real reviews, generated cards or provider usage.
                output.write_text(json.dumps({"status": "needs_crawler_update", "expected_work_request": binding,
                                              "qa_report": [{"code": "UNIT_CHILD_NO_PROVIDER"}]}), encoding="utf-8")
            return Mock(pid=101, returncode=0, poll=Mock(return_value=None if timeout else 0))

        with (patch.object(runner, "preflight_real"), patch.object(adapter, "KEY_FILE", test_key),
              patch.object(adapter.subprocess, "Popen", side_effect=launch) as spawn,
              patch.object(adapter, "_stop_group"),
              patch.object(adapter, "TIMEOUT_SECONDS", 0 if timeout else adapter.TIMEOUT_SECONDS)):
            yield spawn

    def test_manual_generation_cli_and_worker_with_substituted_child(self):
        cli = importlib.import_module("menu_worker.jobs")
        runner = importlib.import_module("menu_worker.job_runner")
        adapter = importlib.import_module("menu_worker.real_generation")
        request = self.anime_candidate()
        probe_id = self.store.submit(request, job_kind="anime")["job_id"]
        output = StringIO()
        with (patch.dict(os.environ, {"CONTENT_JOB_KIND": "anime_generate"}),
              patch.object(cli, "JobStore", return_value=self.store), redirect_stdout(output)):
            code = cli.main(["generate", "--idempotency-key", self.namespace + "-manual", "--source", request["source_id"],
                             "--url", request["source_url"].replace("index.html", "mobile.html") + "?utm_extra=x#top",
                             "--canonical-url", request["source_url"] + "/#content", "--content-type", request["content_type"],
                             "--menu-work-id", str(request["menu_work_id"])])
        self.assertEqual(code, 0)
        live_id = json.loads(output.getvalue())["job_id"]
        self.assertEqual(self.store.get(live_id)["source_url"], request["source_url"].replace("index.html", "mobile.html"))
        self.assertNotEqual(probe_id, live_id)
        with TemporaryDirectory(prefix="manual-queue-") as temporary:
            root = Path(temporary).resolve()
            with self.substituted_live_child(root) as spawn:
                runner.run_job(self.store, root, probe_id, task_id="unit-probe", expected_kind="anime")
                spawn.assert_not_called()
                report = runner.run_job(self.store, root, live_id, task_id="unit-live", expected_kind="anime_generate")
                self.assertTrue(report["processed"])
                replay = runner.run_job(self.store, root, live_id, task_id="unit-redelivery", expected_kind="anime_generate")
                self.assertFalse(replay["processed"])
                spawn.assert_called_once()
            live, probe = self.store.get(live_id), self.store.get(probe_id)
            self.assertEqual((probe["execution_status"], probe["outcome"]), ("succeeded", "routing_verified"))
            self.assertEqual((live["execution_status"], live["outcome"], live["producer_status"]),
                             ("succeeded", "held", "needs_crawler_update"))
            self.assertEqual(live["attempt_count"], 1)
            evidence = adapter.validate_real_artifacts(root, live)
            self.assertEqual(evidence["result_sha256"], live["result_sha256"])
            manifest = json.loads((root / live["artifact_path"]).read_bytes())
            self.assertEqual(manifest["generation_attempt_id"], live["generation_attempt_id"])
            self.assertEqual(manifest["request"]["source_url"], request["source_url"])

    def test_live_timeout_and_unfinished_recovery_never_relaunch_child(self):
        runner = importlib.import_module("menu_worker.job_runner")
        kind = "anime_generate"
        job_id = self.store.submit(self.anime_candidate(), job_kind=kind)["job_id"]
        with TemporaryDirectory(prefix="manual-timeout-") as temporary:
            root = Path(temporary).resolve()
            with self.substituted_live_child(root, timeout=True) as spawn:
                runner.run_job(self.store, root, job_id, task_id="unit-timeout", expected_kind=kind)
                stopped = self.store.get(job_id)
                self.assertEqual((stopped["execution_status"], stopped["error_code"]), ("needs_attention", "agent_timeout"))
                self.assertIsNotNone(stopped["generation_started_at"])
                self.assertFalse(self.store.retry_preflight(job_id, expected_kind=kind))
                replay = runner.run_job(self.store, root, job_id, task_id="unit-redelivered", expected_kind=kind)
                self.assertFalse(replay["processed"])
                runner.run_job(self.store, root, job_id, task_id="unit-reconcile", expected_kind=kind, recover=True)
                spawn.assert_called_once()
            final = self.store.get(job_id)
            self.assertEqual(final["execution_status"], "needs_attention")
            self.assertEqual(final["generation_attempt_id"], stopped["generation_attempt_id"])
            self.assertEqual([row["mode"] for row in final["attempts"]], ["generation", "recovery"])
            self.assertIsNone(final["artifact_path"])

    def test_live_artifacts_recover_after_completion_write_error_without_second_launch(self):
        runner = importlib.import_module("menu_worker.job_runner")
        kind = "anime_generate"
        job_id = self.store.submit(self.anime_candidate(), job_kind=kind)["job_id"]
        with TemporaryDirectory(prefix="manual-recovery-") as temporary:
            root = Path(temporary).resolve()
            with self.substituted_live_child(root) as spawn:
                with (patch.object(self.store, "complete", side_effect=RuntimeError("test completion write error")),
                      self.assertRaises(RuntimeError)):
                    runner.run_job(self.store, root, job_id, task_id="unit-uncommitted-result", expected_kind=kind)
                before = self.store.get(job_id)
                directory = root / "jobs" / job_id / "attempts" / before["generation_attempt_id"]
                content = (directory / "result.json").read_bytes()
                manifest = (directory / "manifest.json").read_bytes()
                self.expire(job_id)
                self.store.recover_expired(expected_kind=kind)
                self.assertEqual(self.store.get(job_id)["execution_status"], "needs_attention")
                runner.run_job(self.store, root, job_id, task_id="unit-restore", expected_kind=kind, recover=True)
                spawn.assert_called_once()
            final = self.store.get(job_id)
            self.assertEqual((final["execution_status"], final["outcome"]), ("succeeded", "held"))
            self.assertEqual(final["generation_attempt_id"], before["generation_attempt_id"])
            self.assertEqual((directory / "manifest.json").read_bytes(), manifest)
            self.assertEqual((directory / "result.json").read_bytes(), content)

    def test_setup_and_spawn_failures_allow_one_manual_retry_with_a_fresh_attempt(self):
        runner = importlib.import_module("menu_worker.job_runner")
        adapter = importlib.import_module("menu_worker.real_generation")
        kind = "anime_generate"
        job_id = self.store.submit(self.anime_candidate(), job_kind=kind)["job_id"]
        with TemporaryDirectory(prefix="manual-launch-") as temporary:
            root = Path(temporary).resolve()
            with self.substituted_live_child(root) as spawn:
                launch = spawn.side_effect
                with patch.object(adapter, "_atomic_write", side_effect=OSError("test setup error")):
                    runner.run_job(self.store, root, job_id, expected_kind=kind)
                spawn.assert_not_called()
                setup = self.store.get(job_id)
                self.assertEqual((setup["execution_status"], setup["error_code"]), ("failed", "agent_attempt_setup_failed"))
                self.assertIsNone(setup["generation_started_at"])
                self.assertTrue(self.store.retry_preflight(job_id, expected_kind=kind))

                spawn.side_effect = OSError("test spawn error")
                runner.run_job(self.store, root, job_id, expected_kind=kind)
                failed = self.store.get(job_id)
                self.assertEqual((failed["execution_status"], failed["error_code"]), ("failed", "agent_spawn_failed"))
                self.assertIsNone(failed["generation_started_at"])
                self.assertEqual([attempt["generation_started_at"] for attempt in failed["attempts"]], [None, None])
                self.assertFalse(runner.run_job(self.store, root, job_id, expected_kind=kind)["processed"])
                self.assertCountEqual(self.parallel(lambda store: store.retry_preflight(job_id, expected_kind=kind)),
                                      [True, False])
                spawn.side_effect = launch
                runner.run_job(self.store, root, job_id, expected_kind=kind)
                self.assertFalse(runner.run_job(self.store, root, job_id, expected_kind=kind)["processed"])
                self.assertEqual(spawn.call_count, 2)  # One failed spawn, one substituted producer.

            job = self.store.get(job_id)
            self.assertEqual((job["execution_status"], job["outcome"]), ("succeeded", "held"))
            self.assertEqual([a["error_code"] for a in job["attempts"]],
                             ["agent_attempt_setup_failed", "agent_spawn_failed", None])
            self.assertEqual(len({a["attempt_id"] for a in job["attempts"]}), 3)
            self.assertIsNotNone(job["attempts"][-1]["generation_started_at"])

    def test_preflight_retry_recognizes_legacy_generation_attempt_with_confirmed_spawn_failure(self):
        kind = "anime_generate"
        job_id = self.store.submit(self.anime_candidate(), job_kind=kind)["job_id"]
        legacy = self.store.claim(job_id, expected_kind=kind)
        self.assertTrue(self.store.mark_generation_started(job_id, legacy["claim_token"], expected_kind=kind))
        self.assertTrue(self.store.complete(job_id, legacy["claim_token"], expected_kind=kind,
                                            execution_status="needs_attention", error_code="agent_spawn_failed"))
        before = self.store.get(job_id)
        self.assertTrue(self.store.retry_preflight(job_id, expected_kind=kind))
        queued = self.store.get(job_id)
        self.assertEqual(queued["attempts"], before["attempts"])
        self.assertIsNone(queued["generation_started_at"])
        next_attempt = self.store.claim(job_id, expected_kind=kind)
        self.assertNotEqual(next_attempt["attempt_id"], legacy["attempt_id"])

    def test_preflight_retry_requeues_once_with_same_identity_and_full_attempt_history(self):
        kind = "anime_generate"
        job = self.store.submit(self.anime_candidate(), job_kind=kind)
        job_id = job["job_id"]
        self.assertTrue(self.store.publish_pending(job_id, Mock(return_value="initial-delivery"), expected_kind=kind))
        first = self.store.claim(job_id, task_id="initial-delivery", expected_kind=kind)
        self.assertTrue(self.store.complete(job_id, first["claim_token"], expected_kind=kind,
                                            execution_status="failed", error_code="invalid_agent_secret"))
        before = self.store.get(job_id)
        for wrong_role in ("fake", "anime"):
            self.assertFalse(self.store.retry_preflight(job_id, expected_kind=wrong_role))
        self.assertCountEqual(self.parallel(lambda store: store.retry_preflight(job_id, expected_kind=kind)),
                              [True, False])
        queued = self.store.get(job_id)
        self.assertEqual(queued["execution_status"], "queued")
        for field in ("job_id", "job_kind", "idempotency_key", "input_sha256", "created_at", "menu_work_id",
                      "source_url", "canonical_url", "attempt_count", "dispatch_count", "attempts"):
            self.assertEqual(queued[field], before[field], field)
        for field in ("current_attempt_id", "generation_attempt_id", "generation_started_at", "claim_token",
                      "lease_expires_at", "last_dispatched_at", "last_task_id", "error_code", "finished_at"):
            self.assertIsNone(queued[field], field)
        self.assertIn(job_id, {row["job_id"] for row in self.store.list_pending(expected_kind=kind)})
        second = self.store.claim(job_id, expected_kind=kind)
        self.assertEqual(second["attempt_count"], 2)
        self.assertNotEqual(second["attempt_id"], first["attempt_id"])
        self.assertFalse(self.store.mark_generation_started(job_id, first["claim_token"], expected_kind=kind))
        self.assertTrue(self.store.complete(job_id, second["claim_token"], expected_kind=kind,
                                            execution_status="failed", error_code="agent_runtime_unavailable"))
        self.assertTrue(self.store.retry_preflight(job_id, expected_kind=kind))
        history = self.store.get(job_id)["attempts"]
        self.assertEqual([row["error_code"] for row in history], ["invalid_agent_secret", "agent_runtime_unavailable"])

    def test_preflight_retry_refuses_started_ambiguous_other_error_or_other_kind(self):
        live = "anime_generate"
        cases = ((live, "failed", "invalid_agent_secret", True),
                 (live, "needs_attention", "agent_runtime_unavailable", False),
                 (live, "failed", "input_identity_mismatch", False),
                 ("anime", "failed", "invalid_agent_secret", False),
                 ("fake", "failed", "agent_runtime_unavailable", False))
        for index, (kind, status, code, started) in enumerate(cases):
            with self.subTest(kind=kind, status=status, code=code, started=started):
                if kind == "fake":
                    request = self.candidate()
                else:
                    article = int(uuid.uuid4().hex[:12], 16)
                    request = self.anime_candidate(idempotency_key=f"{self.namespace}-{index}",
                                                  source_url=f"https://one-piece.com/news/{article}/index.html")
                job_id = self.store.submit(request, job_kind=kind)["job_id"]
                claim = self.store.claim(job_id, expected_kind=kind)
                if started:
                    self.assertTrue(self.store.mark_generation_started(job_id, claim["claim_token"], expected_kind=kind))
                self.assertTrue(self.store.complete(job_id, claim["claim_token"], expected_kind=kind,
                                                    execution_status=status, error_code=code))
                before = self.store.get(job_id)
                self.assertFalse(self.store.retry_preflight(job_id, expected_kind=live))
                self.assertEqual(self.store.get(job_id), before)


if __name__ == "__main__":
    unittest.main()
