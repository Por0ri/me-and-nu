"""Offline handover boundaries: no daemon, systemd, database, or external IO."""
from contextlib import nullcontext, redirect_stderr
import copy
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import types
import sys
import unittest
from unittest.mock import patch

import main_notices as notices
import main_recommend as recommend
import main_runtime as runtime


SHA = "a" * 40
IMAGE = "sha256:" + "b" * 64
SECRET = "synthetic-private-database-password"


def api():
    return {
        "Name": "/me-nu-main-api-1", "Image": IMAGE,
        "Config": {"Image": f"me-nu-backend-main:{SHA}", "User": "10001:10001",
                   "Labels": {"com.docker.compose.project": "me-nu-main", "com.docker.compose.service": "api"},
                   "Env": ["MENU_DEPLOYMENT_ENV=production", "CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED=false",
                           "ENABLE_DEV_API=false", "ENABLE_DEV_AUTH_BYPASS=false", "REDIS_KEY_PREFIX=me_nu_main",
                           "SESSION_COOKIE_NAME=menu_main_session", "EVENT_DATA_SOURCE=local_demo",
                           f"DATABASE_URL=postgresql+psycopg://me_nu_checking:{SECRET}@postgres:5432/me_nu_checking"]},
        "HostConfig": {"ReadonlyRootfs": True, "Privileged": False, "NetworkMode": "me-nu-main_egress"},
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "NetworkSettings": {"Networks": {"me-nu-checking_data": {}, "me-nu-main_egress": {}}},
    }


