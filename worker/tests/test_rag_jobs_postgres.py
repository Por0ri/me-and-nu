"""Opt-in isolated PG tests: no broker, provider calls or service DB connection.

Run alongside migration tests using CONTENT_JOB_MIGRATION_TEST_ADMIN_URL on an
isolated jobs-db service, with `python -m unittest discover -s tests -p test_rag_jobs_postgres.py`.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory
from uuid import uuid4

import test_jobs_migration_postgres as fixture_module
from test_rag_contract import request, receipt
from menu_worker.job_store import JobConflict, MIGRATIONS
from menu_worker.rag_contract import RagContractError
from menu_worker.rag_store import RagStore
from menu_worker.rag_tasks import execute_delivery, read_sealed_result

FIXED_NOW = datetime.fromisoformat("2026-11-26T12:00:00+09:00").timestamp()


@unittest.skipUnless(os.environ.get(fixture_module.ADMIN_ENV), "Requires the isolated jobs-db migration fixture")
class RagPostgresTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.JobSchemaMigrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = RagStore(self.fixture.dsn, expected_database=fixture_module.MIGRATION_DB)
        self.store.initialize()

    def submit(self):
        item = request(work_id="gw_" + uuid4().hex)
        admission = receipt(item)
        return self.store.submit(item, admission), item, admission

    def test_concurrent_replay_and_different_reservation_cannot_create_duplicate(self):
        item = request()
        admission = receipt(item)
        def submit():
            return RagStore(self.fixture.dsn, expected_database=fixture_module.MIGRATION_DB).submit(item, admission)
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda _: submit(), range(4)))
        self.assertEqual(sum(row["created"] for row in rows), 1)
        self.assertEqual(len({row["job_id"] for row in rows}), 1)
        with self.assertRaisesRegex(JobConflict, "rag_candidate_exists"):
            self.store.submit(item, receipt(item))
        with self.assertRaisesRegex(JobConflict, "rag_input_conflict"):
            self.store.submit(request(query="changed"), receipt(request(query="changed")))
        other = request(work_id="gw_other")
        with self.assertRaisesRegex(JobConflict, "rag_reservation_conflict"):
            self.store.submit(other, receipt(other, reservation_id=admission["reservation_id"]))

    def test_claim_launch_and_unknown_usage_preserve_one_attempt(self):
        job, item, admission = self.submit()
        claimed = self.store.claim(job["job_id"])
        self.assertIsNone(self.store.claim(job["job_id"]))
        with self.assertRaisesRegex(RagContractError, "rag_generation_guard_required"):
            self.store.mark_generation_started(job["job_id"], claimed["claim_token"], None)
        with self.store.generation_guard(exclude_job_id=job["job_id"]) as guard:
            self.assertIsNotNone(guard)
            self.assertTrue(self.store.mark_generation_started(job["job_id"], claimed["claim_token"], guard))
            self.assertFalse(self.store.mark_generation_started(job["job_id"], claimed["claim_token"], guard))
            self.assertTrue(self.store.renew_lease(job["job_id"], claimed["claim_token"]))
            usage = [{"stage": "generation", "estimated_cost_usd": None, "total_tokens": 100}]
            self.assertTrue(self.store.complete(job["job_id"], claimed["claim_token"],
                execution_status="succeeded", outcome="held", producer_status="waiting_for_evidence",
                result_sha256="c" * 64,
                artifact_path=f"rag/jobs/{job['job_id']}/attempts/{claimed['generation_attempt_id']}/manifest.json",
                usage_records=usage, cost_complete=True))
        after = self.store.get(job["job_id"])
        self.assertFalse(after["cost_complete"])
        self.assertEqual(after["usage_records"], usage)
        self.assertEqual(after["attempts"][0]["usage_records"], usage)
        self.assertEqual(after["admission_receipt"], admission)
        self.assertEqual(after["request"], item)
        self.assertIsNone(self.store.claim(job["job_id"]))
        self.assertFalse(self.store.complete(job["job_id"], claimed["claim_token"], execution_status="failed"))
        self.assertFalse(self.store.submit(item, admission)["created"])

    def test_period_lock_excludes_rag_and_rag_lock_excludes_period(self):
        with self.store.jobs.generation_history_guard() as period:
            self.assertIsNotNone(period)
            with self.store.generation_guard() as rag:
                self.assertIsNone(rag)
        with self.store.generation_guard() as rag:
            self.assertIsNotNone(rag)
            with self.store.jobs.generation_history_guard() as period:
                self.assertIsNone(period)

    def test_active_rag_launch_fences_period_even_after_lock_connection_closes(self):
        job, _, _ = self.submit()
        claimed = self.store.claim(job["job_id"])
        with self.store.generation_guard(exclude_job_id=job["job_id"]) as guard:
            self.assertTrue(self.store.mark_generation_started(job["job_id"], claimed["claim_token"], guard))
        self.assertTrue(self.store.jobs.generation_history_busy(str(uuid4())))
        with self.store.generation_guard(exclude_job_id=str(uuid4())) as other:
            self.assertIsNone(other)
        with self.fixture.connect() as connection:
            connection.execute("UPDATE content_rag_generation_job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                               (job["job_id"],))
        self.assertFalse(self.store.jobs.generation_history_busy(str(uuid4())))

    def test_active_period_launch_fences_rag_after_lock_connection_closes(self):
        job_id = str(uuid4())
        with self.fixture.connect() as connection:
            connection.execute("""INSERT INTO content_generation_job
                (job_id,idempotency_key,source_id,source_url,canonical_url,content_type,input_sha256,job_kind,menu_work_id)
                VALUES (%s,%s,'one_piece_official',%s,%s,'goods',%s,'anime_generate',8)""",
                (job_id, 'period-' + job_id, "https://one-piece.com/news/83501/index.html",
                 "https://one-piece.com/news/83501/index.html", "e" * 64))
        claimed = self.store.jobs.claim(job_id, expected_kind="anime_generate")
        with self.store.jobs.generation_history_guard() as period:
            self.assertIsNotNone(period)
            self.assertTrue(self.store.jobs.mark_generation_started(job_id, claimed["claim_token"], expected_kind="anime_generate"))
        with self.store.generation_guard(exclude_job_id=str(uuid4())) as rag:
            self.assertIsNone(rag)

    def test_closed_guard_and_expired_started_claim_never_relaunch(self):
        job, _, _ = self.submit()
        claimed = self.store.claim(job["job_id"])
        with self.store.generation_guard(exclude_job_id=job["job_id"]) as guard:
            self.assertTrue(self.store.mark_generation_started(job["job_id"], claimed["claim_token"], guard))
        with self.assertRaisesRegex(RagContractError, "rag_generation_guard_required"):
            self.store.mark_generation_started(job["job_id"], claimed["claim_token"], guard)
        before = self.store.get(job["job_id"])
        with self.fixture.connect() as connection:
            connection.execute("UPDATE content_rag_generation_job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                               (job["job_id"],))
        self.assertEqual(self.store.expire_claims(), 1)
        after = self.store.get(job["job_id"])
        self.assertEqual(after["execution_status"], "needs_attention")
        self.assertEqual(after["generation_started_at"], before["generation_started_at"])
        self.assertEqual(after["generation_attempt_id"], before["generation_attempt_id"])
        self.assertIsNone(after["usage_records"])
        self.assertFalse(after["cost_complete"])
        self.assertIsNone(self.store.claim(job["job_id"]))

    def test_missing_receipt_has_no_database_side_effect(self):
        with self.assertRaises(RagContractError):
            self.store.submit(request(), {})
        with self.fixture.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) AS n FROM content_rag_generation_job").fetchone()["n"], 0)

    def test_changed_stored_input_is_blocked_before_claim(self):
        job, _, _ = self.submit()
        with self.fixture.connect() as connection:
            connection.execute("UPDATE content_rag_generation_job SET request=jsonb_set(request,'{query}',to_jsonb('changed'::text)) WHERE job_id=%s",
                               (job["job_id"],))
        with self.assertRaises(RagContractError):
            self.store.claim(job["job_id"])
        after = self.store.get(job["job_id"])
        self.assertEqual(after["execution_status"], "queued")
        self.assertEqual(after["attempts"], [])

    @staticmethod
    def native_result(job):
        raw = job["request"]
        return {"producer": "ani_rag", "schema_version": 1, "simulated": True, "review_mode": "offline_fixture",
            "job_id": job["job_id"], "generation_attempt_id": job["generation_attempt_id"],
            "namespace": raw["namespace"], "work_id": raw["work_id"],
            "request": {"work_id": raw["work_id"], "menu_work_ids": [raw["menu_work_id"]],
                "content_type": raw["content_type"], "query": raw["query"],
                "seed_evidence_ids": [row["document_id"] for row in raw["seed_evidence"]]},
            "source_ids": raw["allowed_source_ids"], "outcome": "held", "producer_status": "waiting_for_evidence",
            "usage_records": [], "cost_accounting": {"complete": True}}

    def test_delivery_is_disabled_by_default_then_seals_once_without_provider_calls(self):
        job, _, _ = self.submit()
        calls = []
        def produce(claimed, pulse):
            calls.append(claimed["job_id"])
            pulse()
            return self.native_result(claimed)
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            before = self.store.get(job["job_id"])
            self.assertEqual(execute_delivery(self.store, root, job["job_id"], run_native=produce,
                             preflight=lambda _: None)["reason"], "rag_generation_disabled")
            self.assertEqual(self.store.get(job["job_id"]), before)
            self.assertTrue(execute_delivery(self.store, root, job["job_id"], run_native=produce,
                            preflight=lambda _: None, enabled=True, now=lambda: FIXED_NOW)["processed"])
            after = self.store.get(job["job_id"])
            self.assertEqual(read_sealed_result(root, after)["outcome"], "held")
            self.assertFalse(execute_delivery(self.store, root, job["job_id"], run_native=produce,
                             preflight=lambda _: None, enabled=True, now=lambda: FIXED_NOW)["processed"])
            self.assertEqual(calls, [job["job_id"]])
            result = root / after["artifact_path"]
            result.with_name("result.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RagContractError, "rag_artifact_changed"):
                read_sealed_result(root, after)

    def test_preflight_failure_never_starts_producer(self):
        job, _, _ = self.submit()
        def preflight(_):
            raise ValueError("seed hash mismatch")
        with TemporaryDirectory() as temporary:
            result = execute_delivery(self.store, Path(temporary).resolve(), job["job_id"],
                run_native=lambda *_: self.fail("Provider must not run"), preflight=preflight, enabled=True,
                now=lambda: FIXED_NOW)
        self.assertEqual(result["execution_status"], "failed")
        after = self.store.get(job["job_id"])
        self.assertIsNone(after["generation_started_at"])
        self.assertTrue(after["cost_complete"])

    def test_offline_pass_is_blocked_but_returned_usage_is_preserved(self):
        job, _, _ = self.submit()
        usage = [{"stage": "generation", "estimated_cost_usd": None, "total_tokens": 100}]
        def produce(claimed, pulse):
            return {**self.native_result(claimed), "outcome": "ready_for_staging", "usage_records": usage}
        with TemporaryDirectory() as temporary:
            result = execute_delivery(self.store, Path(temporary).resolve(), job["job_id"],
                run_native=produce, preflight=lambda _: None, enabled=True, now=lambda: FIXED_NOW)
        self.assertEqual(result["execution_status"], "needs_attention")
        after = self.store.get(job["job_id"])
        self.assertEqual(after["usage_records"], usage)
        self.assertFalse(after["cost_complete"])
        self.assertIsNone(after["outcome"])
        self.assertIsNone(self.store.claim(job["job_id"]))

    def test_delayed_delivery_cannot_spend_yesterdays_or_late_nights_reservation(self):
        for stamp, code in (("2026-11-27T00:00:00+09:00", "rag_admission_date_inactive"),
                            ("2026-11-26T23:35:01+09:00", "rag_admission_window_closed")):
            with self.subTest(stamp=stamp), TemporaryDirectory() as temporary:
                job, _, admission = self.submit()
                execute_delivery(self.store, Path(temporary).resolve(), job["job_id"],
                    run_native=lambda *_: self.fail("Provider must not run"),
                    preflight=lambda *_: self.fail("Expired admission must stop before producer preflight"),
                    enabled=True, now=lambda: datetime.fromisoformat(stamp).timestamp())
                after = self.store.get(job["job_id"])
                self.assertEqual(after["execution_status"], "failed")
                self.assertEqual(after["error_code"], code)
                self.assertEqual(after["attempts"][0]["error_code"], code)
                self.assertIsNone(after["generation_started_at"])
                self.assertEqual(after["usage_records"], [])
                self.assertTrue(after["cost_complete"])
                self.assertEqual(after["admission_receipt"], admission)

    def test_preflight_crossing_cutoff_is_rechecked_before_launch_marker(self):
        job, _, _ = self.submit()
        clock = [datetime.fromisoformat("2026-11-26T23:34:59+09:00").timestamp()]
        def preflight(_):
            clock[0] += 2
        with TemporaryDirectory() as temporary:
            execute_delivery(self.store, Path(temporary).resolve(), job["job_id"],
                run_native=lambda *_: self.fail("Provider must not run"), preflight=preflight,
                enabled=True, now=lambda: clock[0])
        after = self.store.get(job["job_id"])
        self.assertEqual(after["error_code"], "rag_admission_window_closed")
        self.assertIsNone(after["generation_started_at"])
        self.assertTrue(after["cost_complete"])

    def test_exact_timeout_room_is_admitted_and_old_started_attempt_is_never_restarted(self):
        job, _, _ = self.submit()
        with TemporaryDirectory() as temporary:
            report = execute_delivery(self.store, Path(temporary).resolve(), job["job_id"],
                run_native=lambda claimed, pulse: self.native_result(claimed), preflight=lambda _: None,
                enabled=True, now=lambda: datetime.fromisoformat("2026-11-26T23:35:00+09:00").timestamp())
            self.assertEqual(report["execution_status"], "succeeded")
        old, _, _ = self.submit()
        claimed = self.store.claim(old["job_id"])
        with self.store.generation_guard(exclude_job_id=old["job_id"]) as guard:
            self.assertTrue(self.store.mark_generation_started(old["job_id"], claimed["claim_token"], guard))
        before = self.store.get(old["job_id"])
        with TemporaryDirectory() as temporary:
            report = execute_delivery(self.store, Path(temporary).resolve(), old["job_id"],
                run_native=lambda *_: self.fail("Never regenerate old attempt"),
                preflight=lambda *_: self.fail("Never reenter old attempt"), enabled=True,
                now=lambda: datetime.fromisoformat("2026-11-27T12:00:00+09:00").timestamp())
        self.assertFalse(report["processed"])
        self.assertEqual(self.store.get(old["job_id"]), before)


@unittest.skipUnless(os.environ.get(fixture_module.ADMIN_ENV), "Requires the isolated jobs-db migration fixture")
class RagMigrationTests(unittest.TestCase):
    def test_v7_rows_are_preserved_and_repeated_v8_initialization_is_noop(self):
        fixture = fixture_module.JobSchemaMigrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        migrations = Path(__file__).resolve().parents[1] / "menu_worker/migrations"
        with fixture.connect() as connection:
            for filename in MIGRATIONS[:7]:
                connection.execute((migrations / filename).read_text(encoding="utf-8"), prepare=False)
        original = fixture.store().submit({"idempotency_key": "old-v7", "source_id": "fixture",
            "source_url": "https://example.invalid/success/v7", "content_type": "news"})
        before = fixture.store().get(original["job_id"])
        store = RagStore(fixture.dsn, expected_database=fixture_module.MIGRATION_DB)
        self.assertEqual(store.initialize()["version"], 8)
        self.assertEqual(fixture.store().get(original["job_id"]), before)
        self.assertFalse(store.initialize()["created"])
        self.assertEqual(fixture.store().get(original["job_id"]), before)


if __name__ == "__main__":
    unittest.main()
