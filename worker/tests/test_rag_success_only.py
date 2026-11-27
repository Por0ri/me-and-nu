"""No provider: unlimited admissions retain preflight failures and 20 passes."""
from copy import deepcopy
import unittest
from uuid import uuid4

import test_rag_preflight_failure as zero
from menu_worker.bounded_resume import digest
from menu_worker.daily_limits import build_limit_revision, validate_limit_revisions
from menu_worker.daily_resume import validate_policy_history
from menu_worker.rag_contract import RagContractError, RagRequest, validate_admission_receipt
from menu_worker.rag_preflight_failure import verify_zero_preflight_completion


class RagSuccessOnlyTests(unittest.TestCase):
    def fixture(self):
        base = zero.ZeroPreflightFailureTests()
        base.setUp()
        self.addCleanup(base.doCleanups)
        return base

    def test_forty_zero_failures_then_first_success_at_41_and_twenty_total(self):
        base = self.fixture()
        controller = base.controller
        controller.finish_job(base.record)
        original_record, original_job = deepcopy(base.record), deepcopy(base.job)
        controller.state.update(status="stopped", stop_reason="operator_paused")
        revision = build_limit_revision(controller.state, reason="Approved success target without attempt cap",
            approved_at="2026-11-24T11:00:00+09:00")
        controller.state["daily_limit_revisions"] = [revision]
        controller.state.update(status="running", stop_reason=None)
        controller.save()
        controller.discover = lambda *_: {"articles": [], "discovered": 0}
        published = []
        controller.rag_publish = published.append
        controller.rag_read_result = base.base.rag.result
        controller.rag_export = base.base.rag.export

        def submit(request, receipt):
            validate_admission_receipt(receipt, request)
            job = deepcopy(base.job)
            job_id, attempt_id = str(uuid4()), str(uuid4())
            job.update(job_id=job_id, generation_attempt_id=attempt_id,
                namespace=request.namespace, work_id=request.work_id, identity_key=request.identity_key,
                request=request.as_dict(), input_sha256=request.input_sha256,
                reservation_id=receipt["reservation_id"], admission_receipt=deepcopy(receipt), admission_sha256=digest(receipt))
            job["attempts"][0].update(job_id=job_id, attempt_id=attempt_id)
            if receipt["attempt_ordinal"] >= 41:
                job.update(execution_status="succeeded", outcome="ready_for_staging", error_code=None,
                    result_sha256="b" * 64, usage_records=[{"estimated_cost_usd": "0.02"}])
            base.base.rag.rows[job_id] = job
            return deepcopy(job) | {"created": True}
        base.base.rag.submit = submit
        report = controller.run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (60, 20))
        self.assertEqual(report["stop_reason"], "daily_success_target_reached")
        self.assertIsNone(report["daily_attempt_limit"])
        self.assertEqual(base.record, original_record)
        self.assertEqual(base.base.rag.rows[base.record["job_id"]], original_job)
        self.assertEqual(len(published), len(set(published)))
        self.assertEqual(len(published), 59)
        self.assertNotIn(base.record["job_id"], published)
        rows = controller.state["records"][4:]
        self.assertTrue(all(row["execution_status"] == "failed" for row in rows[:40]))
        self.assertEqual(rows[40]["outcome"], "ready_for_staging")
        self.assertEqual(rows[40]["rag_admission"]["attempt_ordinal"], 41)
        self.assertEqual(rows[0]["rag_admission"]["schema_version"], 1)
        self.assertTrue(all(row["rag_admission"]["schema_version"] == 2 for row in rows[1:]))
        self.assertTrue(all(row["rag_admission"]["limit_revision_sha256"] == digest(revision) for row in rows[1:]))
        validate_limit_revisions(controller.state)
        validate_policy_history(controller.state)
        for row in rows[:40]:
            verify_zero_preflight_completion(controller.state, row, base.base.rag.rows[row["job_id"]],
                artifact_root=base.root)
        controller.run()
        self.assertEqual(len(published), 59)

    def test_v1_cap_stays_strict_and_v2_requires_explicit_revision_field(self):
        base = self.fixture()
        request = RagRequest.parse(base.record["request"]["rag_request"])
        receipt = deepcopy(base.record["rag_admission"])
        receipt["attempt_ordinal"] = 41
        with self.assertRaises(RagContractError):
            validate_admission_receipt(receipt, request)
        receipt["schema_version"] = 2
        with self.assertRaises(RagContractError):
            validate_admission_receipt(receipt, request)
        receipt["limit_revision_sha256"] = None
        self.assertEqual(validate_admission_receipt(receipt, request), receipt)
        for ordinal in (0, -1, True, 1.5):
            with self.subTest(ordinal=ordinal), self.assertRaises(RagContractError):
                validate_admission_receipt({**receipt, "attempt_ordinal": ordinal}, request)


if __name__ == "__main__":
    unittest.main()