class MainRuntimeTest(unittest.TestCase):
    def test_live_schema_receipt_must_be_true_and_contains_no_other_fields(self):
        for value in ({"schemaReady": False}, {"schemaReady": 1}, {"schemaReady": True, "dsn": SECRET}, {}):
            with self.subTest(value=value), patch.object(runtime, "run", return_value=json.dumps(value)), self.assertRaises(runtime.RuntimeGuardError):
                runtime.require_schema_ready()
        with patch.object(runtime, "run", return_value='{"schemaReady":true}') as command:
            runtime.require_schema_ready()
            self.assertIn("--user", command.call_args.args[0])
            self.assertIn(runtime.API, command.call_args.args[0])

    def test_read_only_probe_uses_deployed_schema_policy_and_redacts_failures(self):
        spec = importlib.util.spec_from_file_location("main_test_migration_policy", runtime.ROOT / "backend/app/db/migration_policy.py")
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        class Result:
            def __init__(self, rows):
                self.rows = rows
            def fetchone(self):
                return self.rows[0]
            def __iter__(self):
                return iter(self.rows)
        class Connection:
            def __init__(self, revisions):
                self.revisions = revisions
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def execute(self, query):
                return Result([("me_nu_checking",)] if "current_database" in query else [(item,) for item in self.revisions])
        for revisions, expected in (({"b7d4e2a9c601"}, False), ({policy.SERVICE_SCHEMA_REVISION}, True),
                                     ({policy.SERVICE_SCHEMA_REVISION, policy.DEFERRED_BIRTH_REVISION}, True),
                                     ({policy.SERVICE_SCHEMA_REVISION, "unknown"}, False)):
            options = []
            def connect(dsn, **kwargs):
                options.append(kwargs)
                return Connection(revisions)
            modules = {"psycopg": types.SimpleNamespace(connect=connect), "app.db.migration_policy": policy}
            output = io.StringIO()
            from contextlib import redirect_stdout
            with self.subTest(revisions=revisions), patch.dict(sys.modules, modules), patch.dict(runtime.os.environ, {"DATABASE_URL": SECRET}), redirect_stdout(output):
                exec(runtime.SCHEMA_PROBE, {})
            self.assertEqual(json.loads(output.getvalue()), {"schemaReady": expected})
            self.assertIn("default_transaction_read_only=on", options[0]["options"])
            self.assertNotIn(SECRET, output.getvalue())
        output = io.StringIO()
        from contextlib import redirect_stdout
        with patch.dict(sys.modules, {"psycopg": types.SimpleNamespace(connect=lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError(SECRET))), "app.db.migration_policy": policy}), patch.dict(runtime.os.environ, {"DATABASE_URL": SECRET}), redirect_stdout(output):
            exec(runtime.SCHEMA_PROBE, {})
        self.assertEqual(json.loads(output.getvalue()), {"schemaReady": False})
        self.assertNotIn(SECRET, output.getvalue())

    def test_verified_main_preserves_database_without_returning_other_environment(self):
        identity = runtime.validate_main(api(), SHA)
        self.assertEqual(set(identity), {"sha", "image", "database_url"})
        self.assertIn("/me_nu_checking", identity["database_url"])

    def test_wrong_runtime_and_database_fail_closed(self):
        mutations = (
            lambda x: x.update(Name="/me-nu-checking-api-1"),
            lambda x: x["Config"].update(Image="me-nu-backend-main:latest"),
            lambda x: x["State"]["Health"].update(Status="unhealthy"),
            lambda x: x["HostConfig"].update(Privileged=True),
            lambda x: x["HostConfig"].update(NetworkMode="host"),
            lambda x: x["NetworkSettings"]["Networks"].update(unrelated={}),
            lambda x: x["Config"]["Env"].append("MENU_DEPLOYMENT_ENV=checking"),
            lambda x: x["Config"]["Env"].__setitem__(-1, f"DATABASE_URL=postgresql://other:{SECRET}@host/prod"),
        )
        for change in mutations:
            value = api()
            change(value)
            with self.subTest(change=change), self.assertRaises(runtime.RuntimeGuardError) as error:
                runtime.validate_main(value, SHA)
            self.assertNotIn(SECRET, str(error.exception))

    def test_command_failures_never_return_secret_stdout_or_stderr(self):
        result = subprocess.CompletedProcess([], 1, SECRET, SECRET)
        with patch.object(runtime.subprocess, "run", return_value=result), self.assertRaises(runtime.RuntimeGuardError) as error:
            runtime.run(["unused"])
        self.assertNotIn(SECRET, str(error.exception))

    def test_legacy_recommend_must_be_inactive_with_restart_disabled(self):
        base = {"Name": "/me-nu-checking-recommend", "State": {"Status": "exited", "Running": False},
                "HostConfig": {"RestartPolicy": {"Name": "no"}}}
        with patch.object(runtime, "run", side_effect=["id", json.dumps([base])]):
            runtime.require_legacy_stopped("recommend")
        for running, status, policy in ((True, "running", "no"), (False, "exited", "unless-stopped"),
                                         (False, "restarting", "no")):
            value = copy.deepcopy(base)
            value["State"] = {"Status": status, "Running": running}
            value["HostConfig"]["RestartPolicy"]["Name"] = policy
            with self.subTest(status=status, policy=policy), patch.object(runtime, "run", side_effect=["id", json.dumps([value])]), self.assertRaises(runtime.RuntimeGuardError):
                runtime.require_legacy_stopped("recommend")

    def test_legacy_timer_enabled_or_active_service_blocks_handover(self):
        disabled = {"LoadState": "loaded", "ActiveState": "inactive", "UnitFileState": "disabled"}
        for kind in ("evaluation", "notices"):
            with patch.object(runtime, "_unit_properties", return_value=disabled):
                runtime.require_legacy_stopped(kind)
            for states in ((disabled | {"UnitFileState": "enabled"}, disabled),
                           (disabled, disabled | {"ActiveState": "active"}),
                           (disabled | {"LoadState": "error"}, disabled)):
                with self.subTest(kind=kind, states=states), patch.object(runtime, "_unit_properties", side_effect=states), self.assertRaises(runtime.RuntimeGuardError):
                    runtime.require_legacy_stopped(kind)


