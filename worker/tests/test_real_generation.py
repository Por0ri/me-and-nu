"""Verify worker/producer boundaries with local bytes and a substituted child process."""
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from menu_worker import real_generation as real
from menu_worker.job_artifacts import artifact_directory, sha256


class RealGenerationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.job = {"job_id": str(uuid4()), "generation_attempt_id": str(uuid4()), "job_kind": "anime_generate",
                    "input_sha256": "c" * 64, "source_id": "one_piece_official", "content_type": "goods",
                    "menu_work_id": 8, "canonical_url": "https://one-piece.com/news/82812/index.html"}
        key = self.root / "secret"
        key.write_text("sk-test-offline-only-no-provider-call\n", encoding="ascii")
        self.enterContext(patch.object(real, "KEY_FILE", key))

    def receipt(self, result):
        folder = artifact_directory(self.root, self.job, create=True)
        (folder / "launch.json").write_text(json.dumps(real._identity(self.job)), encoding="utf-8")
        result["expected_work_request"] = real._request(self.job)
        (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return folder

    def reviewed_result(self, cards):
        package = {
            "request_id": "crawl-123", "menu_work_ids": [self.job["menu_work_id"]],
            "content_type": self.job["content_type"], "detail_body": "본문", "cards": cards,
            "sources": [{"id": self.job["source_id"], "url": self.job["canonical_url"]}],
        }
        return {
            "status": "ready_for_feed", "package": package, "review_mode": "llm",
            "editorial_decision": "approved_for_feed", "quality_report": {"decision": "pass_candidate", "hard_blockers": []},
            "qa_report": [], "language_review": {"tone": "pass"}, "usage_records": [{"stage": "review", "total_tokens": 32}],
        }

    def test_child_env_and_args_and_nonzero_duplicate_outcome(self):
        def launch(command, **options):
            self.assertEqual(command.count("crawl-generate"), 1)
            self.assertEqual(command[command.index("--expected-work-id") + 1], "8")
            self.assertNotIn("--retry-failed", command)
            self.assertNotIn("sk-test-", str(command))
            for key in ("DATABASE_URL", "CONTENT_JOB_DATABASE_URL", "REDIS_URL", "HTTP_PROXY", "AWS_SECRET_ACCESS_KEY"):
                self.assertNotIn(key, options["env"])
            self.assertEqual(options["env"]["ANI_MENU_REVIEW_PROFILE"], "standard")
            self.assertEqual(options["env"]["OPENAI_API_KEY"], "sk-test-offline-only-no-provider-call")
            folder = Path(options["cwd"])
            self.assertEqual(options["env"]["ANI_MENU_DATABASE_PATH"], str(folder / "agent.sqlite3"))
            data = {"expected_work_request": real._request(self.job), "status": "duplicate_skipped", "request_id": "crawl-123"}
            (folder / "result.json").write_text(json.dumps(data), encoding="utf-8")
            return Mock(pid=231, returncode=2, poll=Mock(return_value=2))

        with (patch.dict(os.environ, {"HTTP_PROXY": "private", "DATABASE_URL": "private"}),
              patch.object(real.subprocess, "Popen", side_effect=launch) as spawn,
              patch.object(real, "_stop_group") as stop):
            real.generate_real(self.root, self.job, lambda: None)
            result = real.validate_real_artifacts(self.root, self.job)
            self.assertEqual(result["outcome"], "skipped")
            with self.assertRaisesRegex(real.RealGenerationError, "already_exists"):
                real.generate_real(self.root, self.job, lambda: None)
            spawn.assert_called_once()
            stop.assert_called_once()
        folder = artifact_directory(self.root, self.job)
        self.assertNotIn(b"sk-test", (folder / "agent.log").read_bytes())

    def test_missing_secret_is_safe_prelaunch_failure(self):
        real.KEY_FILE.unlink()
        with patch.object(real.subprocess, "run") as dependency_check, self.assertRaises(real.RealGenerationError) as caught:
            real.preflight_real()
        self.assertEqual(caught.exception.code, "invalid_agent_secret")
        self.assertTrue(caught.exception.before_launch)
        dependency_check.assert_not_called()

    def test_lost_lease_terminates_group_without_starting_again(self):
        child = Mock(pid=241, poll=Mock(return_value=None))
        pulse = Mock(side_effect=[None, RuntimeError("lost lease")])
        with (patch.object(real.subprocess, "Popen", return_value=child) as spawn,
              patch.object(real, "_stop_group") as stop,
              self.assertRaisesRegex(RuntimeError, "lost lease")):
            real.generate_real(self.root, self.job, pulse)
        stop.assert_called_once_with(child)
        spawn.assert_called_once()
        with self.assertRaises(real.RealGenerationError):
            real.validate_real_artifacts(self.root, self.job)

    def test_timeout_terminates_group_and_leaves_attempt_for_inspection(self):
        child = Mock(pid=242, poll=Mock(return_value=None))
        with (patch.object(real.subprocess, "Popen", return_value=child), patch.object(real, "_stop_group") as stop,
              patch.object(real.time, "monotonic", side_effect=[0, real.TIMEOUT_SECONDS + 1]),
              self.assertRaisesRegex(real.RealGenerationError, "agent_timeout")):
            real.generate_real(self.root, self.job, lambda: None)
        stop.assert_called_once_with(child)

    @unittest.skipUnless(sys.platform == "linux", "The paid worker runs on Linux")
    def test_killed_pool_parent_also_kills_the_isolated_cli(self):
        # Exercise the real supervisor with a sleeping Python child and no producer or credentials.
        ready = self.root / "sleeping-child.pid"
        group = self.root / "group.pid"
        sleeper = "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
        launcher = ("import os,pathlib,subprocess,sys; "
                    "p=subprocess.Popen([sys.executable,'-I','-c',sys.argv[1],str(os.getpid()),sys.executable,'-I','-c',"
                    "sys.argv[2],sys.argv[3]],start_new_session=True); pathlib.Path(sys.argv[4]).write_text(str(p.pid)); p.wait()")
        pool = subprocess.Popen([sys.executable, "-I", "-c", launcher, real._SUPERVISOR, sleeper, str(ready), str(group)])
        def running(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1][0] != "Z"
            except FileNotFoundError:
                return False
        try:
            limit = time.monotonic() + 8
            while not ready.is_file() and time.monotonic() < limit:
                time.sleep(0.02)
            self.assertTrue(ready.is_file(), "The harmless substitute CLI did not start")
            child_pid = int(ready.read_text())
            pool.kill()
            pool.wait(timeout=3)
            while running(child_pid) and time.monotonic() < limit:
                time.sleep(0.02)
            self.assertFalse(running(child_pid), "A stopped worker left a live producer")
        finally:
            if group.exists():
                try:
                    os.killpg(int(group.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if pool.poll() is None:
                pool.kill()
            pool.wait(timeout=3)

    def test_recovery_seals_known_hold_and_rejects_later_tampering(self):
        folder = self.receipt({"status": "needs_crawler_update", "qa_report": [{"code": "SOURCE_BODY_EMPTY"}]})
        with patch.object(real.subprocess, "Popen", side_effect=AssertionError("Recovery must not generate")):
            evidence = real.validate_real_artifacts(self.root, self.job)
            self.assertEqual(evidence["outcome"], "held")
            self.assertEqual(real.validate_real_artifacts(self.root, self.job), evidence)
            self.assertEqual(evidence["result_sha256"], sha256((folder / "result.json").read_bytes()))
            data = json.loads((folder / "result.json").read_text())
            data["status"] = "stale_candidate_skipped"
            (folder / "result.json").write_text(json.dumps(data))
            with self.assertRaisesRegex(real.RealGenerationError, "manifest_mismatch"):
                real.validate_real_artifacts(self.root, self.job)

    def test_partial_and_synthetic_outputs_are_never_approved(self):
        for data in ({"status": "ready_for_feed"}, {"status": "generated"},
                     {"status": "needs_crawler_update", "review_mode": "offline_fixture"},
                     {"status": "rejected_archived", "editorial_decision": "approved_for_feed"}):
            with self.subTest(result=data):
                self.receipt(data)
                with self.assertRaises(real.RealGenerationError):
                    real.validate_real_artifacts(self.root, self.job)

    def test_blocked_review_never_seals_a_staging_manifest(self):
        for summary, issues in ((["IMAGE_REUSED_RAW"], []),
                                ([], [{"severity": "warning"}, {"severity": "blocker", "code": "IMAGE_REUSED_RAW"}])):
            with self.subTest(hard_blockers=summary, issues=issues):
                data = self.reviewed_result([])
                data["quality_report"]["hard_blockers"] = summary
                data["qa_report"] = issues
                folder = self.receipt(data)
                with self.assertRaisesRegex(real.RealGenerationError, "agent_review_incomplete"):
                    real.validate_real_artifacts(self.root, self.job)
                self.assertFalse((folder / "manifest.json").exists())

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "The live image supplies the PNG decoder")
    def test_ready_card_counts_follow_the_content_type(self):
        from PIL import Image
        images = []
        for page in range(1, 12):
            buffer = BytesIO()
            Image.new("L", (1308, 1632), color=page).save(buffer, format="PNG")
            images.append(buffer.getvalue())
        for content_type, count, accepted in (
            ("news", 2, True), ("event", 2, True), ("goods", 2, True), ("guide", 3, True), ("news", 10, True),
            ("news", 1, False), ("event", 1, False), ("goods", 1, False), ("guide", 2, False), ("news", 11, False),
        ):
            with self.subTest(content_type=content_type, card_count=count):
                self.job["generation_attempt_id"] = str(uuid4())
                self.job["content_type"] = content_type
                cards = [{"page": page, "rendered_image_key": sha256(binary)}
                         for page, binary in enumerate(images[:count], 1)]
                data = self.reviewed_result(cards)
                data["qa_report"] = [{"severity": "warning"}]
                folder = self.receipt(data)
                if not accepted:
                    with self.assertRaisesRegex(real.RealGenerationError, "invalid_card_count"):
                        real.validate_real_artifacts(self.root, self.job)
                    self.assertFalse((folder / "manifest.json").exists())
                    continue
                card_dir = folder / "cards" / data["package"]["request_id"]
                card_dir.mkdir(parents=True)
                for page, binary in enumerate(images[:count], 1):
                    (card_dir / f"card_{page:02}.png").write_bytes(binary)
                result = real.validate_real_artifacts(self.root, self.job)
                self.assertEqual(result["outcome"], "ready_for_staging")
                self.assertEqual(real.validate_real_artifacts(self.root, self.job), result)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "The live image supplies the PNG decoder")
    def test_reviewed_deck_validates_work_type_qa_and_each_original_png(self):
        from PIL import Image
        buffer = BytesIO()
        Image.new("RGB", (1308, 1632), "white").save(buffer, format="PNG")
        binary = buffer.getvalue()
        data = self.reviewed_result([{"page": page, "rendered_image_key": sha256(binary)} for page in (1, 2, 3)])
        package = data["package"]
        folder = self.receipt(data)
        card_dir = folder / "cards" / package["request_id"]
        card_dir.mkdir(parents=True)
        for page in (1, 2):
            (card_dir / f"card_{page:02}.png").write_bytes(binary)
        with self.assertRaises(real.RealGenerationError):
            real.validate_real_artifacts(self.root, self.job)
        self.assertFalse((folder / "manifest.json").exists())
        (card_dir / "card_03.png").write_bytes(binary)
        for field, bad in (("menu_work_ids", [9]), ("content_type", "news")):
            original, package[field] = package[field], bad
            self.receipt(data)
            with self.assertRaises(real.RealGenerationError):
                real.validate_real_artifacts(self.root, self.job)
            package[field] = original
        self.receipt(data)
        self.assertEqual(real.validate_real_artifacts(self.root, self.job)["outcome"], "ready_for_staging")
        data["qa_report"] = [{"severity": "blocker", "code": "IMAGE_REUSED_RAW"}]
        self.receipt(data)
        with self.assertRaisesRegex(real.RealGenerationError, "agent_review_incomplete"):
            real.validate_real_artifacts(self.root, self.job)
        data["qa_report"] = []
        self.receipt(data)
        (card_dir / "card_02.png").write_bytes(b"corrupt")
        with self.assertRaisesRegex(real.RealGenerationError, "invalid_card_digest"):
            real.validate_real_artifacts(self.root, self.job)
        package["cards"][1]["rendered_image_key"] = sha256(b"corrupt")
        self.receipt(data)
        with self.assertRaisesRegex(real.RealGenerationError, "invalid_card_png"):
            real.validate_real_artifacts(self.root, self.job)


if __name__ == "__main__":
    unittest.main()
