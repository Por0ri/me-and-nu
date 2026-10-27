"""Isolation regression tests; only Compose rendering, never starts containers."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

MODULE = Path(__file__).with_name("validate.py")
spec = importlib.util.spec_from_file_location("labs_validate", MODULE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def values():
    result = dict(line.split("=", 1) for line in MODULE.with_name(".env.example").read_text().splitlines()
                  if line and not line.startswith("#"))
    result.update(LABS_IMAGE_TAG="a" * 40, LABS_POSTGRES_PASSWORD="0123456789abcdef" * 4,
                  LABS_REDIS_PASSWORD="fedcba9876543210" * 4)
    return result


@pytest.mark.parametrize("change", [
    {"LABS_IMAGE_TAG": "latest"}, {"LABS_POSTGRES_PASSWORD": "unchanged"},
    {"LABS_REDIS_PASSWORD": "0123456789abcdef" * 4}, {"LABS_FORWARDED_ALLOW_IPS": "*"},
    {"LABS_FORWARDED_ALLOW_IPS": "0.0.0.0"}, {"LABS_KAKAO_OAUTH_ENABLED": "true"},
    {"DATABASE_URL": "postgresql://old/staging"}, {"LABS_NAVER_CLIENT_ID": "$(command)"},
])
def test_unsafe_inputs_rejected(change):
    with pytest.raises(guard.GuardError):
        guard.validate_values(values() | change)


def test_inherited_environment_cannot_override_file():
    with pytest.raises(guard.GuardError, match="inherited LABS_IMAGE_TAG"):
        guard.validate_values(values(), {"LABS_IMAGE_TAG": "b" * 40})


@pytest.fixture(scope="module")
def rendered():
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is unavailable; semantic rendering test requires its CLI only")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("LABS_") and key not in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES")}
    environment.update(values())
    result = subprocess.run(["docker", "--context", "default", "compose", "--env-file", str(MODULE.with_name(".env.example")),
                             "-p", "me-nu-labs", "-f", str(guard.ROOT / "compose.labs.yaml"),
                             "--profile", "tools", "config", "--format", "json"],
                            capture_output=True, text=True, env=environment, cwd=guard.ROOT, timeout=30)
    assert result.returncode == 0, "Compose rendering failed; output suppressed to avoid logging config"
    return json.loads(result.stdout)


def test_standalone_compose_passes_guard(rendered):
    guard.validate_config(rendered, values())


@pytest.mark.parametrize("change", ["project", "volume", "api_origin", "database", "cookie", "port", "network", "memory", "seed_mount", "dev_api", "redis"])
def test_compose_cannot_target_staging_or_escape_caps(rendered, change):
    config = copy.deepcopy(rendered)
    api = config["services"]["api"]
    if change == "project": config["name"] = "me-nu"
    elif change == "volume": config["volumes"]["postgres-data"]["name"] = "me-nu_postgres-data"
    elif change == "api_origin": api["environment"]["OAUTH_PUBLIC_API_ORIGIN"] = "https://api.test.me-nu.kr"
    elif change == "database": api["environment"]["DATABASE_URL"] = "postgresql://old/staging"
    elif change == "cookie": api["environment"]["SESSION_COOKIE_NAME"] = "menu_staging_session"
    elif change == "port": api["ports"][0]["published"] = "8000"
    elif change == "network": config["networks"]["data"]["external"] = True
    elif change == "memory": api["mem_limit"] = 4 * 1024**3
    elif change == "seed_mount": config["services"]["seed"]["volumes"][0]["read_only"] = False
    elif change == "dev_api": api["environment"]["ENABLE_DEV_API"] = "true"
    elif change == "redis": config["services"]["redis"]["command"][2] = "old-password"
    with pytest.raises(guard.GuardError):
        guard.validate_config(config, values())
