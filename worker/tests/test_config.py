"""Configuration boundaries keep the smoke runtime separate from service stores."""

import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from menu_worker.config import load_settings


class SettingsTests(unittest.TestCase):
    def test_defaults_use_dedicated_broker_and_result_databases(self):
        settings = load_settings({})
        self.assertEqual(settings.broker_url, "redis://redis-tasks:6379/0")
        self.assertEqual(settings.result_backend, "redis://redis-tasks:6379/1")
        self.assertEqual(settings.beat_interval_seconds, 30)

    def test_service_environment_is_not_used_as_runtime_configuration(self):
        settings = load_settings({
            "DATABASE_URL": "postgresql://service/production",
            "REDIS_URL": "redis://sessions:6379/0",
            "LLM_KEY": "must-not-be-used",
        })
        self.assertEqual(settings, load_settings({}))

    def test_broker_and_results_must_not_resolve_to_same_database(self):
        for backend in ("redis://redis-tasks/0", "rediss://redis-tasks:6379/00"):
            with self.subTest(backend=backend):
                with self.assertRaisesRegex(ValueError, "separate Redis databases"):
                    load_settings({"CELERY_RESULT_BACKEND": backend})

    def test_redis_database_must_be_explicit_and_url_valid(self):
        invalid = (
            "redis://host", "redis://host/", "redis://host/-1",
            "http://host/0", "redis:///0", "redis://host:invalid/0",
            "redis://host:70000/0", "redis://host/0?db=1",
            "redis://host/0#fragment", "redis://host name/0",
        )
        for key in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
            for value in invalid:
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        load_settings({key: value})

    def test_interval_bounds_and_type(self):
        for value in ("4", "3601", "0", "-1", "5.0", "nan", " 5", "", 5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    load_settings({"CELERY_BEAT_INTERVAL_SECONDS": value})
        for value in ("5", "3600"):
            with self.subTest(value=value):
                self.assertEqual(
                    load_settings({"CELERY_BEAT_INTERVAL_SECONDS": value}).beat_interval_seconds,
                    int(value),
                )

    def test_configuration_diagnostics_do_not_echo_credentials(self):
        marker = "private-password-marker"
        settings = load_settings({
            "CELERY_BROKER_URL": f"redis://user:{marker}@broker/0",
        })
        self.assertNotIn(marker, repr(settings))
        with self.assertRaises(ValueError) as caught:
            load_settings({"CELERY_BROKER_URL": f"redis://user:{marker}@broker/invalid"})
        self.assertNotIn(marker, str(caught.exception))


class FlowerConfigurationTests(unittest.TestCase):
    config = Path(__file__).resolve().parents[1] / "flowerconfig.py"

    def test_missing_or_multiple_credentials_fail_closed(self):
        for credential in ("", "user", "user:", ":password", "one:secret,two:secret"):
            with self.subTest(credential=credential):
                with patch.dict(os.environ, {"FLOWER_BASIC_AUTH": credential}):
                    with self.assertRaises(ValueError):
                        runpy.run_path(str(self.config))

    def test_authentication_and_read_only_monitoring_are_enabled(self):
        with patch.dict(os.environ, {"FLOWER_BASIC_AUTH": "unit:private-test-value"}):
            config = runpy.run_path(str(self.config))
        self.assertEqual(config["basic_auth"], ["unit:private-test-value"])
        self.assertTrue(config["read_only"])
        self.assertTrue(config["persistent"])


if __name__ == "__main__":
    unittest.main()
