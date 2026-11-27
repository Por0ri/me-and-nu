"""No providers: strict failed-attempt completion and historical revalidation."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock
from uuid import uuid4

import test_rag_daily as daily
from menu_worker.bounded_generation import StopRun
from menu_worker.bounded_resume import digest
from menu_worker.rag_contract import RagRequest
from menu_worker.rag_daily_runtime import verify_rag_completion
from menu_worker.rag_preflight_failure import complete_zero_preflight_record, verify_zero_preflight_completion


class ZeroPreflightFailureTests(unittest.TestCase):
    def setUp(self):
        self.base = daily.MixedDailyTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.controller = self.base.controller()
        self.root = Path(self.controller.config["artifact_root"])
        self.record = self.controller.reserve_rag(self.controller.collect_rag()[0])
        request = RagRequest.parse(self.record["request"]["rag_request"])
        receipt = self.record["rag_admission"]
        job_id, attempt_id = str(uuid4()), str(uuid4())
        finished = {"execution_status": "failed", "error_code": "rag_preflight_failed",
                    "generation_started_at": None, "outcome": None, "producer_status": None,
                    "result_sha256": None, "artifact_path": None, "usage_records": [],
                    "cost_complete": True, "finished_at": "2026-11-24T01:00:02+00:00"}
        attempt = {**deepcopy(finished), "attempt_id": attempt_id, "job_id": job_id,
                   "claimed_at": "2026-11-24T01:00:01+00:00"}
        self.job = {**deepcopy(finished), "job_id": job_id, "generation_attempt_id": attempt_id,
            "namespace": request.namespace, "work_id": request.work_id, "identity_key": request.identity_key,
            "request": request.as_dict(), "input_sha256": request.input_sha256,
            "reservation_id": receipt["reservation_id"], "admission_receipt": deepcopy(receipt),
            "admission_sha256": digest(receipt), "claim_token": None, "lease_expires_at": None,
            "created_at": "2026-11-24T01:00:00+00:00", "attempts": [attempt]}
        self.record.update(job_id=job_id, publish_intent=True, published=True,
                           published_at=self.controller.now())
        self.base.rag.rows[job_id] = self.job
        self.controller.save()
        self.controller.rag_read_result = Mock(side_effect=AssertionError("No generated result exists"))
        self.controller.rag_export = Mock(side_effect=AssertionError("Failed result cannot export"))

    def verify(self, *, state=None, record=None, job=None):
        verify_zero_preflight_completion(state or self.controller.state, record or self.record,
                                        job or self.job, artifact_root=self.root)

    def test_confirmed_failure_finishes_one_slot_without_changing_ledger_or_history(self):
        before, job_before = deepcopy(self.controller.state), deepcopy(self.job)
        self.controller.finish_job(self.record)
        self.assertEqual(self.job, job_before)
        self.assertEqual(self.controller.state["records"][:-1], before["records"][:-1])
        self.assertEqual(self.controller.state["seen"], before["seen"])
        self.assertTrue(self.record["counts_slot"])
        self.assertEqual(self.record["state"], "complete")
        self.assertEqual(self.record["estimated_cost_usd"], "0.00000000")
        self.assertEqual(self.record["rag_usage_records"], [])
        self.assertEqual((self.controller.summary()["daily_attempts"],
                          self.controller.summary()["daily_successes"]), (1, 0))
        self.verify()
        verify_rag_completion(self.record, self.base.rag, self.controller.rag_read_result,
                              state=self.controller.state, artifact_root=self.root)
        self.controller.rag_read_result.assert_not_called()
        self.controller.rag_export.assert_not_called()
        finished = deepcopy(self.record)
        self.controller.finish_job(self.record)
        self.assertEqual(self.record, finished)
        self.assertNotIn(self.record["canonical_url"], [r["canonical_url"] for r in self.controller.collect_rag()])

    def test_ambiguous_job_or_attempt_cannot_be_completed(self):
        cases = [(key, value) for key, value in (
            ("generation_started_at", "2026-11-24T01:00:01+00:00"),
            ("execution_status", "needs_attention"), ("error_code", "RAG_LEASE_EXPIRED_BEFORE_GENERATION"),
            ("cost_complete", False), ("cost_complete", None), ("usage_records", None),
            ("usage_records", [{"estimated_cost_usd": "0"}]), ("finished_at", None),
            ("outcome", "held"), ("producer_status", "rejected"),
            ("result_sha256", "a" * 64), ("artifact_path", "unrecorded.json"))]
        for at_attempt in (False, True):
            for key, value in cases:
                with self.subTest(at_attempt=at_attempt, key=key, value=value):
                    job = deepcopy(self.job)
                    (job["attempts"][0] if at_attempt else job)[key] = value
                    with self.assertRaises(StopRun):
                        complete_zero_preflight_record(self.controller.state, self.record, job, artifact_root=self.root)
        for key, value in (("claim_token", str(uuid4())), ("lease_expires_at", "2026-11-24T01:01:00+00:00"),
                           ("attempts", []), ("attempts", self.job["attempts"] * 2),
                           ("admission_sha256", "b" * 64), ("reservation_id", str(uuid4())),
                           ("input_sha256", "b" * 64)):
            with self.subTest(key=key), self.assertRaises(StopRun):
                complete_zero_preflight_record(self.controller.state, self.record, {**self.job, key: value}, artifact_root=self.root)

    def test_missing_null_markers_and_artifact_directory_fail_closed(self):
        for key in ("generation_started_at", "artifact_path", "result_sha256", "claim_token", "lease_expires_at"):
            changed = deepcopy(self.job)
            changed.pop(key)
            with self.subTest(key=key), self.assertRaises(StopRun):
                complete_zero_preflight_record(self.controller.state, self.record, changed, artifact_root=self.root)
        directory = self.root / "rag/jobs" / self.job["job_id"]
        directory.mkdir(parents=True)
        with self.assertRaises(StopRun):
            self.controller.finish_job(self.record)
        self.assertEqual(self.record["state"], "reserved")

    def test_conflicting_checkpoint_cost_flags_and_delivery_evidence_are_preserved(self):
        self.record["estimated_cost_usd"] = "0.00000000"
        for key, value in (("cost_unknown", True), ("rag_cost_incomplete", True),
                           ("generation_started_at", "2026-11-24T01:00:01+00:00"),
                           ("delivery_envelope_path", "/unexpected/delivery.json"),
                           ("delivery_envelope_sha256", "c" * 64)):
            state = deepcopy(self.controller.state)
            record = state["records"][-1]
            record[key] = value
            before, job_before = deepcopy(record), deepcopy(self.job)
            with self.subTest(key=key), self.assertRaises(StopRun):
                complete_zero_preflight_record(state, record, self.job, artifact_root=self.root)
            self.assertEqual(record, before)
            self.assertEqual(self.job, job_before)

    def test_finished_evidence_digest_and_exact_zero_are_rechecked_on_restart(self):
        self.controller.finish_job(self.record)
        changed = deepcopy(self.job)
        changed["finished_at"] = "2026-11-24T01:00:03+00:00"
        with self.assertRaises(StopRun):
            self.verify(job=changed)
        self.base.rag.rows[self.job["job_id"]] = changed
        with self.assertRaises(StopRun):
            self.controller.validate_run_history()
        self.base.rag.rows[self.job["job_id"]] = self.job
        for key, value in (("estimated_cost_usd", "0.01"), ("rag_usage_records", None),
                           ("counts_slot", False), ("rag_cost_incomplete", True), ("cost_unknown", True),
                           ("rag_preflight_failure", None)):
            state = deepcopy(self.controller.state)
            record = state["records"][-1]
            record[key] = value
            with self.subTest(key=key), self.assertRaises(StopRun):
                self.verify(state=state, record=record)

    def test_completed_failure_remains_valid_under_next_days_policy(self):
        self.controller.finish_job(self.record)
        state = deepcopy(self.controller.state)
        tomorrow = {**deepcopy(state["daily_policy"]), "policy_date": "2026-11-25",
                    "start_record_index": len(state["records"])}
        state["daily_policies"].append(tomorrow)
        state["daily_policy"] = tomorrow
        self.verify(state=state, record=state["records"][-1])
        state["daily_policies"][0]["success_target"] = 21
        with self.assertRaises(StopRun):
            self.verify(state=state, record=state["records"][-1])

    def test_zero_cost_failures_consume_all_forty_slots_without_retry_or_success(self):
        self.controller.finish_job(self.record)
        first_job = deepcopy(self.job)
        self.controller.discover = lambda *_: {"articles": [], "discovered": 0}
        published = []
        self.controller.rag_publish = published.append

        def submit(request, receipt):
            job = deepcopy(self.job)
            job_id, attempt_id = str(uuid4()), str(uuid4())
            job.update(job_id=job_id, generation_attempt_id=attempt_id,
                       namespace=request.namespace, work_id=request.work_id,
                       identity_key=request.identity_key, request=request.as_dict(),
                       input_sha256=request.input_sha256, reservation_id=receipt["reservation_id"],
                       admission_receipt=deepcopy(receipt), admission_sha256=digest(receipt))
            job["attempts"][0].update(job_id=job_id, attempt_id=attempt_id)
            self.base.rag.rows[job_id] = job
            return deepcopy(job) | {"created": True}
        self.base.rag.submit = submit
        report = self.controller.run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (40, 0))
        self.assertEqual(report["stop_reason"], "daily_attempt_limit_reached")
        self.assertEqual(len(published), 39)
        self.assertEqual(len(set(published)), 39)
        self.assertNotIn(self.record["job_id"], published)
        self.assertEqual(self.base.rag.rows[self.record["job_id"]], first_job)
        before = list(published)
        self.controller.run()
        self.assertEqual(published, before)


if __name__ == "__main__":
    unittest.main()
