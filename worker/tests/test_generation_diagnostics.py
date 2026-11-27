"""Failure diagnostics with synthetic child processes; no Agent, network or LLM."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from menu_worker import real_generation as real
from menu_worker.job_artifacts import artifact_directory
import test_real_generation as fixtures


class DiagnosticParserTests(unittest.TestCase):
    def test_chunk_boundaries_never_preserve_secret_messages_headers_or_paths(self):
        key = "sk-test-secret-only-for-diagnostic-fixture"
        text = ('  File "/opt/ani-agent/src/ani_menu_agent/cli.py", line 142, in crawl_generate\n'
                '    client(api_key="' + key + '")\n'
                'TypeError: credential=' + key + '; Authorization: Bearer private-token\n'
                'https://private.example/?token=private-token\n').encode()
        capture = real._Diagnostics(key)
        for byte in text:
            capture.feed(bytes((byte,)))
        capture.finish()
        summary = capture.summary()
        self.assertEqual(summary["events"], [{"file": "cli.py", "line": 142}, {"exception_type": "TypeError"}])
        encoded = json.dumps(summary)
        for secret in (key, "private-token", "private.example", "client", "crawl_generate", "/opt/"):
            self.assertNotIn(secret, encoded)

    def test_long_lines_and_repeated_events_have_fixed_memory_bounds(self):
        capture = real._Diagnostics("sk-synthetic-key-value")
        for _ in range(1000):
            capture.feed(b"private=" + b"x" * 8192)
            self.assertLessEqual(len(capture._line), real.MAX_DIAGNOSTIC_LINE)
        capture.feed(b"\n")
        for _ in range(1000):
            capture.feed(b"ValueError: never persist this message\n")
        capture.finish()
        self.assertEqual(capture.dropped_lines, 1)
        self.assertEqual(len(capture.events), real.MAX_DIAGNOSTIC_EVENTS)
        self.assertLess(len(real._dump(capture.summary())), 4096)

    def test_unknown_output_and_rich_traceback_only_expose_allowlisted_metadata(self):
        capture = real._Diagnostics()
        capture.feed(b'\x1b[31mTypeError: do not retain arbitrary values\x1b[0m\n')
        capture.feed('│ /opt/ani-agent/src/ani_menu_agent/cli.py:321 in crawl_generate │\n'.encode())
        capture.feed(b'  File "/private/credential.py", line 99, in secret\n')
        capture.feed(b'PrivateSecretError: some data\n')
        capture.feed(b'openai.AuthenticationError: provider response and credentials stay private\n')
        capture.finish()
        self.assertEqual(list(capture.events), [{"exception_type": "TypeError"}, {"file": "cli.py", "line": 321},
                                               {"exception_type": "AuthenticationError"}])

    def test_provider_response_and_finish_errors_keep_type_without_payload(self):
        for name in ("APIResponseValidationError", "LengthFinishReasonError", "ContentFilterFinishReasonError",
                     "UnprocessableEntityError", "ConflictError", "NotFoundError", "APIError", "OpenAIError"):
            with self.subTest(exception=name):
                capture = real._Diagnostics()
                capture.feed(f"openai.{name}: private completion, response body and request headers\n".encode())
                capture.finish()
                self.assertEqual(list(capture.events), [{"exception_type": name}])
                self.assertNotIn("private", json.dumps(capture.summary()))


class ProcessDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RealGenerationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.job = self.fixture.root, self.fixture.job

    def test_nonzero_missing_result_is_process_failure_and_preserves_original_files(self):
        directory = self.fixture.receipt({"status": "duplicate_skipped"})
        (directory / "result.json").unlink()
        (directory / "exit.json").write_text('{"returncode": 1}')
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        with self.assertRaisesRegex(real.RealGenerationError, "^agent_process_failed$"):
            real.validate_real_artifacts(self.root, self.job)
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})
        self.assertFalse((directory / "manifest.json").exists())

    def test_zero_exit_missing_result_and_invalid_result_are_distinguished(self):
        directory = self.fixture.receipt({"status": "duplicate_skipped"})
        (directory / "result.json").unlink()
        (directory / "exit.json").write_text('{"returncode": 0}')
        with self.assertRaisesRegex(real.RealGenerationError, "^agent_result_missing$"):
            real.validate_real_artifacts(self.root, self.job)
        (directory / "result.json").write_bytes(b"not-json")
        with self.assertRaisesRegex(real.RealGenerationError, "^artifact_verification_failed$"):
            real.validate_real_artifacts(self.root, self.job)
        (directory / "exit.json").write_text('{"returncode": false}')
        with self.assertRaisesRegex(real.RealGenerationError, "^invalid_agent_exit_record$"):
            real.validate_real_artifacts(self.root, self.job)

    def test_nonzero_duplicate_remains_idempotently_sealable(self):
        directory = self.fixture.receipt({"status": "duplicate_skipped"})
        (directory / "exit.json").write_text('{"returncode": 2}')
        evidence = real.validate_real_artifacts(self.root, self.job)
        original = (directory / "manifest.json").read_bytes()
        self.assertEqual(evidence["outcome"], "skipped")
        self.assertEqual(real.validate_real_artifacts(self.root, self.job), evidence)
        self.assertEqual((directory / "manifest.json").read_bytes(), original)

    def test_nonzero_failure_receipt_preserves_partial_usage_without_sealing(self):
        directory = self.fixture.receipt({"status": "generation_failed", "request_id": "failed-original",
            "failure_stage": "generation", "error_type": "TypeError",
            "usage_records": [{"stage": "extract", "estimated_cost_usd": "0.01"}],
            "cost_accounting": {"complete": False}})
        (directory / "exit.json").write_text('{"returncode": 1}')
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        with self.assertRaisesRegex(real.RealGenerationError, "^agent_process_failed$"):
            real.validate_real_artifacts(self.root, self.job)
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})
        self.assertFalse((directory / "manifest.json").exists())
        (directory / "exit.json").write_text('{"returncode": 0}')
        with self.assertRaisesRegex(real.RealGenerationError, "^unknown_or_unbound_agent_result$"):
            real.validate_real_artifacts(self.root, self.job)

    @unittest.skipUnless(sys.platform == "linux", "The generation runtime uses Linux process groups")
    def test_real_pipe_flood_is_drained_and_exit_metadata_is_safe(self):
        original_popen = subprocess.Popen
        script = r"""import os, sys
