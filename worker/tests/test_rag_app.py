"""The real Celery entry point remains disabled and transport-isolated."""
import importlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch


class RagAppTests(unittest.TestCase):
    def load(self, **environment):
        import menu_worker.rag_app as module
        with patch.dict(os.environ, environment, clear=True):
            return importlib.reload(module)

    def test_default_off_does_not_open_config_credentials_or_database(self):
        module = self.load()
        with patch.dict(os.environ, {}, clear=True), patch.object(
            Path, "read_text", side_effect=AssertionError("No files may be opened")):
            result = module.runner_factory()("00000000-0000-4000-8000-000000000001")
        self.assertFalse(result["processed"])
        self.assertEqual(result["reason"], "rag_generation_disabled")

    def test_queue_is_explicit_and_has_no_beat_schedule(self):
        module = self.load()
        self.assertEqual(tuple(queue.name for queue in module.app.conf.task_queues), ("content.rag_generate",))
        self.assertFalse(module.app.conf.task_create_missing_queues)
        self.assertFalse(module.app.conf.beat_schedule)
        self.assertEqual(module.app.conf.result_backend_transport_options["global_keyprefix"], "menu-rag-generation:")
        self.assertEqual(module.app.conf.worker_prefetch_multiplier, 1)

    def test_service_and_fake_brokers_are_rejected(self):
        for host in ("redis", "redis-tasks", "anime-redis", "localhost"):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "dedicated generation broker"):
                self.load(CELERY_BROKER_URL=f"redis://{host}:6379/0")
        self.load()

    def test_disabled_runtime_config_never_instantiates_provider(self):
        module = self.load()
        with patch.dict(os.environ, {"RAG_GENERATION_ENABLED": "false",
                                    "RAG_RUNTIME_CONFIG_FILE": "/does/not/exist"}, clear=True):
            self.assertEqual(module.runner_factory()("x")["reason"], "rag_generation_disabled")
