import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("main_preflight", Path(__file__).with_name("preflight.py"))
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class PreflightSafetyTests(unittest.TestCase):
    def test_container_output_excludes_secrets_in_all_docker_fields(self):
        secret = "sensitive-value-must-not-leave-process"
        data = {
            "Name": "/me-nu-main-api-1", "Id": "container-id", "Image": "sha256:image",
            "Config": {"Image": "me-nu-backend-main:commit", "Cmd": [secret],
                       "Env": ["DATABASE_URL=" + secret, "REDIS_URL=" + secret,
                               "MAIN_LLM_KEY=" + secret, "SESSION_COOKIE_NAME=menu_main_session"],
                       "Labels": {"unknown": secret, "com.docker.compose.project": "me-nu-main"}},
            "State": {"Status": "running", "Error": secret,
                      "Health": {"Status": "healthy", "Log": [{"Output": secret}]}},
            "Args": [secret],
        }
        report = preflight.container_summary(data)
        self.assertNotIn(secret, json.dumps(report))
        self.assertEqual(report["settings"], {"SESSION_COOKIE_NAME": "menu_main_session"})
        self.assertEqual(report["health"], "healthy")

    def test_command_failure_never_exposes_stderr_or_stdout(self):
        failed = subprocess.CompletedProcess(["docker"], 1, "secret-stdout", "secret-stderr")
        with patch.object(preflight.subprocess, "run", return_value=failed):
            with self.assertRaises(preflight.ProbeError) as error:
                preflight.run(["docker", "inspect", "expected-name"])
        self.assertEqual(str(error.exception), "command_failed")

    def test_missing_required_containers_fail_checks_without_secret_output(self):
        with patch.object(preflight, "run", side_effect=preflight.ProbeError("hidden")), \
             patch.object(preflight, "urlopen", side_effect=OSError("hidden")), \
             patch.object(preflight, "resource_summary", return_value={}):
            report = preflight.collect()
        self.assertFalse(report["runtimeChecksPassed"])
        self.assertFalse(report["sameDatabaseObserved"])
        self.assertNotIn("hidden", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