key = os.environ['OPENAI_API_KEY']
for _ in range(128):
    os.write(1, (key + ' ' + ('x' * 4096)).encode())
os.write(2, b'\n  File "/opt/ani-agent/src/ani_menu_agent/cli.py", line 142, in crawl_generate\n')
for part in ('TypeError: credential=', key[:8], key[8:], '\n'):
    os.write(2, part.encode())
sys.exit(17)
"""
        def fake_cli(command, **options):
            self.assertEqual(options["stdout"], subprocess.PIPE)
            self.assertEqual(options["stderr"], subprocess.STDOUT)
            return original_popen([sys.executable, "-I", "-c", script], **options)
        started = time.monotonic()
        with patch.object(real.subprocess, "Popen", side_effect=fake_cli) as launch:
            with self.assertRaisesRegex(real.RealGenerationError, "^agent_process_failed$"):
                real.generate_real(self.root, self.job, lambda: None)
            with self.assertRaisesRegex(real.RealGenerationError, "generation_attempt_already_exists"):
                real.generate_real(self.root, self.job, lambda: None)
            launch.assert_called_once()
        self.assertLess(time.monotonic() - started, 15)
        directory = artifact_directory(self.root, self.job)
        log = (directory / "agent.log").read_bytes()
        self.assertLess(len(log), 8192)
        self.assertNotIn(b"sk-test", log)
        self.assertNotIn(b"credential", log)
        self.assertIn(b"TypeError", log)
        self.assertIn(b'"file":"cli.py"', log)
        self.assertIn(b"exited 17", log)
        self.assertEqual(json.loads((directory / "exit.json").read_text())["returncode"], 17)
        self.assertFalse((directory / "manifest.json").exists())

    @unittest.skipUnless(sys.platform == "linux", "The generation runtime uses Linux process groups")
    def test_output_flood_timeout_records_exit_without_deadlock_or_retry(self):
        original_popen = subprocess.Popen
        script = "import os; payload=b'x'*4096\nwhile True: os.write(1,payload)"
        def fake_cli(command, **options):
            return original_popen([sys.executable, "-I", "-c", script], **options)
        started = time.monotonic()
        with patch.object(real.subprocess, "Popen", side_effect=fake_cli) as launch, patch.object(real, "TIMEOUT_SECONDS", 0.15):
            with self.assertRaisesRegex(real.RealGenerationError, "^agent_timeout$"):
                real.generate_real(self.root, self.job, lambda: None)
            launch.assert_called_once()
        self.assertLess(time.monotonic() - started, 5)
        directory = artifact_directory(self.root, self.job)
        exit_record = json.loads((directory / "exit.json").read_text())
        self.assertLess(exit_record["returncode"], 0)
        self.assertEqual(exit_record["supervisor_stop"], "agent_timeout")
        self.assertIn(b"Supervisor stop: agent_timeout", (directory / "agent.log").read_bytes())
        self.assertLess((directory / "agent.log").stat().st_size, 8192)


if __name__ == "__main__":
    unittest.main()
