"""Preparation and real Settings checks, without credentials or external IO."""

from contextlib import redirect_stdout
import copy
import importlib.util
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


MODULE = Path(__file__).with_name("validate.py")
ROOT = MODULE.resolve().parents[2]
spec = importlib.util.spec_from_file_location("main_prepare_validate", MODULE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class MainPreparationTest(unittest.TestCase):
    def values(self):
        return guard.read_manifest(guard.EXAMPLE)

    def test_template_records_target_without_authorizing_generation_or_deployment(self):
        report = guard.validate(self.values())
        self.assertEqual(report["daily_approved_content_target"], 20)
        self.assertFalse(report["generation_authorized"])
        self.assertFalse(report["deployment_performed"])
        self.assertFalse(report["live_checks_performed"])
        self.assertFalse(report["daily_scheduler_implemented"])
        self.assertEqual(set(report["generation_decisions_pending"]), set(guard.GENERATION_LIMITS))
        self.assertEqual(self.values()["MAIN_DATA_STRATEGY"], "reuse_checking")
        self.assertIn("MAIN_IMAGE_TAG", report["release_blockers"])
        self.assertIn("MAIN_DB_CAPACITY_VERIFIED", report["release_blockers"])

    def test_preparation_passes_but_unresolved_preflight_fails(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(guard.main(["prepare"]), 0)
            self.assertEqual(guard.main(["preflight"]), 2)

    def test_main_isolation_and_disabled_generation_are_required(self):
        changes = {
            "MENU_DEPLOYMENT_ENV": "checking",
            "FRONTEND_ORIGINS": "https://checking.me-nu.kr",
            "OAUTH_PUBLIC_API_ORIGIN": "https://api.checking.me-nu.kr",
            "SESSION_COOKIE_SECURE": "false",
            "SESSION_COOKIE_NAME": "menu_checking_session",
            "REDIS_KEY_PREFIX": "me_nu_checking",
            "ENABLE_DEV_API": "true",
            "ENABLE_DEV_AUTH_BYPASS": "true",
            "CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED": "true",
            "MAIN_PLAN_AUTO_COLLECTION_ENABLED": "true",
            "MAIN_PLAN_PAID_GENERATION_ENABLED": "true",
        }
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaises(guard.GuardError):
                guard.validate(self.values() | {key: value})

    def test_separate_attempt_cap_is_not_approved_content_target(self):
        report = guard.validate(self.values() | {"MAX_GENERATION_ATTEMPTS_PER_DAY": "5"})
        self.assertEqual(report["daily_approved_content_target"], 20)
        self.assertFalse(report["generation_authorized"])
        for value in ("0", "-1", "20.5", "unlimited"):
            with self.subTest(value=value), self.assertRaises(guard.GuardError):
                guard.validate(self.values() | {"MAX_GENERATION_ATTEMPTS_PER_DAY": value})

    def test_budget_and_end_time_are_independently_validated(self):
        for key, value in (
            ("GENERATION_DAILY_BUDGET_USD", "NaN"),
            ("GENERATION_DAILY_BUDGET_USD", "Infinity"),
            ("GENERATION_DAILY_BUDGET_USD", "0"),
            ("GENERATION_ENDS_AT", "2026-11-17T23:59:59"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(guard.GuardError):
                guard.validate(self.values() | {key: value})
        report = guard.validate(self.values() | {
            "MAX_GENERATION_ATTEMPTS_PER_DAY": "30",
            "GENERATION_DAILY_BUDGET_USD": "5.00",
            "GENERATION_ENDS_AT": "2026-11-17T23:59:59+09:00",
        })
        self.assertEqual(report["generation_decisions_pending"], [])
        self.assertFalse(report["generation_authorized"])

    def test_existing_data_requires_matching_explicit_strategy(self):
        guard.validate(self.values() | {
            "MAIN_DATA_STRATEGY": "reuse_checking", "MAIN_TARGET_DATABASE": "me_nu_checking",
        })
        for strategy, database in (("reuse_checking", "me_nu"), ("curated_migration", "me_nu_checking")):
            with self.subTest(strategy=strategy), self.assertRaises(guard.GuardError):
                guard.validate(self.values() | {"MAIN_DATA_STRATEGY": strategy, "MAIN_TARGET_DATABASE": database})

    def test_no_credentials_or_unexpected_settings_are_accepted_or_printed(self):
        secret = "synthetic-secret-never-print"
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "plan.env"
            for line in (f"DATABASE_URL={secret}", f"MAIN_DATA_STRATEGY={secret}\nMAIN_DATA_STRATEGY=", f"MAIN_DATA_STRATEGY='{secret}'"):
                manifest.write_text(line, encoding="utf-8")
                output = io.StringIO()
                with self.subTest(line=line), redirect_stdout(output):
                    self.assertEqual(guard.main(["prepare", "--env", str(manifest)]), 2)
                self.assertNotIn(secret, output.getvalue())

    def test_complete_declarations_still_do_not_authorize_runtime_actions(self):
        values = self.values() | {key: "true" for key in guard.ATTESTATIONS} | {
            "MAIN_IMAGE_TAG": "a" * 40, "MAIN_DATA_STRATEGY": "reuse_checking",
            "MAIN_TARGET_DATABASE": "me_nu_checking", "MAIN_EVENT_DATA_SOURCE": "local_demo",
            "MAIN_POSTGRES_PASSWORD": "0123456789abcdef" * 4,
            "MAIN_REDIS_PASSWORD": "fedcba9876543210" * 4,
            "MAIN_KAKAO_OAUTH_ENABLED": "true", "MAIN_KAKAO_CLIENT_ID": "synthetic",
            "MAIN_KAKAO_CLIENT_SECRET": "synthetic",
        }
        report = guard.validate(values)
        self.assertEqual(report["release_blockers"], [])
        self.assertFalse(report["live_checks_performed"])
        self.assertFalse(report["generation_authorized"])
        self.assertFalse(report["deployment_performed"])

    def test_real_backend_settings_accept_main_oauth_and_reject_checking_preview(self):
        # Copy unchanged sources so importing module-level Settings cannot read
        # a developer's backend/.env. Nothing imports a DB session or HTTP client.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            package = directory / "app" / "core"
            package.mkdir(parents=True)
            for name in ("config.py", "carousel_preview.py"):
                shutil.copyfile(ROOT / "backend" / "app" / "core" / name, package / name)
            environment = {key: value for key, value in os.environ.items()
                           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
            environment.update(PYTHONPATH=str(directory), DATABASE_URL="postgresql://unused:synthetic@localhost/unused")
            result = subprocess.run([sys.executable, "-c", '''
import json
from pydantic import ValidationError
from app.core.config import Settings
from app.core.carousel_preview import checking_preview_database
base = dict(database_url="postgresql+psycopg://me_nu_checking:synthetic@postgres:5432/me_nu_checking",
    menu_deployment_env="production", frontend_origins="https://www.me-nu.kr,https://api.me-nu.kr",
    oauth_frontend_origin="https://www.me-nu.kr", oauth_public_api_origin="https://api.me-nu.kr",
    session_backend="redis", redis_url="redis://localhost:6379/0", redis_key_prefix="me_nu_main",
    session_cookie_name="menu_main_session", session_cookie_secure=True,
    enable_dev_api=False, enable_dev_auth_bypass=False,
    kakao_oauth_enabled=True, kakao_client_id="synthetic", kakao_client_secret="synthetic",
    naver_oauth_enabled=True, naver_client_id="synthetic", naver_client_secret="synthetic")
for provenance in ("local_demo", "production"):
    config = Settings(_env_file=None, **base, event_data_source=provenance)
    assert config.cors_origins == ["https://www.me-nu.kr", "https://api.me-nu.kr"]
    assert checking_preview_database(config) is None
    for provider in ("kakao", "naver"):
        assert config.oauth_callback_url(provider) == f"https://api.me-nu.kr/api/v1/auth/login/{provider}/callback"
    try:
        Settings(_env_file=None, **base, event_data_source=provenance, checking_carousel_test_preview_enabled=True)
    except ValidationError:
        pass
    else:
        raise AssertionError("Main must never inherit Checking preview")
print(json.dumps({"main_settings": "passed", "llm_calls": 0, "database_connections": 0}))
'''], cwd=directory, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {
                "main_settings": "passed", "llm_calls": 0, "database_connections": 0,
            })

    def test_api_only_compose_is_bounded_and_reuses_existing_data(self):
        self.assertIsNotNone(shutil.which("docker"), "Docker Compose CLI is required for offline rendering only")
        values = self.values() | {
            "MAIN_IMAGE_TAG": "a" * 40, "MAIN_POSTGRES_PASSWORD": "0123456789abcdef" * 4,
            "MAIN_REDIS_PASSWORD": "fedcba9876543210" * 4,
        }
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "main.env"
            env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
            environment = {key: value for key, value in os.environ.items()
                           if key not in guard.KEYS and not key.startswith(("COMPOSE_", "MAIN_"))}
            rendered = subprocess.run(["docker", "--context", "default", "compose", "--env-file", str(env_file),
                "-p", "me-nu-main", "-f", str(ROOT / "compose.main.yaml"), "config", "--format", "json"],
                env=environment, cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(rendered.returncode, 0, "Offline Compose rendering failed; credential-bearing output suppressed")
            config = json.loads(rendered.stdout)
            output = io.StringIO()
            with patch.dict(os.environ, {"MAIN_IMAGE_TAG": "inherited-wrong-tag", "COMPOSE_PROFILES": "generation"}), redirect_stdout(output):
                self.assertEqual(guard.main(["prepare", "--env", str(env_file), "--check-compose"]), 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report["compose_config_verified"])
            self.assertFalse(report["deployment_performed"])
            self.assertNotIn(values["MAIN_POSTGRES_PASSWORD"], output.getvalue())
            self.assertNotIn(values["MAIN_REDIS_PASSWORD"], output.getvalue())
        guard.validate_config(config, values)
        mutations = {
            "public_port": lambda c: c["services"]["api"]["ports"][0].update(host_ip="0.0.0.0"),
            "new_database": lambda c: c["services"].update(postgres={}),
            "migration": lambda c: c["services"]["api"].update(command=["alembic", "upgrade", "head"]),
            "secret_mount": lambda c: c["services"]["api"].update(volumes=["/secret:/key"]),
            "llm_key": lambda c: c["services"]["api"]["environment"].update(LLM_KEY="synthetic"),
            "checking_cookie": lambda c: c["services"]["api"]["environment"].update(SESSION_COOKIE_NAME="menu_checking_session"),
            "preview": lambda c: c["services"]["api"]["environment"].update(CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED="true"),
            "unbounded_pool": lambda c: c["services"]["api"]["environment"].pop("DATABASE_MAX_OVERFLOW"),
            "fresh_network": lambda c: c["networks"]["data"].update(external=False),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), self.assertRaises(guard.GuardError):
                modified = copy.deepcopy(config)
                mutate(modified)
                guard.validate_config(modified, values)


if __name__ == "__main__":
    unittest.main()
