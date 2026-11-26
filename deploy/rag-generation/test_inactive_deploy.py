"""Resolve reviewed compose files only; never start Docker services or providers."""
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ARTIFACTS = "/home/ubuntu/me-nu-beta-20261116/artifacts"
STATE = "/home/ubuntu/me-nu-beta-20261116/state"


def resolved(files, *, overrides=None):
    # A dedicated empty env file prevents accidental use of repository secrets.
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "HOME",
                              "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
                              "HOMEDRIVE", "HOMEPATH", "DOCKER_CONFIG", "DOCKER_CLI_PLUGIN_EXTRA_DIRS"}}
    env.update({"RAG_RELEASE_TAG": "isolated-config-test", "RAG_WORKER_IMAGE": "sha256:" + "1" * 64,
        "RAG_IMPORTER_IMAGE": "sha256:" + "2" * 64, "CONTENT_JOB_DB_PASSWORD": "isolated-placeholder",
        "RAG_VECTOR_IMAGE": "sha256:" + "3" * 64, "RAG_VECTOR_DATA_HOST_DIR": "/prepared/vector/pgdata",
        "RAG_VECTOR_OWNER_PASSWORD_FILE": "/prepared/vector/password",
        "RAG_ISOLATED_STATE_HOST_DIR": "/prepared/rag/state", "RAG_IMAGE_HOST_DIR": "/prepared/rag/images",
        "RAG_RUNTIME_CONFIG_HOST_FILE": "/prepared/rag/runtime.json",
        "RAG_VECTOR_DSN_HOST_FILE": "/prepared/rag/vector-dsn", "RAG_VECTOR_NETWORK": "isolated-rag-private",
        "RAG_INBOX_NAME": "rag-inbox-isolated", "RAG_GENERATION_ENABLED": "true",
        "DAILY_RELEASE_TAG": "preserved-existing", "MAIN_POSTGRES_PASSWORD": "isolated-placeholder",
        "DAILY_INBOX_NAME": "existing-period-inbox", "DAILY_IMPORT_POLICY_HOST_FILE": "/prepared/period-policy.json"})
    env.update(overrides or {})
    with TemporaryDirectory(prefix="rag-compose-check-") as temp:
        env_file = Path(temp) / "empty.env"
        env_file.write_text("", encoding="utf-8")
        command = ["docker", "compose", "--env-file", str(env_file)]
        for path in files:
            command.extend(("-f", str(path)))
        command.extend(("--profile", "*", "config", "--format", "json"))
        result = subprocess.run(command, env=env, capture_output=True, timeout=30)
        if result.returncode:
            # Never print a resolved configuration or inherited credentials.
            raise AssertionError("compose_config_failed: " + result.stderr.decode(errors="replace")[-2000:])
        return json.loads(result.stdout)


def mounts(service):
    return {row["target"]: row for row in service.get("volumes", [])}


def assert_pinned_images(config):
    for service in config["services"].values():
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", service["image"]):
            raise ValueError("runtime_image_must_be_reviewed_digest")


class InactiveDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = resolved([HERE / "compose.runtime.yaml"])
        cls.inactive = resolved([HERE / "compose.runtime.yaml", HERE / "compose.inactive.yaml"])

    def test_no_new_db_broker_or_existing_article_services(self):
        for config in (self.base, self.inactive):
            self.assertEqual(set(config["services"]), {"rag-worker", "rag-pending-importer"})
            self.assertTrue(all(row["external"] for row in config["networks"].values()))
            self.assertEqual(config["networks"]["tasks"]["name"], "menu-beta-generation-20261116_tasks")
            self.assertEqual(config["networks"]["data"]["name"], "me-nu-checking_data")

    def test_default_help_and_off_survive_true_ambient_flag(self):
        self.assertTrue(all(service["command"][-1] == "--help" for service in self.base["services"].values()))
        for config in (self.base, self.inactive):
            self.assertEqual(config["services"]["rag-worker"]["environment"]["RAG_GENERATION_ENABLED"], "false")
            self.assertEqual(config["services"]["rag-pending-importer"]["command"], ["--help"])
        command = self.inactive["services"]["rag-worker"]["command"]
        self.assertIn("--queues=content.rag_generate", command)
        self.assertIn("--concurrency=1", command)

    def test_runtime_images_are_digest_pinned(self):
        assert_pinned_images(self.base)
        unsafe = json.loads(json.dumps(self.base))
        unsafe["services"]["rag-worker"]["image"] = "me-nu-rag-generation:latest"
        with self.assertRaisesRegex(ValueError, "reviewed_digest"):
            assert_pinned_images(unsafe)

    def test_no_provider_key_egress_ports_or_automatic_restart(self):
        for config in (self.base, self.inactive):
            for service in config["services"].values():
                self.assertFalse(service.get("ports"))
                self.assertEqual(service["restart"], "no")
                self.assertEqual(service["user"], "10001:10001")
                self.assertTrue(service["read_only"])
                self.assertNotIn("generation-egress", service["networks"])
                for key, value in service.get("environment", {}).items():
                    if "LLM_KEY" in key or key == "OPENAI_API_KEY": self.assertEqual(value, "")
                self.assertFalse(any("rag-key" in value or "anime_llm_key" in value for value in mounts(service)))
                self.assertTrue(all(row["type"] == "bind" and row["bind"]["create_host_path"] is False
                                    for row in service.get("volumes", [])))

    def test_exact_artifact_and_inbox_paths_with_minimal_write_scope(self):
        worker, importer = (mounts(self.base["services"][name]) for name in ("rag-worker", "rag-pending-importer"))
        self.assertEqual(worker["/var/lib/menu-jobs"]["source"], ARTIFACTS)
        self.assertEqual(importer["/var/lib/menu-jobs"]["source"], ARTIFACTS)
        self.assertFalse(worker["/var/lib/menu-jobs"].get("read_only", False))
        self.assertTrue(all(value["read_only"] for value in importer.values()))
        inbox = "/runtime/state/rag-inbox-isolated"
        self.assertEqual(importer[inbox]["source"], STATE + "/rag-inbox-isolated")
        self.assertFalse(worker["/var/lib/menu-rag"].get("read_only", False))
        for target in ("/opt/rag-images", "/run/rag/runtime.json", "/run/secrets/rag-vector-dsn"):
            self.assertTrue(worker[target]["read_only"])

    def test_controller_overlay_only_adds_explicit_inactive_mounts(self):
        original_file = ROOT / "deploy/daily-generation/compose.runtime.yaml"
        old = resolved([original_file])
        new = resolved([original_file, HERE / "compose.controller.yaml"])
        for name in ("generation-worker", "pending-importer"):
            self.assertEqual(old["services"][name], new["services"][name])
        before, after = (value["services"]["daily-controller"] for value in (old, new))
        for key in ("image", "command", "entrypoint", "networks", "profiles", "restart"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after["environment"]["RAG_GENERATION_ENABLED"], "false")
        self.assertTrue(all(mounts(after)[key] == value for key, value in mounts(before).items()))
        self.assertTrue(mounts(after)["/var/lib/menu-rag"]["read_only"])
        self.assertTrue(mounts(after)["/run/rag/runtime.json"]["read_only"])
        self.assertEqual(mounts(after)["/runtime/state/rag-inbox-isolated"]["source"], STATE + "/rag-inbox-isolated")

    def test_build_is_separate_and_copies_no_runtime_files(self):
        config = resolved([HERE / "compose.build.yaml"])
        for service in config["services"].values():
            self.assertEqual(service["network_mode"], "none")
            self.assertFalse(service.get("volumes"))
            self.assertFalse(service.get("ports"))
        contexts = config["services"]["rag"]["build"]["additional_contexts"]
        self.assertEqual(contexts["runtime-base"], "service:worker-base")
        self.assertEqual(set(contexts), {"runtime-base", "rag-project", "anime-works"})

    def test_storage_has_private_network_and_no_service_database_mount(self):
        config = resolved([HERE / "compose.storage.yaml"])
        self.assertEqual(set(config["services"]), {"vector-db"})
        assert_pinned_images(config)
        self.assertTrue(config["networks"]["rag-vector"]["internal"])
        database = config["services"]["vector-db"]
        self.assertFalse(database.get("ports"))
        self.assertEqual(database["environment"]["POSTGRES_DB"], "menu_rag_vector")
        self.assertNotIn("POSTGRES_PASSWORD", database["environment"])
        self.assertTrue(all(value["source"].startswith("/prepared/vector/") for value in mounts(database).values()))


if __name__ == "__main__":
    unittest.main()
