"""Disposable PostgreSQL orchestration guards; all Docker calls are mocked."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import postgres_rehearsal as rehearsal

RUN = "a" * 32
SHA = "b" * 40
IMAGE = "sha256:" + "c" * 64
PG = "d" * 64
TESTS = "e" * 64


def owned(identifier, role):
    return {"Id": identifier, "Name": f"/me-nu-main-rehearsal-{RUN}-{role}",
            "Config": {"Labels": {rehearsal.LABEL: rehearsal.PURPOSE, rehearsal.RUN_LABEL: RUN,
                                  rehearsal.ROLE_LABEL: role}},
            "State": {"Status": "exited", "ExitCode": 0, "OOMKilled": False}}


def values(command, flag):
    return [command[index + 1] for index, value in enumerate(command[:-1]) if value == flag]


class RehearsalSafetyTests(unittest.TestCase):
    def test_default_plan_never_calls_docker_or_requires_live_credentials(self):
        output = io.StringIO()
        with patch.object(rehearsal.runtime, "run") as run, redirect_stdout(output):
            self.assertEqual(rehearsal.main([]), 0)
        run.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())["containersStarted"])

    def test_postgres_is_bounded_network_none_tmpfs_only_without_published_ports(self):
        command = rehearsal.postgres_command(IMAGE, RUN)
        self.assertEqual(values(command, "--network"), ["none"])
        self.assertEqual(values(command, "--pull"), ["never"])
        self.assertEqual(values(command, "--memory"), ["512m"])
        self.assertEqual(values(command, "--memory-swap"), ["512m"])
        self.assertEqual(values(command, "--restart"), ["no"])
        self.assertEqual(values(command, "--user"), ["999:999"])
        self.assertIn("--read-only", command)
        self.assertIn("/var/lib/postgresql/data:rw,noexec,nosuid,size=512m,uid=999,gid=999,mode=0700", values(command, "--tmpfs"))
        self.assertFalse(set(command) & {"-v", "--volume", "--mount", "-p", "--publish", "--privileged"})
        self.assertIn(IMAGE, command)
        self.assertIn("listen_addresses=127.0.0.1", command)

    def test_test_container_has_only_readonly_sources_and_owned_postgres_namespace(self):
        with patch.object(rehearsal, "source_mounts", return_value=["--mount", "type=bind,src=/reviewed/backend/app,dst=/rehearsal/app,readonly"]):
            command = rehearsal.test_command(IMAGE, PG, RUN)
        self.assertEqual(values(command, "--network"), ["container:" + PG])
        self.assertEqual(values(command, "--workdir"), ["/rehearsal"])
        self.assertEqual(values(command, "--user"), ["10001:10001"])
        self.assertEqual(values(command, "--entrypoint"), ["python"])
        self.assertIn("--read-only", command)
        self.assertFalse(set(command) & {"--env-file", "--privileged", "-v", "-p", "--publish"})
        environment = dict(value.split("=", 1) for value in values(command, "--env"))
        self.assertEqual(environment["MAIN_WITHDRAWAL_TEST_URL"], rehearsal.URL)
        self.assertEqual(environment["DATABASE_URL"], rehearsal.URL)
        self.assertEqual(environment["SESSION_BACKEND"], "postgres")
        for key in ("OPENAI_API_KEY", "LLM_KEY", "TMDB_KEY", "DICT_DATABASE_URL", "REDIS_URL"):
            self.assertEqual(environment[key], "")
        self.assertNotIn("/var/run/docker.sock", " ".join(command))

    def test_real_backend_settings_accept_exact_rehearsal_environment_without_env_file(self):
        with patch.object(rehearsal, "source_mounts", return_value=[]):
            command = rehearsal.test_command(IMAGE, PG, RUN)
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        environment.update(dict(value.split("=", 1) for value in values(command, "--env")))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "app/core"
            package.mkdir(parents=True)
            for name in ("config.py", "carousel_preview.py"):
                shutil.copyfile(rehearsal.runtime.ROOT / "backend/app/core" / name, package / name)
            environment["PYTHONPATH"] = str(root)
            result = subprocess.run([sys.executable, "-c",
                "from app.core.config import settings; assert settings.session_backend == 'postgres'; "
                "assert settings.database_url.endswith('/main_withdrawal_test'); print('settings_valid')"],
                cwd=root, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)  # Isolated source and synthetic environment only.
        self.assertEqual(result.stdout.strip(), "settings_valid")

    def test_source_mounts_exclude_backend_root_and_reject_nested_environment_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend = root / "backend"
            for directory in ("app", "tests", "alembic"):
                (backend / directory).mkdir(parents=True)
            (backend / "alembic.ini").write_text("synthetic")
            for relative in rehearsal.TEST_FILES:
                (backend / relative).write_text("synthetic")
            (backend / ".env").write_text("PRIVATE_DO_NOT_MOUNT=synthetic")
            with patch.object(rehearsal.runtime, "ROOT", root):
                mounts = values(rehearsal.source_mounts(), "--mount")
                self.assertEqual(len(mounts), 4)
                self.assertTrue(all(item.endswith(",readonly") for item in mounts))
                self.assertFalse(any(f"src={backend}," in item for item in mounts))
                (backend / "app/.env").write_text("PRIVATE_DO_NOT_MOUNT=synthetic")
                with self.assertRaises(rehearsal.RehearsalError):
                    rehearsal.source_mounts()

    def test_image_with_unexpected_declared_volume_is_rejected_before_create(self):
        image = {"Id": IMAGE, "Config": {"User": "10001:10001", "Volumes": {"/private": {}}}}
        with patch.object(rehearsal.runtime, "run", return_value=json.dumps([image])), self.assertRaises(rehearsal.RehearsalError):
            rehearsal.image_id("me-nu-backend-main:" + SHA)

    def test_dirty_or_wrong_commit_cannot_create_any_container(self):
        with patch.object(rehearsal.runtime, "require_host"), patch.object(rehearsal.runtime, "checkout_sha", return_value="0" * 40), patch.object(rehearsal.runtime, "run") as command:
            with self.assertRaises(rehearsal.RehearsalError):
                rehearsal.apply(SHA)
            command.assert_not_called()

    def test_cleanup_verifies_ids_and_labels_and_removes_tests_before_database(self):
        listing = f"{PG} postgres\n{TESTS} tests"
        calls = []
        def run(command, **_):
            calls.append(command)
            if "ps" in command:
                return listing
            if "inspect" in command:
                return json.dumps([owned(command[-1], "postgres" if command[-1] == PG else "tests")])
            return ""
        with patch.object(rehearsal.runtime, "run", side_effect=run):
            rehearsal.cleanup(RUN)
        removals = [command for command in calls if "rm" in command]
        self.assertEqual([command[-1] for command in removals], [TESTS, PG])
        self.assertTrue(all(command[-3:-1] == ["rm", "--force"] for command in removals))
        self.assertFalse(any("prune" in command or "volume" in command for command in calls))

    def test_foreign_label_stops_cleanup_before_any_removal(self):
        foreign = owned(PG, "postgres")
        foreign["Config"]["Labels"][rehearsal.RUN_LABEL] = "f" * 32
        with patch.object(rehearsal.runtime, "run", side_effect=[f"{PG} postgres", json.dumps([foreign])]) as command:
            with self.assertRaises(rehearsal.RehearsalError):
                rehearsal.cleanup(RUN)
            self.assertFalse(any("rm" in call.args[0] for call in command.call_args_list))

    def test_failed_test_attempt_still_cleans_up_only_its_generated_run(self):
        with patch.object(rehearsal.runtime, "require_host"), patch.object(rehearsal.runtime, "checkout_sha", return_value=SHA), patch.object(rehearsal, "source_mounts", return_value=[]), patch.object(rehearsal, "image_id", return_value=IMAGE), patch.object(rehearsal.uuid, "uuid4", return_value=type("Run", (), {"hex": RUN})()), patch.object(rehearsal, "create", side_effect=[PG, TESTS]), patch.object(rehearsal, "inspect_owned", return_value={}), patch.object(rehearsal, "validate_isolation"), patch.object(rehearsal.runtime, "run", side_effect=["", "not-a-test-receipt"]), patch.object(rehearsal, "cleanup") as cleanup:
            with self.assertRaises(rehearsal.RehearsalError):
                rehearsal.apply(SHA)
            cleanup.assert_called_once_with(RUN)

    def test_actual_container_network_ports_volumes_and_bounds_must_match(self):
        item = {"Image": IMAGE, "Config": {"Image": IMAGE, "User": "999:999"}, "Mounts": [],
                "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                    "CapDrop": ["ALL"], "RestartPolicy": {"Name": "no"}, "Memory": 512 * 1024**2,
                    "MemorySwap": 512 * 1024**2, "NanoCpus": 500_000_000, "PidsLimit": 64,
                    "SecurityOpt": ["no-new-privileges:true"], "Tmpfs": {
                        "/var/lib/postgresql/data": "bounded", "/var/run/postgresql": "bounded", "/tmp": "bounded"}}}
        rehearsal.validate_isolation(item, IMAGE, "postgres")
        mutations = (
            lambda value: value["HostConfig"].update(NetworkMode="host"),
            lambda value: value["HostConfig"].update(PortBindings={"5432/tcp": [{"HostPort": "5432"}]}),
            lambda value: value["HostConfig"].update(Memory=0),
            lambda value: value["HostConfig"].update(Binds=["/var/run/docker.sock:/var/run/docker.sock"]),
            lambda value: value.update(Mounts=[{"Type": "volume", "Destination": "/var/lib/postgresql/data"}]),
            lambda value: value.update(NetworkSettings={"Networks": {"me-nu-checking_data": {}}}),
        )
        for mutate in mutations:
            candidate = copy.deepcopy(item)
            mutate(candidate)
            with self.subTest(mutate=mutate), self.assertRaises(rehearsal.RehearsalError):
                rehearsal.validate_isolation(candidate, IMAGE, "postgres")

    def test_test_receipt_requires_every_collected_test_passed_without_skips(self):
        valid = {"exitCode": 0, "collected": 6, "passed": 6, "failed": 0, "skipped": 0}
        self.assertEqual(rehearsal.receipt(json.dumps(valid)), valid)
        for changed in ({"skipped": 1}, {"collected": 0, "passed": 0}, {"failed": 1}, {"exitCode": 1}, {"dsn": "synthetic-secret"}):
            with self.subTest(changed=changed), self.assertRaises(rehearsal.RehearsalError):
                rehearsal.receipt(json.dumps(valid | changed))


if __name__ == "__main__":
    unittest.main()
