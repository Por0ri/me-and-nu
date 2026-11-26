"""Failure skip reconciliation against an invocation-owned PostgreSQL DB.

All producer results/receipts are synthetic. The DB uses the dedicated migration
fixture, and no broker, provider, service DB or real Agent is contacted.
"""
import copy
from decimal import Decimal
import hashlib
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_jobs_bounded_resume_postgres as legacy_tests
import test_jobs_migration_postgres as migration_tests
from menu_worker.bounded_generation import StopRun, database_lock, read_json, write_json
from menu_worker.bounded_resume import digest, verify_completed_history
from menu_worker.daily_generation import DailyController
from menu_worker.daily_resume import activate_plan, build_plan, resume_failure_recovery, source_checkpoint
from menu_worker.job_artifacts import artifact_directory
from menu_worker.job_store import JobConflict
from menu_worker.real_generation import _identity, _request


@unittest.skipUnless(os.environ.get(migration_tests.ADMIN_ENV), "Requires the isolated jobs-db fixture")
class FailureRecoveryPostgresTests(unittest.TestCase):
    complete = legacy_tests.BoundedResumePostgresTests.complete
    read_result = legacy_tests.BoundedResumePostgresTests.read_result

    def setUp(self):
        from menu_worker import failure_recovery
        self.recovery = failure_recovery
        legacy_tests.BoundedResumePostgresTests.setUp(self)
        # The older fixture used only the idempotency key for its single-source
        # controller. Production discovery stores full input; retain that input
        # here so the multi-route controller can compare historical identities.
        parent = read_json(self.parent)
        for row in parent["records"]:
            job = self.store.get(row["job_id"])
            row["request"].update({key: job[key] for key in
                ("source_id", "source_url", "canonical_url", "content_type", "menu_work_id")})
        write_json(self.parent, parent)
        self.original = self.parent.read_bytes()
        self.plan = self.make_plan()
        state, _ = activate_plan(self.plan, store=self.store, read_result=self.read_result, now=self.now)
        self.before_day = copy.deepcopy(state)
        self.success_ids = []
        for number, outcome, cost in ((0, "ready_for_staging", "0.00682887"), (1, "held", "0.00593476")):
            request = self.request(83600 + number)
            job = self.started(request)
            result = {"status": "ready_for_feed" if outcome == "ready_for_staging" else "needs_crawler_update",
                      "usage_records": [{"estimated_cost_usd": cost}],
                      "usage_summary": {"llm_calls": 1, "estimated_cost_usd": cost}}
            self.assertTrue(self.store.complete(job["job_id"], job["claim_token"], expected_kind="anime_generate",
                execution_status="succeeded", outcome=outcome, producer_status=result["status"],
                artifact_path=f"jobs/{job['job_id']}/attempts/{job['generation_attempt_id']}/manifest.json",
                result_sha256=digest(result)))
            job = self.store.get(job["job_id"])
            self.results[job["job_id"]] = result
            state["records"].append({**self.record(job, request, state), "state": "complete",
                "execution_status": "succeeded", "outcome": outcome, "producer_status": result["status"],
                "generation_attempt_id": job["generation_attempt_id"], "result_sha256": job["result_sha256"],
                "estimated_cost_usd": cost})
            state["seen"].append(job["canonical_url"])
            self.success_ids.append(job["job_id"])
        self.failed_request = self.request(83602)
        failed = self.started(self.failed_request)
        self.assertTrue(self.store.complete(failed["job_id"], failed["claim_token"], expected_kind="anime_generate",
                                           execution_status="needs_attention", error_code="agent_process_failed"))
        self.failed = self.store.get(failed["job_id"])
        self.directory = artifact_directory(self.root, self.failed, create=True)
        write_json(self.directory / "launch.json", _identity(self.failed))
        write_json(self.directory / "exit.json", {"returncode": 1})
        (self.directory / "agent.log").write_bytes(b"agent exited 1\n")
        state["records"].append({**self.record(self.failed, self.failed_request, state),
            "execution_status": "needs_attention", "outcome": None, "producer_status": None})
        state["seen"].append(self.failed["canonical_url"])
        state.update(status="stopped", stop_reason="job_needs_attention")
        write_json(self.child, state)
        self.before = copy.deepcopy(state)
        self.original_checkpoint = self.child.read_bytes()
        self.original_database = self.fixture.snapshot()
        self.original_artifacts = self.artifact_bytes()

    def make_plan(self, **changes):
        inbox = self.root / "inbox"
        inbox.mkdir(exist_ok=True)
        values = dict(source=self.parent, source_sha256=hashlib.sha256(self.original).hexdigest(),
                      checkpoint=self.child, policy_date="2026-11-17", delivery_root=inbox,
                      routes=[{"source_id": "one_piece_official", "content_type": "goods", "menu_work_id": 8}],
                      now=self.now)
        values.update(changes)
        return build_plan(**values)

    def request(self, article):
        url = f"https://one-piece.com/news/{article}/index.html"
        return {"idempotency_key": f"beta-open-20261116:recovery-{article}", "source_id": "one_piece_official",
                "source_url": url, "canonical_url": url, "content_type": "goods", "menu_work_id": 8}

    def started(self, request):
        job = self.store.submit(request, job_kind="anime_generate")
        self.assertTrue(job["created"])
        claimed = self.store.claim(job["job_id"], expected_kind="anime_generate", lease_seconds=300)
        self.assertIsNotNone(claimed)
        self.assertTrue(self.store.mark_generation_started(job["job_id"], claimed["claim_token"], expected_kind="anime_generate"))
        return self.store.get(job["job_id"])

    def record(self, job, request, state):
        return {"request": request, "job_id": job["job_id"], "canonical_url": job["canonical_url"],
                "state": "reserved", "counts_slot": True, "reserved_at": self.now,
                "publish_intent": True, "publish_intent_at": self.now, "published": True,
                "policy_date": state["daily_policy"]["policy_date"],
                "day_policy_sha256": digest(state["daily_policy"])}

    def artifact_bytes(self):
        return {path.name: path.read_bytes() for path in self.directory.iterdir() if path.is_file()}

    def recovery_plan(self):
        return self.recovery.plan_recovery(checkpoint=self.child,
            expected_sha256=hashlib.sha256(self.original_checkpoint).hexdigest(), store=self.store,
            artifact_root=self.root, job_id=self.failed["job_id"], reason="operator_approved_skip_failed_article")

    def apply(self, plan=None):
        plan = self.recovery_plan() if plan is None else plan
        receipt = self.recovery.apply_recovery(plan, expected_plan_sha256=digest(plan), store=self.store)
        return plan, receipt, read_json(self.child)

    def controller(self, checkpoint=None, **overrides):
        checkpoint = self.child if checkpoint is None else checkpoint
        values = dict(config=read_json(checkpoint)["config"], checkpoint=checkpoint, store=self.store,
            discover=lambda *_: self.fail("No discovery is authorized by reconciliation"),
            validate=lambda *_: self.fail("No new admission is authorized by reconciliation"),
            publish=lambda *_: self.fail("No queue publication is authorized by reconciliation"),
            read_result=self.read_result, export=lambda *_: self.fail("No export is authorized by reconciliation"),
            now=lambda: self.now, sleep=lambda _: self.fail("No polling is needed"))
        values.update(overrides)
        return DailyController(**values)

    def test_apply_preserves_original_job_attempt_artifacts_and_known_history(self):
        plan = self.recovery_plan()
        self.assertEqual(self.child.read_bytes(), self.original_checkpoint)
        self.assertEqual(self.fixture.snapshot(), self.original_database)
        _, receipt, state = self.apply(plan)
        self.assertEqual(self.fixture.snapshot(), self.original_database)
        self.assertEqual(self.artifact_bytes(), self.original_artifacts)
        self.assertEqual(self.parent.read_bytes(), self.original)
        self.assertEqual(state["records"][:-1], self.before["records"][:-1])
        for key in ("seen", "config", "daily_policy", "daily_policies", "daily_plan_sha256"):
            self.assertEqual(state[key], self.before[key])
        row = state["records"][-1]
        self.assertEqual((state["status"], state["stop_reason"]), ("stopped", "failure_reconciled_paused"))
        self.assertEqual((row["execution_status"], row["state"], row["outcome"]), ("needs_attention", "complete", None))
        self.assertEqual(row["generation_attempt_id"], self.failed["generation_attempt_id"])
        self.assertTrue(row["counts_slot"])
        self.assertTrue(row["cost_unknown"])
        self.assertIsNone(row["estimated_cost_usd"])
        self.assertIsNone(row["measured_cost"])
        self.assertNotIn("reserved_cost_usd", row)
        self.assertIsNone(receipt["known_partial_estimated_usd"])
        self.assertTrue(receipt["unknown_total"])
        self.recovery.validate_recovery_record(row, store=self.store, artifact_root=self.root)
        verify_completed_history(self.store, state, self.read_result)
        after = self.child.read_bytes()
        self.recovery.apply_recovery(plan, expected_plan_sha256=digest(plan), store=self.store)
        self.assertEqual(self.child.read_bytes(), after)
        self.assertEqual(self.fixture.snapshot(), self.original_database)

    def test_explicit_next_day_preserves_unknown_and_enforces_real_forty_attempt_cap(self):
        _, receipt, state = self.apply()
        report = self.controller().summary()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (3, 1))
        self.assertEqual(report["unknown_cost_jobs"], [self.failed["job_id"]])
        self.assertEqual(report["daily_unknown_cost_jobs"], [self.failed["job_id"]])
        self.assertIsNone(report["estimated_total_cost_usd"])
        self.assertIsNone(report["daily_spent_usd"])
        self.assertEqual(Decimal(report["daily_known_and_reserved_subtotal_usd"]), Decimal("0.01276363"))
        paused = self.child.read_bytes()
        with self.assertRaisesRegex(StopRun, "daily_source_requires_reconciliation"):
            source_checkpoint(self.child, hashlib.sha256(paused).hexdigest())
        self.assertEqual(self.child.read_bytes(), paused)
        with self.assertRaisesRegex(StopRun, "explicit_reviewed_failure_receipt_required"):
            resume_failure_recovery(copy.deepcopy(state), "0" * 64, store=self.store, now=self.now)
        next_now = self.now + 86400
        authorized = resume_failure_recovery(state, digest(receipt), store=self.store, now=next_now)
        self.assertEqual((authorized["status"], authorized["stop_reason"]), ("stopped", "deadline_reached"))
        write_json(self.child, authorized)  # This test's isolated local checkpoint only.
        source_sha = hashlib.sha256(self.child.read_bytes()).hexdigest()
        source_checkpoint(self.child, source_sha)
        next_child = self.root / "next-day.json"
        args = dict(source=self.child, source_sha256=source_sha, checkpoint=next_child,
            policy_date="2026-11-18", delivery_root=self.root / "inbox", now=next_now,
            routes=self.plan["daily_policy"]["routes"])
        with self.assertRaisesRegex(StopRun, "unknown_cost_in_budget_mode"):
            build_plan(**args, daily_budget_usd="5.00", deadline="2026-11-18T23:00:00+09:00")
        next_plan = build_plan(**args)
        self.assertEqual(next_plan["config"]["max_new_jobs"], 47)
        self.assertEqual((next_plan["daily_policy"]["max_attempts"], next_plan["daily_policy"]["success_target"]), (40, 20))
        self.assertEqual(next_plan["daily_policy"]["prior_unknown_costs"],
                         [{"job_id": self.failed["job_id"], **state["records"][-1]["failure_recovery_receipt"]}])
        next_state, _ = activate_plan(next_plan, store=self.store, read_result=self.read_result, now=next_now)
        self.assertEqual(next_state["records"], state["records"])
        self.assertEqual(self.fixture.snapshot(), self.original_database)
        self.assertEqual(self.store.get(self.failed["job_id"]), self.failed)
        original_get = self.store.get
        previous_ids = self.old_ids | set(self.success_ids) | {self.failed["job_id"]}
        publications, scans = [], []

        def fake_producer(job_id):
            job = original_get(job_id)
            if job and job_id not in previous_ids and job["execution_status"] == "queued" and job.get("last_task_id"):
                claimed = self.store.claim(job_id, expected_kind="anime_generate")
                self.assertTrue(self.store.mark_generation_started(job_id, claimed["claim_token"], expected_kind="anime_generate"))
                self.complete(claimed)
                return original_get(job_id)
            return job

        def discover(*_):
            scans.append(len(scans))
            # Re-offer the failed article on every scan; it must stay skipped.
            articles = [self.failed_request, self.request(84000 + len(scans))]
            return {"articles": articles, "discovered": 2}

        def publish(job_id):
            publications.append(job_id)
            return "synthetic-task-" + job_id

        with patch.object(self.store, "get", side_effect=fake_producer):
            controller = self.controller(next_child, discover=discover, publish=publish, now=lambda: next_now,
                sleep=lambda _: None,  # Skip the deliberate pause between synthetic discovery rounds.
                validate=lambda fields: (fields["canonical_url"], hashlib.sha256(fields["canonical_url"].encode()).hexdigest()))
            completed = controller.run()
        self.assertEqual((completed["status"], completed["stop_reason"]), ("complete", "daily_attempt_limit_reached"))
        self.assertEqual((completed["daily_attempts"], completed["daily_successes"]), (40, 0))
        self.assertEqual(completed["unknown_cost_jobs"], [self.failed["job_id"]])
        self.assertEqual(completed["daily_unknown_cost_jobs"], [])
        self.assertIsNone(completed["estimated_total_cost_usd"])
        self.assertEqual(Decimal(completed["daily_spent_usd"]), Decimal("0.40"))
        self.assertEqual(len(publications), 40)
        self.assertNotIn(self.failed["job_id"], publications)
        self.assertEqual(controller.state["records"][:7], state["records"])
        self.assertEqual(self.store.get(self.failed["job_id"]), self.failed)
        verify_completed_history(self.store, controller.state, self.read_result)
        completed_bytes = next_child.read_bytes()
        replay = self.controller(next_child, now=lambda: next_now).run()
        self.assertEqual(replay["daily_attempts"], 40)
        self.assertEqual(next_child.read_bytes(), completed_bytes)
        self.assertEqual(self.parent.read_bytes(), self.original)

    def test_reconciled_article_stays_deduplicated_and_another_article_can_run(self):
        self.apply()
        before = self.fixture.snapshot()
        self.assertFalse(self.store.retry_preflight(self.failed["job_id"], expected_kind="anime_generate"))
        self.assertIsNone(self.store.claim(self.failed["job_id"], expected_kind="anime_generate"))
        replay = self.store.submit(self.failed_request, job_kind="anime_generate")
        self.assertFalse(replay["created"])
        self.assertEqual(replay["job_id"], self.failed["job_id"])
        for suffix in ("", "/", "?utm_source=fixture"):
            duplicate = {**self.failed_request, "idempotency_key": "another-key-" + str(uuid4()),
                "source_url": self.failed["canonical_url"] + suffix, "canonical_url": self.failed["canonical_url"] + suffix}
            with self.assertRaisesRegex(JobConflict, "candidate_exists"):
                self.store.submit(duplicate, job_kind="anime_generate")
        self.assertEqual(self.fixture.snapshot(), before)
        other = self.store.submit(self.request(83603), job_kind="anime_generate")
        with self.store.generation_history_guard() as check:
            check()
            self.assertFalse(self.store.generation_history_busy(other["job_id"]))
            history = self.store.cover_history_sources(other["job_id"])
            self.assertEqual({r["job_id"] for r in history}, self.old_ids | set(self.success_ids))
            self.assertNotIn(self.failed["job_id"], {r["job_id"] for r in history})
            claimed = self.store.claim(other["job_id"], expected_kind="anime_generate")
            self.assertIsNotNone(claimed)
            self.assertTrue(self.store.mark_generation_started(other["job_id"], claimed["claim_token"],
                                                              expected_kind="anime_generate"))
            check()
            self.assertTrue(self.store.generation_history_busy(str(uuid4())))
        self.assertEqual(self.store.get(self.failed["job_id"]), self.failed)

    def test_other_live_job_blocks_plan_without_modifying_checkpoint_or_history(self):
        self.started(self.request(83603))
        before = self.fixture.snapshot()
        with self.assertRaises(StopRun):
            self.recovery_plan()
        self.assertEqual(self.child.read_bytes(), self.original_checkpoint)
        self.assertEqual(self.artifact_bytes(), self.original_artifacts)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_active_controller_lock_blocks_apply_without_checkpoint_mutation(self):
        plan = self.recovery_plan()
        with database_lock(self.store, plan["run_id"]):
            with self.assertRaisesRegex(StopRun, "controller_already_running"):
                self.apply(plan)
        self.assertEqual(self.child.read_bytes(), self.original_checkpoint)
        self.assertEqual(self.fixture.snapshot(), self.original_database)
        self.assertEqual(self.artifact_bytes(), self.original_artifacts)

    def test_concurrent_ledger_writer_blocks_apply_without_stale_evidence(self):
        plan = self.recovery_plan()
        with self.fixture.connect() as writer:
            # INSERT/UPDATE take this mode; holding it tests the real table lock
            # without committing a fixture mutation or waiting for a deadlock.
            writer.execute("LOCK TABLE public.content_generation_job IN ROW EXCLUSIVE MODE")
            with self.assertRaises(self.fixture.psycopg.errors.LockNotAvailable):
                self.apply(plan)
        self.assertEqual(self.child.read_bytes(), self.original_checkpoint)
        self.assertEqual(self.fixture.snapshot(), self.original_database)
        self.assertEqual(self.artifact_bytes(), self.original_artifacts)

    def test_changed_failed_evidence_blocks_apply_without_writing_checkpoint(self):
        plan = self.recovery_plan()
        with self.fixture.connect() as connection:
            connection.execute("UPDATE content_generation_attempt SET error_code='agent_timeout' WHERE attempt_id=%s",
                               (self.failed["generation_attempt_id"],))
        changed = self.fixture.snapshot()
        with self.assertRaises(StopRun):
            self.apply(plan)
        self.assertEqual(self.child.read_bytes(), self.original_checkpoint)
        self.assertEqual(self.artifact_bytes(), self.original_artifacts)
        self.assertEqual(self.fixture.snapshot(), changed)

    def test_changed_artifacts_fail_later_history_verification_without_db_writes(self):
        _, _, state = self.apply()
        (self.directory / "exit.json").write_text('{"returncode": 0}', encoding="utf-8")
        after = self.child.read_bytes()
        with self.assertRaises(StopRun):
            self.recovery.validate_recovery_history(state, store=self.store, artifact_root=self.root)
        with self.assertRaises(StopRun):
            verify_completed_history(self.store, state, self.read_result)
        self.assertEqual(self.child.read_bytes(), after)
        self.assertEqual(self.fixture.snapshot(), self.original_database)

    def test_partial_known_usage_is_retained_while_total_remains_unknown(self):
        write_json(self.directory / "result.json", {"status": "generation_failed", "request_id": "fixture-failed",
            "expected_work_request": _request(self.failed), "failure": {"stage": "content_generation", "error_type": "TypeError"},
            "usage_records": [{"estimated_cost_usd": "0.0025", "stage": "extraction"}],
            "cost_accounting": {"complete": False}})
        original = self.artifact_bytes()
        _, receipt, state = self.apply()
        self.assertEqual(Decimal(receipt["known_partial_estimated_usd"]), Decimal("0.0025"))
        self.assertTrue(receipt["unknown_total"])
        self.assertTrue(state["records"][-1]["cost_unknown"])
        self.assertIsNone(state["records"][-1]["estimated_cost_usd"])
        self.assertEqual(self.artifact_bytes(), original)
        self.assertEqual(self.fixture.snapshot(), self.original_database)


if __name__ == "__main__":
    unittest.main()
