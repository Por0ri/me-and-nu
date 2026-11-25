"""Render inactive Compose without a daemon, model, database or real credential."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
IMAGES = {
    "DAILY_GENERATION_IMAGE_ID": "sha256:" + "a" * 64,
    "DAILY_IMPORTER_IMAGE_ID": "sha256:" + "b" * 64,
}
HELP = {
    "generation-worker": "menu_worker.daily_resume",
    "daily-controller": "menu_worker.daily_resume",
    "pending-importer": "app.services.pending_content_import",
}
OFFLINE_DSN = "postgresql+psycopg://offline@127.0.0.1:1/offline_not_used"


def validate_inactive_config(config, image_ids):
    """Fail closed on an activation overlay or an unpinned image reference."""
    if set(image_ids) != set(IMAGES) or any(
        not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value)
        for value in image_ids.values()
    ):
        raise ValueError("exact_image_ids_required")
    if config.get("name") != "menu-daily-generation-inactive" or set(config.get("services", {})) != set(HELP):
        raise ValueError("inactive_services_changed")
    if any(config.get(key) for key in ("networks", "volumes", "secrets", "configs")):
        raise ValueError("inactive_external_resources_forbidden")
    for name, service in config["services"].items():
        allowed = {"cap_drop", "command", "cpus", "entrypoint", "environment", "healthcheck",
                   "image", "init", "labels", "logging", "mem_limit", "network_mode",
                   "pids_limit", "profiles", "read_only", "restart", "security_opt",
                   "stop_grace_period", "tmpfs", "user"}
        if set(service) - allowed:
            raise ValueError("inactive_service_option_forbidden")
        image_key = "DAILY_IMPORTER_IMAGE_ID" if name == "pending-importer" else "DAILY_GENERATION_IMAGE_ID"
        expected_env = ({"DATABASE_URL": OFFLINE_DSN, "MENU_DEPLOYMENT_ENV": "production",
                         "DATABASE_ECHO": "false", "CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED": "false"}
                        if name == "pending-importer" else {})
        if (service.get("image") != image_ids[image_key]
                or service.get("entrypoint") != ["python", "-B", "-m", HELP[name]]
                or service.get("command") != ["--help"]
                or service.get("environment", {}) != expected_env):
            raise ValueError("inactive_command_or_environment_changed")
        if (service.get("network_mode") != "none" or service.get("restart") != "no"
                or service.get("user") != "10001:10001" or service.get("read_only") is not True
                or service.get("init") is not True or service.get("profiles") != ["inactive"]
                or service.get("cap_drop") != ["ALL"]
                or service.get("security_opt") != ["no-new-privileges:true"]
                or service.get("healthcheck") != {"disable": True}):
            raise ValueError("inactive_runtime_boundary_changed")
        if service.get("tmpfs") != ["/tmp:rw,noexec,nosuid,size=32m"]:
            raise ValueError("inactive_tmpfs_changed")


def render(image_ids):
    # Only the two explicit image values enter interpolation. In particular,
    # ambient COMPOSE_FILE / COMPOSE_PROFILES must not select an activation file.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("COMPOSE_", "DAILY_"))}
    with tempfile.TemporaryDirectory(prefix="daily-inactive-render-") as directory:
        env_path = Path(directory) / "images.env"
        env_path.write_text("".join(f"{key}={value}\n" for key, value in image_ids.items()), encoding="utf-8")
        return subprocess.run(
            ["docker", "compose", "--env-file", str(env_path), "--profile", "inactive",
             "-f", str(ROOT / "compose.inactive.yaml"), "config", "--format", "json"],
            cwd=directory, env=environment, capture_output=True, text=True, timeout=30,
        )


class InactiveDeployTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("docker"):
            raise unittest.SkipTest("Docker Compose CLI is required; daemon is not used")
        result = render(IMAGES)
        if result.returncode:
            raise AssertionError("Inactive Compose failed to render: " + result.stderr)
        cls.config = json.loads(result.stdout)

    def test_rendered_contract_has_no_execution_or_connection(self):
        validate_inactive_config(self.config, IMAGES)

    def test_image_references_are_required_without_fallback(self):
        for missing in IMAGES:
            with self.subTest(missing=missing):
                result = render({key: value for key, value in IMAGES.items() if key != missing})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(missing, result.stderr)

    def test_mutable_image_references_are_rejected_by_verification(self):
        for value in ("me-nu-anime-generation:latest", "sha256:short", "", "sha256:" + "G" * 64):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "image_ids"):
                validate_inactive_config(self.config, {**IMAGES, "DAILY_GENERATION_IMAGE_ID": value})

    def test_ambient_activation_settings_are_not_inherited(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"COMPOSE_FILE": str(ROOT / "compose.approved.yaml"),
                                     "COMPOSE_PROFILES": "manual", "DAILY_BUDGET_USD": "999",
                                     "DAILY_GENERATION_IMAGE_ID": "unreviewed:latest"}):
            result = render(IMAGES)
        self.assertEqual(result.returncode, 0, result.stderr)
        validate_inactive_config(json.loads(result.stdout), IMAGES)

    def test_activation_commands_or_credentials_are_rejected(self):
        for name in HELP:
            for key, value in (("command", ["--apply"]), ("entrypoint", ["celery"]),
                               ("environment", {"OPENAI_API_KEY": "synthetic-not-a-key"})):
                with self.subTest(name=name, key=key):
                    config = copy.deepcopy(self.config)
                    config["services"][name][key] = value
                    with self.assertRaises(ValueError):
                        validate_inactive_config(config, IMAGES)

    def test_network_mount_port_and_restart_changes_are_rejected(self):
        for key, value in (("network_mode", "host"), ("volumes", ["/tmp:/host"]),
                           ("ports", ["8000:8000"]), ("restart", "unless-stopped"),
                           ("privileged", True), ("healthcheck", {"test": ["CMD", "unsafe"]}),
                           ("post_start", [{"command": "unsafe"}])):
            with self.subTest(key=key):
                config = copy.deepcopy(self.config)
                config["services"]["generation-worker"][key] = value
                with self.assertRaises(ValueError):
                    validate_inactive_config(config, IMAGES)

    def test_extra_service_or_top_level_resources_are_rejected(self):
        for key, value in (("networks", {"tasks": {"external": True}}),
                           ("volumes", {"data": {}}), ("secrets", {"key": {}})):
            with self.subTest(key=key):
                config = copy.deepcopy(self.config)
                config[key] = value
                with self.assertRaises(ValueError):
                    validate_inactive_config(config, IMAGES)
        config = copy.deepcopy(self.config)
        config["services"]["generation-dispatcher"] = {}
        with self.assertRaises(ValueError):
            validate_inactive_config(config, IMAGES)

    def test_template_contains_only_empty_image_values(self):
        values = {}
        for line in (ROOT / "inactive.env.example").read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value
        self.assertEqual(values, {key: "" for key in IMAGES})


if __name__ == "__main__":
    unittest.main()
