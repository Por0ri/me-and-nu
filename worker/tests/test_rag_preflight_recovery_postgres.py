"""Real isolated PostgreSQL proofs for zero-cost, pre-generation RAG failures.

The worker callback always fails before its provider boundary. These tests never
start a Celery consumer, contact a model, or connect to the service database.
"""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import unittest
from uuid import uuid4

import test_jobs_migration_postgres as database_fixture
import test_rag_daily as daily_fixture
from menu_worker.bounded_generation import StopRun
from menu_worker.bounded_resume import verify_completed_history
from menu_worker.job_store import JobConflict
from menu_worker.rag_contract import RagRequest
from menu_worker.rag_store import RagStore
from menu_worker.rag_tasks import execute_delivery


@unittest.skipUnless(os.environ.get(database_fixture.ADMIN_ENV), "Requires isolated jobs-db migration fixture")
class RagPreflightRecoveryPostgresTests(unittest.TestCase):
    def setUp(self):
        self.database = database_fixture.JobSchemaMigrationTests()
        self.database.setUp()
        self.addCleanup(self.database.doCleanups)
        self.rag = RagStore(self.database.dsn, expected_database=database_fixture.MIGRATION_DB)
        self.rag.initialize()
        self.daily = daily_fixture.MixedDailyTests()
        self.daily.setUp()
        self.addCleanup(self.daily.doCleanups)
        self.controller = self.daily.controller()
        self.controller.rag_store = self.rag
        self.controller.rag_read_result = lambda _: self.fail("Preflight failure has no generated result")
        self.controller.rag_export = lambda *_: self.fail("Failed generation must not create a staging delivery")
        self.root = Path(self.controller.config["artifact_root"])
        self.initial_history = deepcopy(self.controller.state["records"])
        self.published = []

        def publish(job_id):
            self.published.append(job_id)
            def preflight(_):
                raise ValueError("Synthetic unchanged-document preflight failure")
            return execute_delivery(self.rag, self.root, job_id,
                run_native=lambda *_: self.fail("Provider must never run"), preflight=preflight,
                enabled=True, now=self.controller.now)
        self.controller.rag_publish = publish

    def fail_before_generation(self, candidate=0):
        fields = self.controller.collect_rag()[candidate]
        record = self.controller.reserve_rag(fields)
        request = RagRequest.parse(fields["rag_request"])
        submitted = self.rag.submit(request, record["rag_admission"])
        record.update(job_id=submitted["job_id"], publish_intent=True, published=True,
                      published_at=self.controller.now())
        self.controller.save()
        report = self.controller.rag_publish(record["job_id"])
        self.assertEqual(report["execution_status"], "failed")
        job = self.rag.get(record["job_id"])
        self.assertEqual(job["error_code"], "rag_preflight_failed")
        self.assertIsNone(job["generation_started_at"])
        self.assertEqual(job["usage_records"], [])
        self.assertIs(job["cost_complete"], True)
        return record, job

    def verify_history(self, state=None):
        return verify_completed_history(self.controller.store, state or self.controller.state,
            self.controller.read_result, rag_store=self.rag,
            rag_read_result=lambda _: self.fail("A zero-cost failed attempt has no sealed result"))

    @contextmanager
    def change_field(self, job, field, value, *, attempt=False):
        from psycopg.types.json import Jsonb
        table = "content_rag_generation_attempt" if attempt else "content_rag_generation_job"
        column = "attempt_id" if attempt else "job_id"
        row = job["attempts"][0] if attempt else job
        identity = row[column]
        def database_value(item):
            return Jsonb(item) if isinstance(item, (dict, list)) else item
        with self.database.connect() as connection:
            connection.execute(f"UPDATE public.{table} SET {field}=%s WHERE {column}=%s",
                               (database_value(value), identity))
        try:
            yield
        finally:
            with self.database.connect() as connection:
                connection.execute(f"UPDATE public.{table} SET {field}=%s WHERE {column}=%s",
                                   (database_value(row[field]), identity))

    def test_actual_failed_rows_are_preserved_and_next_work_consumes_a_new_slot(self):
        record, original = self.fail_before_generation()
        admission = deepcopy(record["rag_admission"])
        self.controller.finish_job(record)
        self.assertEqual(self.rag.get(record["job_id"]), original)
        self.assertEqual(record["state"], "complete")
        self.assertEqual(record["execution_status"], "failed")
        self.assertTrue(record["counts_slot"])
        self.assertEqual(record["rag_admission"], admission)
        self.assertEqual(Decimal(record["estimated_cost_usd"]), 0)
        self.assertEqual(record["rag_usage_records"], [])
        self.assertNotIn("rag_delivery", record)
        self.assertEqual(self.controller.state["records"][:-1], self.initial_history)
        self.assertIn(record["canonical_url"], self.controller.state["seen"])
        first = self.controller.summary()
        self.assertEqual((first["daily_attempts"], first["daily_successes"]), (1, 0))
        self.assertEqual(first["reserved_cost_usd"], "1.00")
        self.verify_history()

        available = self.controller.collect_rag()
        self.assertTrue(available)
        self.assertNotIn(record["canonical_url"], [row["canonical_url"] for row in available])
        second = self.controller.reserve_rag(available[0])
        self.controller.handle(second)
        self.assertEqual(second["state"], "complete")
        self.assertNotEqual(record["job_id"], second["job_id"])
        self.assertEqual((self.controller.summary()["daily_attempts"],
                          self.controller.summary()["daily_successes"]), (2, 0))
        self.assertEqual(self.published, [record["job_id"], second["job_id"]])
        self.assertEqual(self.rag.get(record["job_id"]), original)
        self.verify_history()

    def test_repeated_completion_and_delivery_do_not_recreate_the_failed_job(self):
        record, original = self.fail_before_generation()
        self.controller.finish_job(record)
        finished = deepcopy(record)
        self.controller.finish_job(record)
        self.assertEqual(record, finished)
        replay = execute_delivery(self.rag, self.root, record["job_id"],
            run_native=lambda *_: self.fail("Never regenerate"),
            preflight=lambda *_: self.fail("Never rerun a failed admission"),
            enabled=True, now=self.controller.now)
        self.assertFalse(replay["processed"])
        request = RagRequest.parse(record["request"]["rag_request"])
        self.assertFalse(self.rag.submit(request, record["rag_admission"])["created"])
        other_admission = {**record["rag_admission"], "reservation_id": str(uuid4())}
        with self.assertRaisesRegex(JobConflict, "rag_candidate_exists"):
            self.rag.submit(request, other_admission)
        self.assertEqual(self.rag.get(record["job_id"]), original)
        self.assertEqual(self.controller.summary()["daily_attempts"], 1)
        self.verify_history()

    def test_real_forty_zero_failures_allow_ordinal_forty_one_only_after_reviewed_revision(self):
        from menu_worker.bounded_resume import digest
        from menu_worker.daily_limits import build_limit_revision, validate_limit_revisions
        from menu_worker.daily_resume import validate_policy_history

        original_jobs = {}
        for _ in range(40):
            record, job = self.fail_before_generation()
            self.controller.finish_job(record)
            self.assertEqual(record["rag_admission"]["schema_version"], 1)
            original_jobs[job["job_id"]] = job
        self.assertEqual(self.controller.summary()["daily_attempts"], 40)
        self.assertEqual(self.controller.summary()["daily_successes"], 0)
        self.controller.state.update(status="stopped", stop_reason="daily_attempt_limit_reached")
        prefix = deepcopy(self.controller.state["records"])
        revision = build_limit_revision(self.controller.state,
            reason="Approved attempt-cap removal; preserve all zero-cost failures",
            approved_at="2026-11-24T11:00:00+09:00")
        self.controller.state["daily_limit_revisions"] = [revision]
        self.controller.state.update(status="running", stop_reason=None)
        self.controller.save()

        record, job = self.fail_before_generation()
        self.controller.finish_job(record)
        self.assertEqual(record["rag_admission"]["schema_version"], 2)
        self.assertEqual(record["rag_admission"]["attempt_ordinal"], 41)
        self.assertEqual(record["rag_admission"]["limit_revision_sha256"], digest(revision))
        self.assertEqual(record["daily_limit_revision_sha256"], digest(revision))
        self.assertEqual(self.controller.state["records"][:len(prefix)], prefix)
        self.assertEqual((self.controller.summary()["daily_attempts"],
                          self.controller.summary()["daily_successes"]), (41, 0))
        self.assertIsNone(self.controller.summary()["daily_attempt_limit"])
        self.assertEqual(len(self.published), len(set(self.published)))
        self.assertEqual(len(self.published), 41)
        for job_id, original in original_jobs.items():
            self.assertEqual(self.rag.get(job_id), original)
        self.assertEqual(self.rag.get(job["job_id"]), job)
        validate_limit_revisions(self.controller.state)
        validate_policy_history(self.controller.state)
        self.verify_history()

    def test_reuse_blocks_every_ambiguous_job_or_attempt_field(self):
        record, original = self.fail_before_generation()
        self.controller.finish_job(record)
        cases = [
            ("generation_started_at", "2026-11-24T01:01:00+00:00"),
            ("cost_complete", False), ("cost_complete", None),
            ("usage_records", None),
            ("usage_records", [{"stage": "generation", "estimated_cost_usd": "0.00"}]),
            ("finished_at", None), ("error_code", "RAG_LEASE_EXPIRED_BEFORE_GENERATION"),
            ("outcome", "held"), ("producer_status", "waiting_for_evidence"),
            ("result_sha256", "a" * 64), ("artifact_path", "unexpected/result.json"),
        ]
        for attempt in (False, True):
            for field, value in cases:
                with self.subTest(attempt=attempt, field=field, value=value), self.change_field(
                        original, field, value, attempt=attempt):
                    with self.assertRaises(StopRun):
                        self.verify_history()
        for field, value in (("claim_token", str(uuid4())),
                             ("lease_expires_at", "2026-11-24T01:02:00+00:00")):
            with self.subTest(field=field), self.change_field(original, field, value):
                with self.assertRaises(StopRun):
                    self.verify_history()
        self.assertEqual(self.rag.get(record["job_id"]), original)
        self.verify_history()

    def test_admission_or_checkpoint_cost_changes_cannot_reuse_the_completion(self):
        record, original = self.fail_before_generation()
        self.controller.finish_job(record)
        for field, value in (("attempt_ordinal", 2), ("policy_date", "2026-11-25"),
                             ("policy_sha256", "b" * 64), ("reservation_id", str(uuid4()))):
            with self.subTest(field=field):
                changed = deepcopy(self.controller.state)
                changed["records"][-1]["rag_admission"][field] = value
                with self.assertRaises(StopRun):
                    self.verify_history(changed)
        for field, value in (("estimated_cost_usd", "0.01"), ("counts_slot", False),
                             ("rag_usage_records", [{"estimated_cost_usd": "0.01"}])):
            with self.subTest(field=field):
                changed = deepcopy(self.controller.state)
                changed["records"][-1][field] = value
                with self.assertRaises(StopRun):
                    self.verify_history(changed)
        self.assertEqual(self.rag.get(record["job_id"]), original)

    def test_unrecorded_launch_artifact_blocks_free_failure_completion(self):
        record, original = self.fail_before_generation()
        directory = self.root / "rag/jobs" / original["job_id"] / "attempts" / original["generation_attempt_id"]
        directory.mkdir(parents=True)
        (directory / "launch.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(StopRun):
            self.controller.finish_job(record)
        self.assertNotEqual(record["state"], "complete")
        self.assertTrue(record["counts_slot"])
        self.assertEqual(self.rag.get(record["job_id"]), original)

    def test_conflicting_checkpoint_evidence_is_not_erased_by_zero_completion(self):
        record, original = self.fail_before_generation()
        baseline = deepcopy(record)
        cases = (("estimated_cost_usd", "0.01"),
                 ("rag_usage_records", [{"stage": "generation", "estimated_cost_usd": "0.01"}]),
                 ("rag_known_partial_usd", "0.01"), ("generation_attempt_id", str(uuid4())),
                 ("outcome", "held"), ("result_sha256", "a" * 64))
        for field, value in cases:
            with self.subTest(field=field):
                record.clear()
                record.update(deepcopy(baseline))
                record[field] = value
                self.controller.state.update(status="running", stop_reason=None)
                conflicting = deepcopy(record)
                with self.assertRaises(StopRun):
                    self.controller.finish_job(record)
                self.assertEqual(record, conflicting)
                self.assertEqual(self.rag.get(record["job_id"]), original)


@unittest.skipUnless(os.environ.get(database_fixture.ADMIN_ENV), "Requires isolated jobs-db migration fixture")
class HistoricalRagRecoveryPostgresTests(unittest.TestCase):
    def setUp(self):
        import test_failure_recovery_postgres as legacy_fixture
        from menu_worker.bounded_generation import read_json, write_json
        from menu_worker.bounded_resume import digest
        from menu_worker.rag_daily import pin_scope, rag_route
        from test_rag_contract import request

        self.old = legacy_fixture.FailureRecoveryPostgresTests()
        self.old.setUp()
        self.addCleanup(self.old.doCleanups)
        # Preserve an earlier started/unknown-cost failure and its immutable
        # receipt. The new zero-cost recovery must not settle that old charge.
        self.old.apply()
        self.store = self.old.store
        self.rag = RagStore.__new__(RagStore)
        self.rag.jobs = self.store
        self.root, self.checkpoint = self.old.root, self.old.child
        item = RagRequest.parse(request(work_id="zero-cost-recovery"))
        scope = {"schema_version": 1, "purpose": "rag_daily_generation", "routes": [{
            "namespace": item.namespace, "menu_work_id": item.menu_work_id,
            "content_type": item.content_type, "allowed_source_ids": list(item.allowed_source_ids)}]}
        pin_scope(self.old.controller(), scope, reviewed_sha256=digest(scope))
        state = read_json(self.checkpoint)
        admission = {"schema_version": 1, "reservation_id": str(uuid4()),
            "policy_date": state["daily_policy"]["policy_date"],
            "policy_sha256": digest({"daily": state["daily_policy"], "rag": scope}),
            "input_sha256": item.input_sha256, "counts_slot": True,
            "attempt_ordinal": 1 + sum(row.get("counts_slot") is True
                for row in state["records"][state["daily_policy"]["start_record_index"]:])}
        submitted = self.rag.submit(item, admission)
        def preflight(_):
            raise ValueError("Synthetic pre-generation evidence mismatch")
        execute_delivery(self.rag, self.root, submitted["job_id"],
            run_native=lambda *_: self.fail("Provider must never run"), preflight=preflight,
            enabled=True, now=lambda: self.old.now)
        self.job = self.rag.get(submitted["job_id"])
        url = "urn:menu-rag:" + item.identity_key
        record = {"producer": "ani_rag", "request": {**rag_route(item),
            "producer": "ani_rag", "rag_request": item.as_dict(), "canonical_url": url,
            "idempotency_key": state["config"]["run_id"] + ":rag:" + item.identity_key},
            "canonical_url": url, "rag_input_sha256": item.input_sha256, "rag_admission": admission,
            "job_id": self.job["job_id"], "generation_attempt_id": self.job["generation_attempt_id"],
            "state": "reserved", "counts_slot": True, "reserved_at": self.old.now,
            "publish_intent": True, "published": True, "published_at": self.old.now,
            "policy_date": admission["policy_date"], "day_policy_sha256": digest(state["daily_policy"]),
            "execution_status": "failed", "outcome": None, "producer_status": None,
            "result_sha256": None, "rag_usage_records": [], "estimated_cost_usd": "0.00000000"}
        state["records"].append(record)
        state["seen"].append(url)
        state.update(status="stopped", stop_reason="rag_generation_requires_attention")
        write_json(self.checkpoint, state)
        self.before = deepcopy(state)
        self.before_bytes = self.checkpoint.read_bytes()
        self.before_database = self.snapshot()

    def snapshot(self):
        from menu_worker.failure_recovery import _database_view
        with _database_view(self.store) as snapshot:
            return deepcopy(snapshot)

    def plan(self):
        from menu_worker.rag_preflight_recovery import plan_recovery
        return plan_recovery(checkpoint=self.checkpoint,
            expected_sha256=hashlib.sha256(self.before_bytes).hexdigest(), store=self.store,
            artifact_root=self.root, job_id=self.job["job_id"],
            reason="operator_reviewed_preflight_failure_without_generation_or_cost")

    def apply(self, plan=None):
        from menu_worker.bounded_generation import read_json
        from menu_worker.bounded_resume import digest
        from menu_worker.rag_preflight_recovery import apply_recovery
        plan = self.plan() if plan is None else plan
        receipt = apply_recovery(plan, expected_plan_sha256=digest(plan), store=self.store)
        return plan, receipt, read_json(self.checkpoint)

    def test_plan_apply_preserve_mixed_history_and_remain_paused(self):
        from menu_worker.bounded_generation import read_json
        from menu_worker.bounded_resume import digest
        from menu_worker.daily_resume import current_failure_references, validate_failure_resume
        from menu_worker.failure_recovery import validate_recovery_record
        plan = self.plan()
        self.assertEqual(self.checkpoint.read_bytes(), self.before_bytes)
        self.assertEqual(self.snapshot(), self.before_database)
        _, receipt, state = self.apply(plan)
        self.assertEqual(self.snapshot(), self.before_database)
        self.assertEqual(self.rag.get(self.job["job_id"]), self.job)
        self.assertEqual(state["records"][:-1], self.before["records"][:-1])
        for key in ("seen", "config", "daily_policy", "daily_policies", "daily_plan_sha256"):
            self.assertEqual(state[key], self.before[key])
        self.assertEqual((state["status"], state["stop_reason"]), ("stopped", "failure_reconciled_paused"))
        self.assertEqual(state["records"][-1]["execution_status"], "failed")
        self.assertTrue(state["records"][-1]["counts_slot"])
        self.assertEqual(state["records"][-1]["estimated_cost_usd"], "0.00000000")
        self.assertIs(receipt["generation_enabled"], False)
        self.assertIs(receipt["resume_authorized"], False)
        self.assertEqual(Path(receipt["checkpoint_backup"]).read_bytes(), self.before_bytes)
        self.assertEqual(read_json(Path(receipt["database_backup"])), self.before_database)
        validate_recovery_record(state["records"][-1], store=self.store, artifact_root=self.root)
        refs = current_failure_references(state)
        self.assertEqual(len(refs), 2)  # Prior unknown cost + current proven zero.
        self.assertEqual(refs[-1]["receipt_sha256"], digest(receipt))
        self.assertEqual(validate_failure_resume(state, digest(receipt), store=self.store, now=self.old.now), receipt)
        verify_completed_history(self.store, state, self.old.read_result, rag_store=self.rag,
            rag_read_result=lambda _: self.fail("No result exists for a preflight failure"))
        after = self.checkpoint.read_bytes()
        self.apply(plan)
        self.assertEqual(self.checkpoint.read_bytes(), after)
        self.assertEqual(self.snapshot(), self.before_database)

    def test_changed_checkpoint_or_database_blocks_apply_without_overwrite(self):
        from menu_worker.bounded_resume import digest
        from menu_worker.rag_preflight_recovery import apply_recovery
        plan = self.plan()
        self.checkpoint.write_bytes(self.before_bytes + b"\n")
        changed = self.checkpoint.read_bytes()
        with self.assertRaises((StopRun, OSError)):
            apply_recovery(plan, expected_plan_sha256=digest(plan), store=self.store)
        self.assertEqual(self.checkpoint.read_bytes(), changed)
        self.checkpoint.write_bytes(self.before_bytes)
        with self.store._connect() as connection:
            connection.execute("UPDATE content_rag_generation_job SET error_code='changed_after_plan' WHERE job_id=%s",
                               (self.job["job_id"],))
        with self.assertRaisesRegex(StopRun, "recovery_database_changed"):
            apply_recovery(plan, expected_plan_sha256=digest(plan), store=self.store)
        self.assertEqual(self.checkpoint.read_bytes(), self.before_bytes)

    def test_explicit_next_day_keeps_zero_and_unknown_history_without_resetting_limits(self):
        from menu_worker.bounded_generation import write_json
        from menu_worker.bounded_resume import digest
        from menu_worker.daily_resume import (activate_plan, build_plan, preflight_plan,
                                              resume_failure_recovery, source_checkpoint)
        _, receipt, state = self.apply()
        paused = self.checkpoint.read_bytes()
        with self.assertRaisesRegex(StopRun, "daily_source_requires_reconciliation"):
            source_checkpoint(self.checkpoint, hashlib.sha256(paused).hexdigest())
        next_now = self.old.now + 86400
        resumed = resume_failure_recovery(deepcopy(state), digest(receipt), store=self.store, now=next_now)
        self.assertEqual((resumed["status"], resumed["stop_reason"]), ("stopped", "deadline_reached"))
        write_json(self.checkpoint, resumed)  # Invocation-owned synthetic checkpoint only.
        rag_inbox = self.root / "rag-next-day-inbox"
        rag_inbox.mkdir()
        next_plan = build_plan(source=self.checkpoint,
            source_sha256=hashlib.sha256(self.checkpoint.read_bytes()).hexdigest(),
            checkpoint=self.root / "next-mixed-day.json", policy_date="2026-11-18",
            delivery_root=self.root / "inbox", now=next_now,
            routes=self.old.plan["daily_policy"]["routes"], rag_scope=state["rag_scope"],
            rag_scope_sha256=state["rag_scope_sha256"], rag_delivery_root=rag_inbox,
            rag_runtime_sha256="a" * 64)
        self.assertEqual((next_plan["daily_policy"]["max_attempts"],
                          next_plan["daily_policy"]["success_target"]), (40, 20))
        self.assertEqual(next_plan["config"]["max_new_jobs"],
                         40 + sum(row["counts_slot"] for row in state["records"]))
        self.assertEqual([row["job_id"] for row in next_plan["daily_policy"]["prior_unknown_costs"]],
                         [self.old.failed["job_id"]])
        options = {"store": self.store, "read_result": self.old.read_result,
            "rag_store": self.rag, "rag_read_result": lambda _: self.fail("No RAG generation result exists"),
            "now": next_now}
        preflight_plan(next_plan, **options)
        next_state, _ = activate_plan(next_plan, **options)
        self.assertEqual(next_state["records"], state["records"])
        verify_completed_history(self.store, next_state, self.old.read_result, rag_store=self.rag,
            rag_read_result=lambda _: self.fail("Never regenerate old failed work"))
        self.assertEqual(self.snapshot(), self.before_database)
        self.assertEqual(self.rag.get(self.job["job_id"]), self.job)

    def test_changed_receipt_prevents_resume_and_preserves_jobs(self):
        from menu_worker.bounded_generation import write_json
        from menu_worker.bounded_resume import digest
        from menu_worker.daily_resume import validate_failure_resume
        _, receipt, state = self.apply()
        path = Path(state["records"][-1]["failure_recovery_receipt"]["receipt_path"])
        write_json(path, {**receipt, "operator_reason": "changed receipt after application"})
        with self.assertRaisesRegex(StopRun, "failure_recovery_receipt_changed"):
            validate_failure_resume(state, digest(receipt), store=self.store, now=self.old.now)
        self.assertEqual(self.snapshot(), self.before_database)
        self.assertEqual(state["status"], "stopped")

    def test_same_day_revision_and_uncapped_rollover_preserve_zero_and_unknown_receipts(self):
        from menu_worker.bounded_generation import write_json
        from menu_worker.bounded_resume import digest
        from menu_worker.daily_limits import build_limit_revision, validate_limit_revisions
        from menu_worker.daily_resume import (activate_plan, build_plan, preflight_plan,
                                              resume_failure_recovery, validate_failure_resume)
        from menu_worker.failure_recovery import validate_recovery_record

        _, receipt, state = self.apply()
        records = deepcopy(state["records"])
        audit = deepcopy(state["failure_recovery_audit"])
        revision = build_limit_revision(state, reason="Approved daily twenty passes without an attempt cap",
            approved_at="2026-11-17T11:00:00+09:00")
        state["daily_limit_revisions"] = [revision]
        validate_limit_revisions(state)
        self.assertEqual(state["records"], records)
        self.assertEqual(state["failure_recovery_audit"], audit)
        validate_recovery_record(state["records"][-1], store=self.store, artifact_root=self.root)
        self.assertEqual(validate_failure_resume(state, digest(receipt), store=self.store, now=self.old.now), receipt)
        verify_completed_history(self.store, state, self.old.read_result, rag_store=self.rag,
            rag_read_result=lambda _: self.fail("Old zero-cost failure cannot generate again"))
        self.assertEqual(self.snapshot(), self.before_database)

        next_now = self.old.now + 86400
        resumed = resume_failure_recovery(deepcopy(state), digest(receipt), store=self.store, now=next_now)
        self.assertEqual((resumed["status"], resumed["stop_reason"]), ("stopped", "deadline_reached"))
        write_json(self.checkpoint, resumed)
        inbox = self.root / "uncapped-next-day-inbox"
        inbox.mkdir()
        next_plan = build_plan(source=self.checkpoint,
            source_sha256=hashlib.sha256(self.checkpoint.read_bytes()).hexdigest(),
            checkpoint=self.root / "uncapped-next-day.json", policy_date="2026-11-18",
            delivery_root=self.root / "inbox", now=next_now,
            routes=self.old.plan["daily_policy"]["routes"], rag_scope=state["rag_scope"],
            rag_scope_sha256=state["rag_scope_sha256"], rag_delivery_root=inbox,
            rag_runtime_sha256="a" * 64, success_target_only=True)
        self.assertEqual(next_plan["daily_policy"]["schema_version"], 4)
        self.assertIsNone(next_plan["daily_policy"]["max_attempts"])
        self.assertIsNone(next_plan["config"]["max_new_jobs"])
        self.assertEqual(next_plan["daily_policy"]["success_target"], 20)
        self.assertEqual([row["job_id"] for row in next_plan["daily_policy"]["prior_unknown_costs"]],
                         [self.old.failed["job_id"]])
        options = {"store": self.store, "read_result": self.old.read_result, "rag_store": self.rag,
            "rag_read_result": lambda _: self.fail("No RAG generation result exists"), "now": next_now}
        preflight_plan(next_plan, **options)
        next_state, _ = activate_plan(next_plan, **options)
        self.assertEqual(next_state["records"], records)
        self.assertEqual(next_state["failure_recovery_audit"], audit)
        self.assertEqual(next_state["daily_limit_revisions"], [revision])
        validate_limit_revisions(next_state)
        validate_recovery_record(next_state["records"][-1], store=self.store, artifact_root=self.root)
        verify_completed_history(self.store, next_state, self.old.read_result, rag_store=self.rag,
            rag_read_result=lambda _: self.fail("Never regenerate old failed work"))
        self.assertEqual(self.snapshot(), self.before_database)
        self.assertEqual(self.rag.get(self.job["job_id"]), self.job)

    def test_started_or_unknown_cost_job_cannot_be_planned_as_free(self):
        cases = (("generation_started_at", "2026-11-17T01:01:00+00:00"),
                 ("cost_complete", False), ("cost_complete", None))
        for field, value in cases:
            with self.subTest(field=field, value=value):
                with self.store._connect() as connection:
                    connection.execute(f"UPDATE content_rag_generation_job SET {field}=%s WHERE job_id=%s",
                                       (value, self.job["job_id"]))
                with self.assertRaises(StopRun):
                    self.plan()
                self.assertEqual(self.checkpoint.read_bytes(), self.before_bytes)
                with self.store._connect() as connection:
                    connection.execute(f"UPDATE content_rag_generation_job SET {field}=%s WHERE job_id=%s",
                                       (self.job[field], self.job["job_id"]))

    def test_launch_artifact_appearing_after_plan_blocks_apply(self):
        plan = self.plan()
        directory = self.root / "rag/jobs" / self.job["job_id"] / "attempts" / self.job["generation_attempt_id"]
        directory.mkdir(parents=True)
        (directory / "launch.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(StopRun):
            self.apply(plan)
        self.assertEqual(self.checkpoint.read_bytes(), self.before_bytes)
        self.assertEqual(self.snapshot(), self.before_database)


if __name__ == "__main__":
    unittest.main()
