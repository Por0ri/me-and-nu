"""Opt-in engine limits without opening a database connection."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch, sentinel

from pydantic import ValidationError

from app.core.config import Settings


SESSION_PATH = Path(__file__).resolve().parents[1] / "app" / "db" / "session.py"
TEST_DATABASE_URL = "postgresql+psycopg://unit:synthetic@localhost:5432/pool_unit_test"


class DatabasePoolConfigTests(unittest.TestCase):
    def setUp(self):
        # Preserve OS essentials while isolating all application settings.
        isolated_env = {
            name: value for name, value in os.environ.items()
            if name.lower() not in Settings.model_fields
        }
        environment = patch.dict(os.environ, isolated_env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def config(self, **values):
        return Settings(_env_file=None, **({"database_url": TEST_DATABASE_URL} | values))

    def engine_call(self, config):
        # Load the real session module separately so existing app engines stay intact.
        spec = importlib.util.spec_from_file_location("pool_config_test_session", SESSION_PATH)
        module = importlib.util.module_from_spec(spec)
        with (
            patch("app.core.config.settings", config),
            patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=sentinel.engine) as create_engine,
            patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=sentinel.sessions),
        ):
            spec.loader.exec_module(module)
        create_engine.assert_called_once()
        return create_engine.call_args

    def test_unset_limits_preserve_existing_engine_defaults(self):
        config = self.config()
        self.assertIsNone(config.database_pool_size)
        self.assertIsNone(config.database_max_overflow)
        call = self.engine_call(config)
        self.assertEqual(call.args, (TEST_DATABASE_URL,))
        self.assertEqual(call.kwargs, {"echo": False})

    def test_main_environment_caps_pool_at_two_without_overflow(self):
        with patch.dict(os.environ, {"DATABASE_POOL_SIZE": "2", "DATABASE_MAX_OVERFLOW": "0"}):
            config = self.config()
        call = self.engine_call(config)
        self.assertEqual(call.kwargs, {"echo": False, "pool_size": 2, "max_overflow": 0})

    def test_each_limit_can_be_set_independently_without_overriding_other_defaults(self):
        for setting, engine_option, value in [
            ("database_pool_size", "pool_size", 2),
            ("database_max_overflow", "max_overflow", 0),
        ]:
            with self.subTest(setting=setting):
                call = self.engine_call(self.config(database_echo=True, **{setting: value}))
                self.assertEqual(call.kwargs, {"echo": True, engine_option: value})

    def test_sqlite_keeps_compatible_engine_options(self):
        for url in ["sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite:///isolated-test.db"]:
            with self.subTest(url=url):
                config = self.config(database_url=url, database_pool_size=2, database_max_overflow=0)
                call = self.engine_call(config)
                self.assertEqual(call.args, (url,))
                self.assertEqual(call.kwargs, {"echo": False})

    def test_pool_size_requires_positive_integer(self):
        for value in [0, -1, 1.5, "invalid"]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.config(database_pool_size=value)

    def test_overflow_requires_nonnegative_integer(self):
        for value in [-1, 1.5, "invalid"]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.config(database_max_overflow=value)


if __name__ == "__main__":
    unittest.main()
