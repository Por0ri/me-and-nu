"""Cover history is verified before launch and bound to the actual attempt."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from menu_worker import cover_history as history, job_runner, real_generation as real
from menu_worker.job_store import JobStoreError
from menu_worker.job_artifacts import artifact_directory, sha256


class CoverHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.job = {"job_id": str(uuid4()), "generation_attempt_id": str(uuid4())}
        self.previous = {"job_id": str(uuid4()), "generation_attempt_id": str(uuid4()),
            "job_kind": "anime_generate", "menu_work_id": 8, "input_sha256": "a" * 64,
            "source_id": "one_piece_official", "content_type": "goods",
            "canonical_url": "https://one-piece.com/news/82812/index.html"}
        self.package = {"request_id": "crawl-previous", "menu_work_ids": [8], "content_type": "goods",
            "sources": [{"id": "one_piece_official", "url": self.previous["canonical_url"]}],
            "cards": [{"page": 1, "image_id": "cover"}],
            "images": [{"id": "cover", "url": "https://one-piece.com/assets/one.jpg",
                "source_url": self.previous["canonical_url"], "fingerprint": "b" * 64}]}
        folder = artifact_directory(self.root, self.previous, create=True)
        (folder / "launch.json").write_bytes(real._dump(real._identity(self.previous)))
        (folder / "result.json").write_bytes(real._dump({"status": "rejected_archived",
            "expected_work_request": real._request(self.previous), "editorial_decision": "rejected", "package": self.package}))
        self.previous.update(real.validate_real_artifacts(self.root, self.previous))

    def test_rejected_cover_and_original_identity_are_passed_without_mutation(self):
        folder = artifact_directory(self.root, self.previous)
        before = {p.name: p.read_bytes() for p in folder.iterdir()}
        data = history.build_snapshot(self.root, self.job, [self.previous])
        document = history.validate_snapshot(data, self.job)
        self.assertEqual(document["entries"][0]["image"], self.package["images"][0])
        self.assertEqual(document["entries"][0]["origin"]["generation_attempt_id"], self.previous["generation_attempt_id"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})

    def test_tampered_or_missing_completed_result_blocks_new_snapshot(self):
        folder = artifact_directory(self.root, self.previous)
        (folder / "result.json").write_bytes(b"{}")
        with self.assertRaisesRegex(real.RealGenerationError, "cover_history_invalid"):
            history.build_snapshot(self.root, self.job, [self.previous])
        (folder / "result.json").unlink()
        with self.assertRaisesRegex(real.RealGenerationError, "cover_history_invalid"):
            history.build_snapshot(self.root, self.job, [self.previous])

    def test_bootstrap_is_pinned_deduplicated_and_rebound(self):
        data = history.build_snapshot(self.root, self.job, [self.previous])
        seed = self.root / "bootstrap.json"
        seed.write_bytes(data)
        with patch.dict(os.environ, {"ANIME_COVER_HISTORY_BOOTSTRAP_FILE": str(seed),
                                    "ANIME_COVER_HISTORY_BOOTSTRAP_SHA256": sha256(data)}):
            other = {"job_id": str(uuid4()), "generation_attempt_id": str(uuid4())}
            value = history.validate_snapshot(history.build_snapshot(self.root, other, [self.previous]), other)
            self.assertEqual(len(value["entries"]), 1)
            seed.write_bytes(b"{}")
            with self.assertRaisesRegex(real.RealGenerationError, "cover_history_invalid"):
                history.build_snapshot(self.root, other, [])

    def test_partial_bootstrap_and_other_attempt_binding_are_rejected(self):
        with patch.dict(os.environ, {"ANIME_COVER_HISTORY_BOOTSTRAP_FILE": str(self.root / "missing")}):
            with self.assertRaisesRegex(real.RealGenerationError, "cover_history_invalid"):
                history.build_snapshot(self.root, self.job, [])
        data = history.build_snapshot(self.root, self.job, [])
        with self.assertRaisesRegex(real.RealGenerationError, "cover_history_invalid"):
            history.validate_snapshot(data, {**self.job, "generation_attempt_id": str(uuid4())})

    def test_cover_only_and_editorial_graphic_are_distinguished(self):
        package = {**self.package, "cards": [{"page": 2, "image_id": "absent"}, self.package["cards"][0]]}
        self.assertEqual(history.cover_entry(package, {})["image"]["id"], "cover")
        self.assertIsNone(history.cover_entry({"cards": [{"page": 1, "visual_mode": "editorial_graphic"}]}, {}))
        with self.assertRaisesRegex(real.RealGenerationError, "cover_history_invalid"):
            history.cover_entry({"cards": [{"page": 1, "image_id": "missing"}]}, {})

    def test_guard_contention_requeues_exact_delivery_without_claim(self):
        store = Mock()
        store.generation_history_guard.return_value = nullcontext(None)
        report = job_runner.run_job(store, self.root, self.job["job_id"], "receipt", expected_kind="anime_generate")
        self.assertEqual(report["reason"], "generation_history_busy")
        store.requeue_delivery.assert_called_once_with(self.job["job_id"], "receipt", expected_kind="anime_generate")
        store.claim.assert_not_called()

    def test_unavailable_guard_rejects_without_acknowledging_delivery(self):
        from celery.exceptions import Reject
        from menu_worker import job_store
        store = Mock()
        guard = Mock()
        guard.__enter__ = Mock(side_effect=JobStoreError("cover_history_unavailable"))
        guard.__exit__ = Mock()
        store.generation_history_guard.return_value = guard
        with (patch.object(job_store, "JobStore", return_value=store), patch.object(job_runner.time, "sleep"),
              patch.dict(os.environ, {"CONTENT_JOB_KIND": "anime_generate", "CONTENT_JOB_ARTIFACT_ROOT": str(self.root)}),
              self.assertRaises(Reject) as rejected):
            job_runner.execute_delivery(self.job["job_id"], "receipt", "offline", expected_kind="anime_generate")
        self.assertTrue(rejected.exception.requeue)
        store.claim.assert_not_called()

    def test_busy_receipt_reset_failure_also_keeps_broker_delivery(self):
        store = Mock()
        store.generation_history_guard.return_value = nullcontext(None)
        store.requeue_delivery.side_effect = RuntimeError("offline DB failure")
        with self.assertRaises(job_runner.HistoryAdmissionUnavailable):
            job_runner.run_job(store, self.root, self.job["job_id"], "receipt", expected_kind="anime_generate")
        store.claim.assert_not_called()

    def test_previous_running_generation_defers_a_new_claim_after_lock_loss(self):
        store = Mock()
        store.generation_history_guard.return_value = nullcontext(lambda: None)
        store.generation_history_busy.return_value = True
        result = job_runner.run_job(store, self.root, self.job["job_id"], "receipt", expected_kind="anime_generate")
        self.assertEqual(result["reason"], "generation_history_busy")
        store.claim.assert_not_called()
        store.requeue_delivery.assert_called_once()

    def test_failed_history_query_is_recorded_before_generation_start(self):
        job = {**self.previous, "claim_token": str(uuid4()), "attempt_count": 1,
            "idempotency_key": "history-preflight", "source_url": self.previous["canonical_url"],
            "candidate_url": self.previous["canonical_url"]}
        store = Mock()
        store.cover_history_sources.side_effect = JobStoreError("cover_history_invalid")
        store.retry_failure.return_value = None
        store.get.return_value = {"execution_status": "failed"}
        with (patch.object(job_runner, "validate_job_request", return_value=(None, job["candidate_url"], None)),
              patch.object(job_runner, "job_input_hash", return_value=job["input_sha256"]),
              patch.object(job_runner, "preflight_real"), patch.object(job_runner, "generate_real") as generate):
            report, _ = job_runner._run_attempt(store, self.root, job, recover=False,
                expected_kind="anime_generate", retry_failures=True, retry_delay=0, history_check=lambda: None)
        # Mock retry must not create an unrelated next attempt for a nonretryable configuration error.
        store.mark_generation_started.assert_not_called()
        generate.assert_not_called()
        self.assertEqual(store.complete.call_args.kwargs["error_code"], "cover_history_invalid")
        self.assertTrue(store.complete.call_args.kwargs["agent_not_launched"])


if __name__ == "__main__":
    unittest.main()
