"""Real isolated PG history audit; sealed offline fixtures, no provider calls."""
from copy import deepcopy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import test_jobs_migration_postgres as database_fixture
from test_rag_contract import request, receipt
import test_rag_jobs_postgres as native_fixture
from menu_worker.bounded_generation import StopRun, audit_existing_jobs
from menu_worker.bounded_resume import verify_completed_history
from menu_worker.rag_contract import RagRequest
from menu_worker.rag_store import RagStore
from menu_worker.rag_tasks import execute_delivery, read_sealed_result


@unittest.skipUnless(os.environ.get(database_fixture.ADMIN_ENV), "Requires isolated jobs-db migration fixture")
class RagHistoryPostgresTests(unittest.TestCase):
    def setUp(self):
        self.fixture = database_fixture.JobSchemaMigrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.rag = RagStore(self.fixture.dsn, expected_database=database_fixture.MIGRATION_DB)
        self.rag.initialize()
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.period_request = {"idempotency_key": "mixed-pg-review:period-original", "source_id": "one_piece_official",
            "source_url": "https://one-piece.com/news/83501/index.html", "content_type": "goods", "menu_work_id": 8}
        job = self.rag.jobs.submit(self.period_request, job_kind="anime_generate")
        # Preserve a harmless pre-launch failure as the existing period history.
        with self.fixture.connect() as connection:
            connection.execute("UPDATE content_generation_job SET execution_status='failed' WHERE job_id=%s",
                               (job["job_id"],))
        self.state = {"config": {"run_id": "mixed-pg-review", "max_new_jobs": 40,
                                "artifact_root": str(self.root)},
            "records": [{"job_id": job["job_id"], "request": self.period_request,
                "canonical_url": job["canonical_url"], "state": "complete", "counts_slot": True,
                "execution_status": "failed", "estimated_cost_usd": "0.00000000"}]}

    def add_held_rag(self):
        item = RagRequest.parse(request())
        admission = receipt(item.as_dict(), attempt_ordinal=2)
        submitted = self.rag.submit(item, admission)
        def produce(claimed, pulse):
            pulse()
            return {**native_fixture.RagPostgresTests.native_result(claimed), "usage_summary": {"llm_calls": 0}}
        report = execute_delivery(self.rag, self.root, submitted["job_id"], run_native=produce,
                                  preflight=lambda _: None, enabled=True, now=lambda: native_fixture.FIXED_NOW)
        self.assertEqual(report["execution_status"], "succeeded")
        job = self.rag.get(submitted["job_id"])
        row = {key: job[key] for key in ("job_id", "generation_attempt_id", "result_sha256",
                                       "execution_status", "outcome", "producer_status")}
        row.update(producer="ani_rag", request={"rag_request": item.as_dict()},
            canonical_url="urn:menu-rag:" + item.identity_key, rag_input_sha256=item.input_sha256,
            rag_admission=admission, state="complete", counts_slot=True, estimated_cost_usd="0.00000000",
            rag_usage_records=[])
        self.state["records"].append(row)
        return row

    def verify(self, state=None):
        return verify_completed_history(self.rag.jobs, state or self.state,
            lambda _: self.fail("Pre-launch period failure must not read or regenerate a result"),
            rag_store=self.rag, rag_read_result=lambda job: read_sealed_result(self.root, job))

    def test_period_history_passes_with_empty_rag_ledger(self):
        original = deepcopy(self.state)
        audit_existing_jobs(self.rag.jobs, self.state)
        self.assertEqual(self.verify(), {})
        self.assertEqual(self.state, original)

    def test_mixed_actual_rows_and_native_seals_preserve_both_histories(self):
        self.add_held_rag()
        original = deepcopy(self.state)
        before = self.rag.get(self.state["records"][-1]["job_id"])
        audit_existing_jobs(self.rag.jobs, self.state)
        self.assertEqual(self.verify(), {})
        self.assertEqual(self.state, original)
        self.assertEqual(self.rag.get(before["job_id"]), before)
        self.assertEqual(len(before["attempts"]), 1)

    def test_period_only_rewind_and_combined_cap_are_blocked(self):
        old_period_only = deepcopy(self.state)
        self.add_held_rag()
        with self.assertRaisesRegex(StopRun, "rag_database_history_not_owned_by_checkpoint"):
            audit_existing_jobs(self.rag.jobs, old_period_only)
        reduced = deepcopy(self.state)
        reduced["config"]["max_new_jobs"] = 1
        with self.assertRaisesRegex(StopRun, "database_job_cap_already_exceeded"):
            audit_existing_jobs(self.rag.jobs, reduced)

    def test_changed_receipt_cost_or_native_result_stops_history_reuse(self):
        row = self.add_held_rag()
        changed = deepcopy(self.state)
        changed["records"][-1]["rag_admission"]["attempt_ordinal"] = 3
        with self.assertRaisesRegex(StopRun, "rag_database_history_changed"):
            self.verify(changed)
        changed = deepcopy(self.state)
        changed["records"][-1]["estimated_cost_usd"] = "0.01"
        with self.assertRaisesRegex(StopRun, "rag_completed_cost_changed"):
            self.verify(changed)
        job = self.rag.get(row["job_id"])
        result = (self.root / job["artifact_path"]).with_name("result.json")
        result.write_bytes(result.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "rag_artifact_changed"):
            self.verify()