class MainRecommendationTest(unittest.TestCase):
    def test_active_legacy_worker_blocks_start_without_replacing_any_container(self):
        identity = {"sha": SHA, "image": IMAGE, "database_url": SECRET}
        with tempfile.TemporaryDirectory() as temporary:
            context = Path(temporary) / "context.gz"
            context.write_bytes(b"existing-context")
            with patch.object(recommend, "CONTEXT", context), patch.object(runtime, "lock", return_value=nullcontext()), patch.object(runtime, "inspect_main", return_value=identity), patch.object(recommend, "model_image", return_value=IMAGE), patch.object(runtime, "require_legacy_stopped", side_effect=runtime.RuntimeGuardError("legacy_running")), patch.object(runtime, "run") as command:
                with self.assertRaises(runtime.RuntimeGuardError):
                    recommend.execute("up")
                command.assert_not_called()

    def test_existing_main_worker_is_never_automatically_removed(self):
        identity = {"sha": SHA, "image": IMAGE, "database_url": SECRET}
        with tempfile.TemporaryDirectory() as temporary:
            context = Path(temporary) / "context.gz"
            context.write_bytes(b"existing-context")
            with patch.object(recommend, "CONTEXT", context), patch.object(runtime, "lock", return_value=nullcontext()), patch.object(runtime, "inspect_main", return_value=identity), patch.object(recommend, "model_image", return_value=IMAGE), patch.object(runtime, "require_legacy_stopped"), patch.object(runtime, "run", return_value="existing-main-id") as command:
                with self.assertRaises(runtime.RuntimeGuardError):
                    recommend.execute("up")
                self.assertEqual(command.call_count, 1)
                self.assertIn("ps", command.call_args.args[0])
                self.assertNotIn("rm", command.call_args.args[0])

    def test_commands_preserve_db_and_never_enable_preview_carousels(self):
        preview = recommend.command(IMAGE, apply=False)
        applied = recommend.command(IMAGE, apply=True)
        for command in (preview, applied):
            self.assertIn("me_nu_checking", command)
            self.assertIn(runtime.NETWORK, command)
            self.assertNotIn("--preview", command)
            self.assertNotIn(SECRET, " ".join(command))
            self.assertNotIn("--replace", command)
            self.assertIn("10001:10001", command)
            self.assertIn(IMAGE, command)
        self.assertNotIn("--apply", preview)
        self.assertIn("PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=30000", preview)
        self.assertIn("--apply", applied)
        self.assertIn("06:00", applied)
        with self.assertRaises(runtime.RuntimeGuardError):
            recommend.command("me-nu-recommend-main:latest", apply=True)

    def test_cli_never_echoes_credential_bearing_errors(self):
        output = io.StringIO()
        with patch.object(runtime, "require_host", side_effect=ValueError(SECRET)), redirect_stderr(output):
            self.assertEqual(recommend.main(["up"]), 1)
        self.assertNotIn(SECRET, output.getvalue())


class MainNoticesTest(unittest.TestCase):
    def test_apply_waits_for_legacy_retirement_before_database_command(self):
        with patch.object(runtime, "lock", return_value=nullcontext()), patch.object(runtime, "inspect_main"), patch.object(runtime, "require_legacy_stopped", side_effect=runtime.RuntimeGuardError("legacy_active")), patch.object(runtime, "run") as command:
            with self.assertRaises(runtime.RuntimeGuardError):
                notices.dispatch(apply=True)
            command.assert_not_called()

    def test_notice_receipt_contains_only_counts_and_no_user_text(self):
        receipt = {"mode": "apply", "due": 1, "published": 1, "deliveries": 4, "title": SECRET}
        with patch.object(runtime, "lock", return_value=nullcontext()), patch.object(runtime, "inspect_main"), patch.object(runtime, "require_legacy_stopped") as guard, patch.object(runtime, "run", return_value=json.dumps(receipt)) as command:
            self.assertEqual(notices.dispatch(apply=True), {"mode": "apply", "due": 1, "published": 1, "deliveries": 4})
            guard.assert_called_once_with("notices")
            args = command.call_args.args[0]
            self.assertIn("me_nu_checking", args)
            self.assertIn("--apply", args)
            self.assertIn(runtime.API, args)

    def test_unit_payload_uses_current_checkout_and_main_names(self):
        units = notices.units()
        service = units[notices.SERVICE].decode()
        self.assertIn(str(runtime.ROOT), service)
        self.assertIn("main_notices.py run", service)
        self.assertNotIn("me-nu-checking", service)
        self.assertNotIn("main_recommend", service)


if __name__ == "__main__":
    unittest.main()
