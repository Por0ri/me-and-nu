"""Offline debate finalize timer boundaries: no daemon, systemd, database, or external IO."""
from contextlib import contextmanager, nullcontext, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import main_debates as debates
import main_notices as notices
import main_runtime as runtime

SECRET = "synthetic-private-database-password"
COUNTS = {"expired": 1, "closed": 2, "deleted_messages": 30, "purged_debates": 0}


def calendar(content):
    return [line.split("=", 1)[1] for line in content.splitlines() if line.startswith("OnCalendar=")]


def cli(argv, error):
    """Run main() with dispatch raising `error`; return (exit code, parsed stderr)."""
    output = io.StringIO()
    with patch.object(runtime, "require_host"), patch.object(debates, "dispatch", side_effect=error), redirect_stderr(output):
        code = debates.main(argv)
    return code, output.getvalue()


class MainDebatesTest(unittest.TestCase):
    def test_runtime_guard_failure_blocks_database_command(self):
        with patch.object(runtime, "lock", return_value=nullcontext()), patch.object(runtime, "inspect_main", side_effect=runtime.RuntimeGuardError("main_runtime_identity_failed")), patch.object(runtime, "run") as command:
            with self.assertRaises(runtime.RuntimeGuardError):
                debates.dispatch(apply=True)
            command.assert_not_called()

    def test_fixed_command_runs_inside_verified_api_and_applies_only_on_run(self):
        for apply, mode in ((False, "dry_run"), (True, "apply")):
            receipt = {"mode": mode, "retention_days": 9, **COUNTS}
            with self.subTest(apply=apply), patch.object(runtime, "lock", return_value=nullcontext()), patch.object(runtime, "inspect_main") as guard, patch.object(runtime, "run", return_value=json.dumps(receipt)) as command:
                self.assertEqual(debates.dispatch(apply=apply), {"mode": mode, **COUNTS})
                guard.assert_called_once_with()
                args = command.call_args.args[0]
                self.assertEqual(args[:len(runtime.DOCKER)], runtime.DOCKER)
                self.assertIn(runtime.API, args)
                self.assertIn("10001:10001", args)
                self.assertIn("app.jobs.finalize_debates", args)
                self.assertEqual(args[args.index("--expected-database") + 1], "me_nu_checking")
                self.assertEqual("--apply" in args, apply)

    def test_job_runs_while_both_rollout_locks_are_held(self):
        held = []

        @contextmanager
        def lock(path=runtime.ROLLOUT_LOCK):
            held.append(path)
            try:
                yield
            finally:
                held.remove(path)

        def verified():
            self.assertEqual(held, [runtime.ROLLOUT_LOCK, debates.LEGACY_LOCK])

        def job(*_args, **_kwargs):
            self.assertEqual(held, [runtime.ROLLOUT_LOCK, debates.LEGACY_LOCK])
            return json.dumps({"mode": "dry_run", **COUNTS})

        with patch.object(runtime, "lock", side_effect=lock), patch.object(runtime, "inspect_main", side_effect=verified) as guard, patch.object(runtime, "run", side_effect=job) as run:
            debates.dispatch()
        guard.assert_called_once_with()
        run.assert_called_once()
        self.assertEqual(held, [])

    def test_receipt_keeps_only_bounded_counts(self):
        for value in ({"mode": "apply", **COUNTS, "closed": -1}, {"mode": "apply", **COUNTS, "closed": True},
                      {"mode": "apply", **COUNTS, "closed": "2"}, {"mode": "dry_run", **COUNTS},
                      {"mode": "apply", "expired": 1}, [], "apply"):
            with self.subTest(value=value), self.assertRaises(runtime.RuntimeGuardError):
                debates.safe_receipt(value, apply=True)
        self.assertEqual(debates.safe_receipt({"mode": "apply", "note": SECRET, **COUNTS}, apply=True), {"mode": "apply", **COUNTS})

    def test_receipt_keys_match_the_backend_job(self):
        source = (runtime.ROOT / "backend/app/services/debate_finalize_service.py").read_text(encoding="utf-8")
        for key in debates.COUNTS:
            self.assertIn(f'report["{key}"]', source)
        self.assertIn('"mode": "apply" if apply else "dry_run"', source)

    def test_backend_job_finishes_before_the_wrapper_gives_up(self):
        # On timeout the wrapper kills only the docker CLI and releases the locks; the job inside must give up first.
        source = (runtime.ROOT / "backend/app/jobs/finalize_debates.py").read_text(encoding="utf-8")
        match = re.search(r"^JOB_TIMEOUT_SECONDS = (\d+)$", source, re.M)
        self.assertIsNotNone(match)
        self.assertIn("SET LOCAL lock_timeout", source)
        with patch.object(runtime, "lock", return_value=nullcontext()), patch.object(runtime, "inspect_main"), patch.object(runtime, "run", return_value=json.dumps({"mode": "dry_run", **COUNTS})) as command:
            debates.dispatch()
        # Room for interpreter start-up and psycopg's cancel and rollback after the job's own deadline.
        self.assertGreaterEqual(command.call_args.kwargs.get("timeout", 0) - int(match.group(1)), 30)

    def test_systemd_limit_outlasts_preflight_and_wrapper_timeout(self):
        # TimeoutStartSec counts from service start and kills only the host side (wrapper, docker CLI, locks).
        service = debates.units()[debates.SERVICE].decode()
        (limit,) = [line.split("=", 1)[1] for line in service.splitlines() if line.startswith("TimeoutStartSec=")]
        with patch.object(runtime, "lock", return_value=nullcontext()), patch.object(runtime, "inspect_main"), patch.object(runtime, "run", return_value=json.dumps({"mode": "dry_run", **COUNTS})) as command:
            debates.dispatch()
        preflight = 5 * 30 + 20  # inspect_main: git x2 and docker inspect x3 (runtime.run default 30 s), schema probe 20 s
        self.assertTrue(limit.endswith("min"))
        self.assertGreaterEqual(int(limit.removesuffix("min")) * 60, preflight + command.call_args.kwargs["timeout"] + 30)

    def test_unit_payload_uses_current_checkout_and_main_names(self):
        units = debates.units()
        service = units[debates.SERVICE].decode()
        self.assertIn(str(runtime.ROOT), service)
        if str(runtime.ROOT) != debates.UNIT_ROOT:
            self.assertNotIn(debates.UNIT_ROOT, service)
        self.assertIn("main_debates.py run", service)
        self.assertIn("SyslogIdentifier=me-nu-main-debates", service)
        self.assertNotIn("me-nu-checking", service)
        self.assertIn(f"Unit={debates.SERVICE}", units[debates.TIMER].decode())

    def test_timer_runs_every_ten_minutes_clear_of_the_notice_job(self):
        timer = debates.units()[debates.TIMER].decode()
        self.assertEqual(calendar(timer), ["*-*-* *:05/10:30"])
        self.assertIn("AccuracySec=1s", timer)
        # The notice job starts every minute at second 0 and takes the same rollout lock.
        self.assertEqual(calendar(notices.units()[notices.TIMER].decode()), ["*-*-* *:*:00"])

    def test_enable_verifies_runtime_then_installs_rendered_units_and_starts_timer(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def verified():
                self.assertEqual(list(directory.iterdir()), [])  # nothing is written before the runtime check

            with patch.object(debates, "UNIT_DIRECTORY", directory), patch.object(runtime, "lock", return_value=nullcontext()), patch.object(runtime, "inspect_main", side_effect=verified) as guard, patch.object(runtime, "run", return_value="") as command:
                self.assertEqual(debates.enable(), {"timer_enabled": True})
            guard.assert_called_once_with()
            self.assertEqual(sorted(path.name for path in directory.iterdir()), sorted((debates.SERVICE, debates.TIMER)))
            for name, data in debates.units().items():
                self.assertEqual((directory / name).read_bytes(), data)
            self.assertEqual([call.args[0] for call in command.call_args_list],
                             [["/usr/bin/systemctl", "daemon-reload"], ["/usr/bin/systemctl", "enable", "--now", debates.TIMER]])

    def test_enable_writes_nothing_when_the_runtime_check_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.object(debates, "UNIT_DIRECTORY", directory), patch.object(runtime, "lock", return_value=nullcontext()), patch.object(runtime, "inspect_main", side_effect=runtime.RuntimeGuardError("main_runtime_validation_failed")), patch.object(runtime, "run") as command:
                with self.assertRaises(runtime.RuntimeGuardError):
                    debates.enable()
            self.assertEqual(list(directory.iterdir()), [])
            command.assert_not_called()

    def test_preview_is_the_default_action(self):
        with patch.object(runtime, "require_host"), patch.object(debates, "dispatch", return_value={"mode": "dry_run", **COUNTS}) as dispatch, redirect_stdout(io.StringIO()):
            self.assertEqual(debates.main([]), 0)
        dispatch.assert_called_once_with(apply=False)

    def test_failures_report_a_fixed_reason_and_lock_overlap_skips_only_timer_runs(self):
        cases = (
            (["run"], runtime.RuntimeGuardError("operation_in_progress"), 0, {"state": "skipped", "reason": "operation_in_progress"}),
            (["preview"], runtime.RuntimeGuardError("operation_in_progress"), 1, {"state": "failed", "reason": "operation_in_progress"}),
            (["run"], runtime.RuntimeGuardError("main_service_schema_not_ready"), 1, {"state": "failed", "reason": "main_service_schema_not_ready"}),
            (["run"], runtime.RuntimeGuardError(f"Bad {SECRET}"), 1, {"state": "failed", "reason": "runtime_guard_failed"}),
            (["run"], json.JSONDecodeError(SECRET, SECRET, 0), 1, {"state": "failed", "reason": "unexpected_error"}),
            (["run"], OSError(SECRET), 1, {"state": "failed", "reason": "unexpected_error"}),
        )
        for argv, error, expected_code, expected in cases:
            with self.subTest(argv=argv, error=error):
                code, stderr = cli(argv, error)
                self.assertEqual(code, expected_code)
                self.assertEqual(json.loads(stderr), expected)
                self.assertNotIn(SECRET, stderr)

    def test_cli_never_echoes_credential_bearing_errors(self):
        output = io.StringIO()
        with patch.object(runtime, "require_host", side_effect=ValueError(SECRET)), redirect_stderr(output):
            self.assertEqual(debates.main(["run"]), 1)
        self.assertNotIn(SECRET, output.getvalue())


if __name__ == "__main__":
    unittest.main()
