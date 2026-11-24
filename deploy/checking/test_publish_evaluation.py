"""No-network publication guards; never invokes Docker or a real deployment."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import publish_evaluation as publisher

SHA = "a" * 40
IMAGE = "sha256:" + "b" * 64


class PublishEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "checkout"
        self.artifacts = self.root / "deploy/checking/admin-evaluation"
        self.artifacts.mkdir(parents=True)
        os.chmod(self.artifacts, 0o755)
        (self.root / "model/eval").mkdir(parents=True)
        self.target = self.artifacts / "evaluation.json"
        self.incoming = self.base / "incoming.json"
        self.incoming.write_bytes(b'{"incoming":true}')
        self.root_patch = patch.object(publisher, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.temporary.cleanup)

    def validate(self, command):
        self.command = command
        source = next(part for part in command if part.startswith("type=bind,src=") and part.endswith(",dst=/staging"))
        stage = Path(source.removeprefix("type=bind,src=").removesuffix(",dst=/staging"))
        (stage / "evaluation.json").write_bytes(b'{"canonical":true}')
        os.chmod(stage / "evaluation.json", 0o644)
        return "safe metadata only"

    def publish(self, validator=None, identities=None):
        with patch.object(publisher, "runtime_identity", side_effect=identities or [(SHA, IMAGE), (SHA, IMAGE)]), \
                patch.object(publisher, "run", side_effect=validator or self.validate):
            return publisher.publish(self.incoming)

    def test_missing_becomes_published_and_validator_is_isolated(self):
        result = self.publish()
        self.assertEqual(self.target.read_bytes(), b'{"canonical":true}')
        self.assertTrue(result["published"])
        self.assertIsNone(result["previousSha256"])
        self.assertEqual(result["sha256"], publisher.digest(self.target.read_bytes()))
        self.assertEqual(self.command[:4], ["docker", "--context", "default", "run"])
        for flag in ("--rm", "--read-only", "--cap-drop", "--security-opt", "--pids-limit", "--memory", "--cpus"):
            self.assertIn(flag, self.command)
        self.assertEqual(self.command[self.command.index("--network") + 1], "none")
        self.assertEqual(self.command[self.command.index("--cap-drop") + 1], "ALL")
        self.assertIn(IMAGE, self.command)
        mounts = [part for part in self.command if part.startswith("type=bind,")]
        self.assertEqual(len(mounts), 3)
        self.assertEqual(sum(part.endswith(",readonly") for part in mounts), 2)
        self.assertFalse(any(str(self.artifacts) in part or "docker.sock" in part for part in mounts))
        self.assertNotIn("--env-file", self.command)
        self.assertNotIn("--privileged", self.command)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o644)

    def test_previous_content_and_digest_are_backed_up_outside_api_mount(self):
        previous = b'{"previous":true}'
        self.target.write_bytes(previous)
        result = self.publish()
        backups = self.base / ".checking-evaluation-artifacts/backups/offline"
        self.assertEqual((backups / (result["backup"] + ".json")).read_bytes(), previous)
        self.assertEqual((backups / (result["backup"] + ".sha256")).read_text().strip(), publisher.digest(previous))
        self.assertEqual(result["previousSha256"], publisher.digest(previous))
        self.assertEqual([p.name for p in self.artifacts.iterdir()], ["evaluation.json"])

    def test_failed_validation_preserves_previous_and_creates_no_backup(self):
        self.target.write_bytes(b"previous")
        with self.assertRaises(publisher.PublishError):
            self.publish(validator=lambda _: (_ for _ in ()).throw(publisher.PublishError("rejected")))
        self.assertEqual(self.target.read_bytes(), b"previous")
        self.assertEqual(list((self.base / ".checking-evaluation-artifacts/backups/offline").iterdir()), [])

    def test_backup_failure_preserves_previous(self):
        self.target.write_bytes(b"previous")
        write = publisher.write_private

        def fail_backup(path, data):
            if path.parent.name == "offline":
                raise OSError("disk full")
            return write(path, data)

        with patch.object(publisher, "write_private", side_effect=fail_backup), self.assertRaises(OSError):
            self.publish()
        self.assertEqual(self.target.read_bytes(), b"previous")

    def test_deploy_change_during_validation_preserves_previous(self):
        self.target.write_bytes(b"previous")
        with self.assertRaises(publisher.PublishError):
            self.publish(identities=[(SHA, IMAGE), ("c" * 40, IMAGE)])
        self.assertEqual(self.target.read_bytes(), b"previous")

    def test_concurrent_file_change_is_not_overwritten(self):
        self.target.write_bytes(b"previous")

        def concurrent(command):
            self.validate(command)
            self.target.write_bytes(b"concurrent")

        with self.assertRaises(publisher.PublishError):
            self.publish(validator=concurrent)
        self.assertEqual(self.target.read_bytes(), b"concurrent")

    def test_input_cannot_be_live_destination(self):
        self.target.write_bytes(b"previous")
        with self.assertRaises(publisher.PublishError), patch.object(publisher, "run") as run:
            publisher.publish(self.target)
        run.assert_not_called()

    def test_oversized_empty_and_directory_inputs_rejected_before_docker(self):
        for content in (b"", b"x" * (publisher.MAX_BYTES + 1)):
            self.incoming.write_bytes(content)
            with self.assertRaises(publisher.PublishError), patch.object(publisher, "run") as run:
                publisher.publish(self.incoming)
            run.assert_not_called()
        with self.assertRaises(publisher.PublishError):
            publisher.read_regular(self.base)

    def test_symlink_input_and_destination_are_rejected(self):
        link = self.base / "link.json"
        try:
            link.symlink_to(self.incoming)
        except OSError:
            self.skipTest("Symbolic links unavailable to this test user")
        with self.assertRaises(publisher.PublishError):
            publisher.read_regular(link)
        self.target.symlink_to(self.incoming)
        with self.assertRaises(publisher.PublishError):
            publisher.publish(self.incoming)

    def test_relative_or_traversal_paths_are_rejected(self):
        for path in (Path("relative.json"), self.base / ".." / "input.json"):
            with self.assertRaises(publisher.PublishError):
                publisher.no_symlinks(path)

    def runtime(self, *, mount=None, tag=None, configured=None, dirty="", image_id=IMAGE):
        mount = mount if mount is not None else [{"Type": "bind", "RW": False,
                                                 "Source": str(self.artifacts), "Destination": "/app/admin-evaluation"}]
        values = [IMAGE, tag or f"me-nu-backend-checking:{SHA}", "10001:10001", True, "running", "healthy", mount]
        results = [SHA, dirty, "\n".join(json.dumps(value) for value in values), image_id,
                   publisher.CONTAINER_PATH if configured is None else configured]
        with patch.object(publisher, "run", side_effect=results) as run:
            identity = publisher.runtime_identity(self.artifacts)
        for call in run.call_args_list:
            if call.args[0][0] == "docker":
                self.assertEqual(call.args[0][:3], ["docker", "--context", "default"])
        return identity

    def test_runtime_checks_deployed_tag_mount_and_enabled_path(self):
        self.assertEqual(self.runtime(), (SHA, IMAGE))
        for arguments in ({"dirty": " M model/eval/code.py"}, {"tag": "me-nu-backend-main:wrong"},
                          {"mount": []}, {"configured": ""}, {"image_id": "sha256:" + "c" * 64},
                          {"mount": [{"Type": "bind", "RW": True, "Source": str(self.artifacts),
                                      "Destination": "/app/admin-evaluation"}]}):
            with self.subTest(arguments=arguments), self.assertRaises(publisher.PublishError):
                self.runtime(**arguments)

    def test_root_git_checks_only_trust_the_fixed_checkout(self):
        with patch.object(publisher, "run", side_effect=[SHA, "dirty"]) as run:
            with self.assertRaises(publisher.PublishError):
                publisher.runtime_identity(self.artifacts)
        for call in run.call_args_list:
            self.assertIn(f"safe.directory={self.root}", call.args[0])
            self.assertIn("--no-optional-locks", call.args[0])
            self.assertNotIn("--global", call.args[0])

    def test_validator_requires_immutable_image(self):
        with self.assertRaises(publisher.PublishError):
            publisher.validator_command("mutable:latest", self.incoming, self.base)

    def test_subprocess_errors_never_echo_sensitive_child_output(self):
        result = subprocess.CompletedProcess(["docker"], 1, "private-input", "private-secret")
        with patch.object(subprocess, "run", return_value=result), self.assertRaises(publisher.PublishError) as error:
            publisher.run(["docker"])
        self.assertNotIn("private", str(error.exception))

    def test_service_dataset_uses_separate_target_and_backup(self):
        self.target.write_bytes(b"offline previous")
        service = self.artifacts / "service-evaluation.json"
        service.write_bytes(b"service previous")
        with patch.object(publisher, "runtime_identity", return_value=(SHA, IMAGE)) as identity, \
                patch.object(publisher, "run", side_effect=self.validate):
            result = publisher.publish(self.incoming, "service")
        self.assertEqual(self.target.read_bytes(), b"offline previous")
        self.assertEqual(service.read_bytes(), b'{"canonical":true}')
        self.assertEqual(result["dataset"], "service")
        self.assertEqual(self.command[-2:], ["--dataset", "service"])
        self.assertTrue(all(call.args[1] == "service" for call in identity.call_args_list))
        backup = self.base / ".checking-evaluation-artifacts/backups/service" / (result["backup"] + ".json")
        self.assertEqual(backup.read_bytes(), b"service previous")

    def test_identical_valid_artifact_is_not_replaced_or_backed_up(self):
        self.target.write_bytes(b'{"canonical":true}')
        result = self.publish()
        self.assertFalse(result["published"])
        self.assertTrue(result["unchanged"])
        self.assertEqual(list((self.base / ".checking-evaluation-artifacts/backups/offline").iterdir()), [])

    def test_invalid_dataset_is_rejected_before_io(self):
        with patch.object(publisher, "run") as run, self.assertRaises(publisher.PublishError):
            publisher.publish(self.incoming, "../other")
        run.assert_not_called()

    def test_service_runtime_checks_service_setting(self):
        mounts = [{"Type": "bind", "RW": False, "Source": str(self.artifacts), "Destination": "/app/admin-evaluation"}]
        values = [IMAGE, f"me-nu-backend-checking:{SHA}", "10001:10001", True, "running", "healthy", mounts]
        replies = [SHA, "", "\n".join(json.dumps(item) for item in values), IMAGE, publisher.DATASETS["service"][2]]
        with patch.object(publisher, "run", side_effect=replies) as run:
            self.assertEqual(publisher.runtime_identity(self.artifacts, "service"), (SHA, IMAGE))
        self.assertIn("ADMIN_SERVICE_EVALUATION_PATH", run.call_args.args[0][-1])


if __name__ == "__main__":
    unittest.main()
