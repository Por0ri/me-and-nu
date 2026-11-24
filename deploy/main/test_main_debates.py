"""Offline debate finalize timer boundaries: no daemon, systemd, database, or external IO."""
from contextlib import nullcontext, redirect_stderr, redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

import main_debates as debates
import main_notices as notices
import main_runtime as runtime

SECRET = "synthetic-private-database-password"
COUNTS = {"expired": 1, "closed": 2, "deleted_messages": 30, "purged_debates": 0}


def calendar(content):
    return [line.split("=", 1)[1] for line in content.splitlines() if line.startswith("OnCalendar=")]


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

    def test_both_rollout_locks_are_held_while_the_job_runs(self):
        held = []

        def lock(path=runtime.ROLLOUT_LOCK):
            held.append(path)
            return nullcontext()

        with patch.object(runtime, "lock", side_effect=lock), patch.object(runtime, "inspect_main"), patch.object(runtime, "run", return_value=json.dumps({"mode": "dry_run", **COUNTS})):
            debates.dispatch()
        self.assertEqual(held, [runtime.ROLLOUT_LOCK, debates.LEGACY_LOCK])

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

    def test_unit_payload_uses_current_checkout_and_main_names(self):
        units = debates.units()
        service = units[debates.SERVICE].decode()
        self.assertIn(str(runtime.ROOT), service)
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

    def test_preview_is_the_default_action(self):
        with patch.object(runtime, "require_host"), patch.object(debates, "dispatch", return_value={"mode": "dry_run", **COUNTS}) as dispatch, redirect_stdout(io.StringIO()):
            self.assertEqual(debates.main([]), 0)
        dispatch.assert_called_once_with(apply=False)

    def test_cli_never_echoes_credential_bearing_errors(self):
        output = io.StringIO()
        with patch.object(runtime, "require_host", side_effect=ValueError(SECRET)), redirect_stderr(output):
            self.assertEqual(debates.main(["run"]), 1)
        self.assertNotIn(SECRET, output.getvalue())


if __name__ == "__main__":
    unittest.main()
