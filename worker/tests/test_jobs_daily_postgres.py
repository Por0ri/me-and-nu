"""Isolated PostgreSQL daily-ledger guards; synthetic producer, no broker/LLM."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
import unittest
from unittest.mock import patch

import test_jobs_migration_postgres as migration_tests
import test_jobs_bounded_resume_postgres as legacy_tests
from menu_worker.bounded_generation import StopRun, audit_existing_jobs, database_lock, local_lock
from menu_worker.daily_generation import DailyController
from menu_worker.daily_resume import activate_plan, build_plan, source_checkpoint, validate_child


@unittest.skipUnless(os.environ.get(migration_tests.ADMIN_ENV), "Requires the isolated jobs-db fixture")
class DailyPostgresTests(unittest.TestCase):
    complete = legacy_tests.BoundedResumePostgresTests.complete
    read_result = legacy_tests.BoundedResumePostgresTests.read_result

    def setUp(self):
        legacy_tests.BoundedResumePostgresTests.setUp(self)

    def make_plan(self, **changes):
        inbox = self.root / "inbox"
        inbox.mkdir(exist_ok=True)
        args = dict(source=self.parent, source_sha256=hashlib.sha256(self.original).hexdigest(),
                    checkpoint=self.child, policy_date="2026-11-17", daily_budget_usd="0.04",
                    deadline="2026-11-17T12:00:00+09:00", delivery_root=inbox, now=self.now)
        args.update(changes)
        return build_plan(**args)

    def test_distinct_checkpoint_files_still_share_one_real_db_execution_lock(self):
        alternate = self.root / "copied-parent.json"
        alternate.write_bytes(self.original)
        with local_lock(self.parent), database_lock(self.store, self.plan["config"]["run_id"]):
            def second():
                with local_lock(alternate):
                    with self.assertRaisesRegex(StopRun, "controller_already_running"):
                        with database_lock(self.fixture.store(), self.plan["config"]["run_id"]):
                            self.fail("Different parent file bypassed the DB execution lock")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(second).result(timeout=10)

    def test_completed_daily_attempt_cannot_be_restarted_or_forked_from_copied_parent(self):
        before = self.fixture.snapshot()
        with local_lock(self.parent), database_lock(self.store, self.plan["config"]["run_id"]) as check:
            _, reservations = activate_plan(self.plan, store=self.store, read_result=self.read_result, now=self.now)
            fresh_child = self.child.read_bytes()
            original_get = self.store.get
            def fake_producer(job_id):
                job = original_get(job_id)
                if job and job_id not in self.old_ids and job["execution_status"] == "queued" and job.get("last_task_id"):
                    claimed = self.store.claim(job_id, expected_kind="anime_generate")
                    self.assertTrue(self.store.mark_generation_started(job_id, claimed["claim_token"], expected_kind="anime_generate"))
                    self.complete(claimed)
                    return original_get(job_id)
                return job
            def publish(job_id):
                self.publications.append(job_id)
                return "fake-task-" + job_id
            def guard():
                check()
                validate_child(controller.state, source_checkpoint(self.parent, self.plan["source_sha256"]), self.plan)
                audit_existing_jobs(self.store, controller.state)
            article = {"source_id": "one_piece_official", "source_url": "https://one-piece.com/news/83500/index.html",
                       "canonical_url": "https://one-piece.com/news/83500/index.html", "content_type": "goods", "menu_work_id": 8}
            with patch.object(self.store, "get", side_effect=fake_producer):
                controller = DailyController(config=self.plan["config"], checkpoint=self.child, store=self.store,
                    discover=lambda *_: {"articles": [article, article], "discovered": 2},
                    validate=lambda fields: (fields["canonical_url"], hashlib.sha256(fields["canonical_url"].encode()).hexdigest()),
                    publish=publish, read_result=self.read_result, export=lambda *_: self.fail("Skipped result cannot export"),
                    reservations=reservations, now=lambda: self.now, sleep=lambda _: self.fail("Fake producer completes immediately"), lock_check=guard)
                with self.assertRaisesRegex(StopRun, "estimated_budget_reserve_unavailable"):
                    controller.run()
            self.assertEqual(controller.summary()["daily_attempts"], 1)
            self.assertEqual(controller.summary()["daily_successes"], 0)
            self.assertEqual(len(self.publications), 1)
            after = self.fixture.snapshot()
            self.assertEqual(tuple([r for r in table if str(r["job_id"]) in self.old_ids] for table in after), before)
            self.assertEqual(len(after[0]), 5)
            activate_plan(self.plan, store=self.store, read_result=self.read_result, now=self.now)
            self.assertEqual(controller.run()["status"], "stopped")
            # Same parent receipt cannot authorize another day/child/limit.
            with self.assertRaisesRegex(StopRun, "another_successor"):
                activate_plan(self.make_plan(checkpoint=self.root / "fork.json"), store=self.store, read_result=self.read_result, now=self.now)
            # Even a copied original without its .successor file cannot reset the
            # day allowance: the real DB already contains the extra owned job.
            copied = self.root / "copied-parent.json"
            copied.write_bytes(self.original)
            fork = self.make_plan(source=copied, checkpoint=self.root / "copied-child.json")
            with self.assertRaisesRegex(StopRun, "database_job_cap_already_exceeded|database_history_not_owned"):
                activate_plan(fork, store=self.store, read_result=self.read_result, now=self.now)
            self.assertFalse((self.root / "copied-child.json").exists())
            self.child.write_bytes(fresh_child)
            with self.assertRaisesRegex(StopRun, "database_history_not_owned"):
                activate_plan(self.plan, store=self.store, read_result=self.read_result, now=self.now)
            self.assertEqual(self.fixture.snapshot(), after)
            self.assertEqual(self.parent.read_bytes(), self.original)

    def test_changed_historical_db_evidence_blocks_before_daily_checkpoint_write(self):
        with self.fixture.connect() as db:
            db.execute("UPDATE content_generation_job SET result_sha256=%s WHERE job_id=%s", ("0" * 64, next(iter(self.old_ids))))
        before = self.fixture.snapshot()
        with local_lock(self.parent), database_lock(self.store, self.plan["config"]["run_id"]):
            with self.assertRaisesRegex(StopRun, "completed_job_evidence_changed"):
                activate_plan(self.plan, store=self.store, read_result=self.read_result, now=self.now)
        self.assertFalse(self.child.exists())
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(self.parent.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
