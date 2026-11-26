"""Preserve v7 recovery evidence through additive v8 migration in isolated PG."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_held_recovery_postgres as held_fixture
import test_jobs_migration_postgres as migration_fixture
from test_rag_contract import request, receipt
from menu_worker import failure_recovery, job_store
from menu_worker.bounded_generation import StopRun, audit_existing_jobs, read_json, write_json
from menu_worker.bounded_resume import digest, verify_completed_history
from menu_worker.daily_resume import resume_failure_recovery
from menu_worker.rag_store import RagStore


@unittest.skipUnless(os.environ.get(migration_fixture.ADMIN_ENV), "Requires isolated jobs-db migration fixture")
class RecoverySchemaCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.f = held_fixture.HeldRecoveryPostgresTests()
        self.addCleanup(self.f.doCleanups)
        # Reproduce the original v7 runtime's setup and its period-only audit.
        # Actual migrations, rows, sealed held output and recovery receipts are real.
        with patch.object(job_store, "MIGRATIONS", job_store.MIGRATIONS[:7]), \
                patch.object(job_store, "SCHEMA_VERSION", 7), \
                patch("menu_worker.rag_daily_runtime.audit_rag_history", return_value=None):
            self.f.setUp()
        self.rag = RagStore(self.f.fixture.dsn, expected_database=migration_fixture.MIGRATION_DB)

    def upgrade(self):
        self.assertEqual(self.f.store.initialize()["version"], 8)

    def test_v7_failed_and_held_receipts_survive_v8_and_new_controller_guard(self):
        _, held_receipt, state = self.f.apply_held()
        originals = self.f.all_files()
        old_jobs = {row["job_id"]: self.f.store.get(row["job_id"]) for row in state["records"]}
        for reference in state["failure_recovery_audit"]:
            evidence = read_json(Path(reference["receipt_path"]))
            backup = read_json(Path(evidence["database_backup"]))
            self.assertEqual([row["version"] for row in backup["menu_worker_schema"]], [7])
            self.assertEqual(set(backup), set(failure_recovery.TABLES))
        self.upgrade()
        self.assertEqual(self.f.all_files(), originals)
        self.assertEqual({key: self.f.store.get(key) for key in old_jobs}, old_jobs)
        self.assertEqual(len(failure_recovery.validate_recovery_history(state,
            store=self.f.store, artifact_root=self.f.root)), 2)
        verify_completed_history(self.f.store, state, self.f.read_result)
        authorized = resume_failure_recovery(state, digest(held_receipt), store=self.f.store, now=self.f.now)
        write_json(self.f.child, authorized)  # Isolated no-provider fixture only.
        def guard():
            failure_recovery.validate_recovery_history(controller.state, store=self.f.store, artifact_root=self.f.root)
            audit_existing_jobs(self.f.store, controller.state)
        controller = self.f.controller(lock_check=guard)
        controller.check()
        self.assertTrue(controller.validate_run_history())
        summary = controller.summary()
        self.assertEqual(summary["unknown_cost_jobs"], [self.f.prior_failure["job_id"], self.f.held["job_id"]])
        self.assertIsNone(summary["estimated_total_cost_usd"])
        self.assertEqual(authorized["records"], state["records"])
        for name, data in originals.items():
            if name != self.f.child.relative_to(self.f.root).as_posix():
                self.assertEqual((self.f.root / name).read_bytes(), data, name)

    def test_v8_snapshot_preserves_rag_rows_and_blocks_queued_or_running_rag(self):
        self.upgrade()
        raw = request()
        job = self.rag.submit(raw, receipt(raw))
        before = self.f.all_files()
        with self.assertRaisesRegex(StopRun, "recovery_database_not_idle"):
            self.f.held_plan()
        claimed = self.rag.claim(job["job_id"])
        with self.assertRaisesRegex(StopRun, "recovery_database_not_idle"):
            self.f.held_plan()
        self.assertEqual(self.f.all_files(), before)
        self.assertTrue(self.rag.complete(job["job_id"], claimed["claim_token"],
            execution_status="failed", usage_records=[], cost_complete=True, error_code="offline_preflight"))
        prior_rag = self.rag.get(job["job_id"])
        _, evidence, state = self.f.apply_held()
        backup = read_json(Path(evidence["database_backup"]))
        self.assertEqual(set(backup), set(failure_recovery.TABLES + failure_recovery.RAG_TABLES))
        self.assertEqual([row["version"] for row in backup["menu_worker_schema"]], [8])
        self.assertEqual(len(backup["content_rag_generation_job"]), 1)
        self.assertEqual(len(backup["content_rag_generation_attempt"]), 1)
        self.assertEqual(self.rag.get(job["job_id"]), prior_rag)
        self.assertEqual(len(failure_recovery.validate_recovery_history(state,
            store=self.f.store, artifact_root=self.f.root)), 2)

    def test_v8_recovery_locks_both_rag_tables_before_writing_evidence(self):
        self.upgrade()
        plan = self.f.held_plan()
        original_files = self.f.all_files()
        for table in failure_recovery.RAG_TABLES:
            with self.subTest(table=table), self.f.fixture.connect() as writer:
                writer.execute("LOCK TABLE public." + table + " IN ROW EXCLUSIVE MODE")
                with self.assertRaises(self.f.fixture.psycopg.errors.LockNotAvailable):
                    self.f.apply_held(plan)
            self.assertEqual(self.f.all_files(), original_files)

    def test_marker_and_complete_table_set_must_match(self):
        with self.f.fixture.connect() as connection:
            connection.execute("ALTER TABLE public.menu_worker_schema DROP CONSTRAINT menu_worker_schema_version_check")
            connection.execute("UPDATE public.menu_worker_schema SET version=8")
            with self.assertRaisesRegex(StopRun, "unexpected_recovery_database_schema"):
                failure_recovery._snapshot(connection)
            connection.rollback()
            connection.execute("CREATE TABLE public.unexpected_fixture(value integer)")
            with self.assertRaisesRegex(StopRun, "unexpected_recovery_database_schema"):
                failure_recovery._snapshot(connection)
            connection.rollback()
        self.upgrade()
        with self.f.fixture.connect() as connection:
            connection.execute("ALTER TABLE public.menu_worker_schema DROP CONSTRAINT menu_worker_schema_version_check")
            connection.execute("UPDATE public.menu_worker_schema SET version=7")
            with self.assertRaisesRegex(StopRun, "unexpected_recovery_database_schema"):
                failure_recovery._snapshot(connection)
            connection.rollback()
            connection.execute("ALTER TABLE public.content_rag_generation_attempt RENAME TO missing_rag_attempt_fixture")
            with self.assertRaisesRegex(StopRun, "unexpected_recovery_database_schema"):
                failure_recovery._snapshot(connection)
            connection.rollback()
