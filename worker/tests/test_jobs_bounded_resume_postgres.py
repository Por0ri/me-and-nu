"""Bounded continuation against a new invocation-owned DB, with a fake producer.

The result reader substitutes sealed-artifact I/O; sealing itself is covered by
the existing real-generation tests. No broker, Agent or provider is contacted.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import test_jobs_migration_postgres as migration_tests
from menu_worker.bounded_generation import Controller, StopRun, audit_existing_jobs, database_lock, local_lock, write_json
from menu_worker.bounded_resume import activate_plan, build_plan, checkpoint_source, validate_successor


@unittest.skipUnless(os.environ.get(migration_tests.ADMIN_ENV), "Requires the isolated jobs-db fixture")
class BoundedResumePostgresTests(unittest.TestCase):
    def setUp(self):
        self.fixture = migration_tests.JobSchemaMigrationTests()
        self.addCleanup(lambda: self.assertTrue(self.fixture.doCleanups()))
        self.fixture.setUp()
        self.store = self.fixture.store()
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.parent = self.root / "previous.json"
        self.child = self.root / "continuation.json"
        self.now = datetime(2026, 11, 17, 0, tzinfo=timezone.utc).timestamp()
        self.results = {}
        self.publications = []
        self.fixture.install_v4()
        with self.fixture.connect() as db:
            ids = [self.fixture.seed_v4_job(db, f"beta-open-20261116:fixture-{n}",
                f"https://one-piece.com/news/{83400+n}/index.html", "running", True)[0] for n in range(4)]
        self.store.initialize()
        rows = []
        for job_id in ids:
            job = self.complete(self.store.get(job_id))
            rows.append({"job_id": job_id, "state": "complete", "counts_slot": True,
                "canonical_url": job["canonical_url"], "request": {"idempotency_key": job["idempotency_key"]},
                "execution_status": "succeeded", "outcome": "skipped", "producer_status": "content_type_mismatch_skipped",
                "generation_attempt_id": job["generation_attempt_id"], "result_sha256": job["result_sha256"],
                "estimated_cost_usd": "0.01000000"})
        self.old_ids = set(ids)
        self.state = {"schema_version": 1, "status": "complete", "stop_reason": "new_job_slots_consumed",
            "records": rows, "seen": [r["canonical_url"] for r in rows], "scans": 1,
            "config": {"run_id": "beta-open-20261116", "source_id": "one_piece_official", "content_type": "goods",
                "baseline_count": 1, "baseline_estimated_cost_usd": "0.01178341", "max_new_jobs": 4,
                "total_budget_usd": "5.00", "deadline": "2026-11-16T17:00:00+09:00",
                "next_job_reserve_usd": "0.04", "job_timeout_seconds": 1500, "scan_interval_seconds": 60,
                "excluded_urls": [], "artifact_root": str(self.root), "bundle_root": str(self.root)}}
        write_json(self.parent, self.state)
        self.original = self.parent.read_bytes()
        self.plan = self.make_plan()

    def make_plan(self, **overrides):
        values = dict(source=self.parent, source_sha256=hashlib.sha256(self.original).hexdigest(),
            checkpoint=self.child, total_budget_usd="5.00", additional_jobs=1,
            deadline="2026-11-17T12:00:00+09:00", now=self.now)
        values.update(overrides)
        return build_plan(**values)

    def complete(self, claimed):
        result = {"status": "content_type_mismatch_skipped", "usage_records": [{"estimated_cost_usd": "0.01"}],
                  "usage_summary": {"llm_calls": 1, "estimated_cost_usd": "0.01"}}
        digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        self.assertTrue(self.store.complete(claimed["job_id"], claimed["claim_token"], expected_kind="anime_generate",
            execution_status="succeeded", outcome="skipped", producer_status="content_type_mismatch_skipped",
            artifact_path=f"jobs/{claimed['job_id']}/attempts/{claimed['generation_attempt_id']}/manifest.json",
            result_sha256=digest))
        self.results[claimed["job_id"]] = result
        return self.store.get(claimed["job_id"])

    def read_result(self, job):
        return self.results[job["job_id"]]

    def test_real_db_lock_rejects_second_controller_and_releases_after_exit(self):
        with database_lock(self.store, self.plan["config"]["run_id"]) as check:
            check()
            def contender():
                with self.assertRaisesRegex(StopRun, "controller_already_running"):
                    with database_lock(self.fixture.store(), self.plan["config"]["run_id"]):
                        self.fail("Concurrent controller acquired the same run lock")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(contender).result(timeout=10)
        with database_lock(self.store, self.plan["config"]["run_id"]) as check:
            check()

    def test_one_new_attempt_replay_and_rollback_preserve_previous_jobs(self):
        before = self.fixture.snapshot()
        with local_lock(self.parent), database_lock(self.store, self.plan["config"]["run_id"]) as check:
            _, reservations = activate_plan(self.plan, store=self.store, read_result=self.read_result, now=self.now)
            self.assertEqual(self.fixture.snapshot(), before)
            fresh_child = self.child.read_bytes()
            original_get = self.store.get

            def get_with_fake_producer(job_id):
                job = original_get(job_id)
                if job and job_id not in self.old_ids and job["execution_status"] == "queued" and job.get("last_task_id"):
                    claimed = self.store.claim(job_id, expected_kind="anime_generate")
                    self.assertTrue(self.store.mark_generation_started(job_id, claimed["claim_token"], expected_kind="anime_generate"))
                    self.complete(claimed)
                    job = original_get(job_id)
                return job

            def publish(job_id):
                self.publications.append(job_id)
                return "fake-task-" + job_id

            def guard():
                check()
                parent = checkpoint_source(self.parent, self.plan["source_sha256"])
                validate_successor(controller.state, parent, self.plan)
                audit_existing_jobs(self.store, controller.state)

            article = {"source_id": "one_piece_official", "source_url": "https://one-piece.com/news/83500/index.html",
                       "canonical_url": "https://one-piece.com/news/83500/index.html", "content_type": "goods", "menu_work_id": 8}
            from unittest.mock import patch
            with patch.object(self.store, "get", side_effect=get_with_fake_producer):
                controller = Controller(config=self.plan["config"], checkpoint=self.child, store=self.store,
                    discover=lambda *_: {"articles": [article, article], "discovered": 2},
                    validate=lambda fields: (fields["canonical_url"], hashlib.sha256(fields["canonical_url"].encode()).hexdigest()),
                    publish=publish, read_result=self.read_result,
                    export=lambda *_: self.fail("Skipped result must not be exported"), reservations=reservations,
                    now=lambda: self.now, sleep=lambda _: self.fail("No polling needed for the fake producer"), lock_check=guard)
                report = controller.run()
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["new_slots_consumed"], 5)
            self.assertEqual(report["budget_accounted_total_usd"], "0.06178341")
            self.assertEqual(len(self.publications), 1)
            activate_plan(self.plan, store=self.store, read_result=self.read_result, now=self.now)
            self.assertEqual(self.parent.read_bytes(), self.original)
            after = self.fixture.snapshot()
            self.assertEqual(tuple([r for r in table if str(r["job_id"]) in self.old_ids] for table in after), before)
            self.assertEqual(len(after[0]), 5)
            with self.assertRaisesRegex(StopRun, "another_successor"):
                activate_plan(self.make_plan(checkpoint=self.root / "fork.json"), store=self.store,
                              read_result=self.read_result, now=self.now)
            self.child.write_bytes(fresh_child)  # Simulate a stale restore of only the new checkpoint.
            with self.assertRaisesRegex(StopRun, "database_history_not_owned"):
                activate_plan(self.plan, store=self.store, read_result=self.read_result, now=self.now)
            self.assertEqual(self.parent.read_bytes(), self.original)
            self.assertEqual(self.fixture.snapshot(), after)

    def test_changed_db_evidence_refuses_activation_without_writing_child(self):
        with self.fixture.connect() as db:
            db.execute("UPDATE content_generation_job SET result_sha256=%s WHERE job_id=%s",
                       ("0" * 64, next(iter(self.old_ids))))
        before = self.fixture.snapshot()
        with local_lock(self.parent), database_lock(self.store, self.plan["config"]["run_id"]):
            with self.assertRaisesRegex(StopRun, "completed_job_evidence_changed"):
                activate_plan(self.plan, store=self.store, read_result=self.read_result, now=self.now)
        self.assertFalse(self.child.exists())
        self.assertEqual(self.parent.read_bytes(), self.original)
        self.assertEqual(self.fixture.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
