"""Checking isolation tests: Compose rendering and temporary files, no containers."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

MODULE = Path(__file__).with_name("validate.py")
spec = importlib.util.spec_from_file_location("checking_validate", MODULE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def values():
    result = dict(line.split("=", 1) for line in MODULE.with_name(".env.example").read_text().splitlines()
                  if line and not line.startswith("#"))
    result.update(CHECKING_IMAGE_TAG="a" * 40, CHECKING_POSTGRES_PASSWORD="0123456789abcdef" * 4,
                  CHECKING_REDIS_PASSWORD="fedcba9876543210" * 4)
    return result


@pytest.mark.parametrize("change", [
    {"CHECKING_IMAGE_TAG": "latest"}, {"CHECKING_POSTGRES_PASSWORD": "unchanged"},
    {"CHECKING_REDIS_PASSWORD": "0123456789abcdef" * 4}, {"CHECKING_FORWARDED_ALLOW_IPS": "*"},
    {"CHECKING_FORWARDED_ALLOW_IPS": "0.0.0.0"}, {"CHECKING_FORWARDED_ALLOW_IPS": "127.0.0.0/8"},
    {"CHECKING_KAKAO_OAUTH_ENABLED": "true"}, {"CHECKING_NAVER_OAUTH_ENABLED": "yes"},
    {"DATABASE_URL": "postgresql://old/labs"}, {"CHECKING_NAVER_CLIENT_ID": "$(command)"},
    {"CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED": "yes"},
])
def test_unsafe_values_rejected(change):
    with pytest.raises(guard.GuardError):
        guard.validate_values(values() | change)


def test_inherited_environment_cannot_override_file():
    with pytest.raises(guard.GuardError, match="inherited CHECKING_IMAGE_TAG"):
        guard.validate_values(values(), {"CHECKING_IMAGE_TAG": "b" * 40})


def test_enabled_provider_requires_separate_complete_configuration():
    configured = values() | {"CHECKING_KAKAO_OAUTH_ENABLED": "true", "CHECKING_KAKAO_CLIENT_ID": "synthetic-id",
                             "CHECKING_KAKAO_CLIENT_SECRET": "synthetic-secret"}
    assert guard.validate_values(configured) is configured


@pytest.fixture(scope="module")
def rendered():
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is unavailable; rendering does not require its daemon")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("CHECKING_") and key not in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES")}
    environment.update(values())
    result = subprocess.run(["docker", "--context", "default", "compose", "--env-file", str(MODULE.with_name(".env.example")),
                             "-p", "me-nu-checking", "-f", str(guard.ROOT / "compose.checking.yaml"),
                             "config", "--format", "json"],
                            capture_output=True, text=True, env=environment, cwd=guard.ROOT, timeout=30)
    assert result.returncode == 0, "Compose rendering failed; raw config is not printed"
    return json.loads(result.stdout)


def test_standalone_compose_is_isolated_and_bounded(rendered):
    guard.validate_config(rendered, values())
    services = rendered["services"]
    assert sum(int(services[name]["mem_limit"]) for name in ("api", "postgres", "redis")) == 1152 * 1024**2
    assert sum(float(services[name]["cpus"]) for name in ("api", "postgres", "redis")) == 1.0
    assert set(services) == {"api", "postgres", "redis", "migrate"}


def test_checking_preview_requires_matching_explicit_opt_in(rendered):
    assert values()["CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED"] == "false"
    enabled = values() | {"CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED": "true"}
    guard.validate_values(enabled)
    config = copy.deepcopy(rendered)
    for name in ("api", "migrate"):
        config["services"][name]["environment"]["CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED"] = "true"
    guard.validate_config(config, enabled)
    with pytest.raises(guard.GuardError, match="Carousel preview"):
        guard.validate_config(config, values())
    with pytest.raises(guard.GuardError, match="inherited CHECKING_CAROUSEL"):
        guard.validate_values(values(), {"CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED": "true"})


@pytest.mark.parametrize("change", [
    "project", "volume", "external_volume", "network", "public_data", "extra_service", "api_origin",
    "database", "cookie", "redis_prefix", "port", "public_port", "udp", "db_port", "memory", "cpu",
    "zero_memory", "dev_api", "dev_bypass", "llm", "tmdb", "openai", "redis", "pg_buffers", "image",
    "build_context", "entrypoint", "api_command", "migration_head", "migration_dob", "startup_gate",
    "api_mount", "db_mount", "privileged", "host_network", "writeable", "capabilities", "proxy", "oauth",
])
def test_config_cannot_cross_environment_or_enable_unsafe_runtime(rendered, change):
    config = copy.deepcopy(rendered)
    api = config["services"]["api"]
    env = api["environment"]
    if change == "project": config["name"] = "me-nu-labs"
    elif change == "volume": config["volumes"]["postgres-data"]["name"] = "me-nu-labs_postgres-data"
    elif change == "external_volume": config["volumes"]["redis-data"]["external"] = True
    elif change == "network": config["networks"]["data"]["external"] = True
    elif change == "public_data": config["networks"]["data"]["internal"] = False
    elif change == "extra_service": config["services"]["seed"] = {}
    elif change == "api_origin": env["OAUTH_PUBLIC_API_ORIGIN"] = "https://api.labs.me-nu.kr"
    elif change == "database": env["DATABASE_URL"] = "postgresql://old/labs"
    elif change == "cookie": env["SESSION_COOKIE_NAME"] = "menu_labs_session"
    elif change == "redis_prefix": env["REDIS_KEY_PREFIX"] = "me_nu_labs"
    elif change == "port": api["ports"][0]["published"] = "8001"
    elif change == "public_port": api["ports"][0]["host_ip"] = "0.0.0.0"
    elif change == "udp": api["ports"][0]["protocol"] = "udp"
    elif change == "db_port": config["services"]["postgres"]["ports"] = [dict(api["ports"][0])]
    elif change == "memory": api["mem_limit"] = 4 * 1024**3
    elif change == "cpu": api["cpus"] = 1.0
    elif change == "zero_memory": api["mem_limit"] = 0
    elif change == "dev_api": env["ENABLE_DEV_API"] = "true"
    elif change == "dev_bypass": env["ENABLE_DEV_AUTH_BYPASS"] = "true"
    elif change == "llm": env["LLM_KEY"] = "synthetic-key"
    elif change == "tmdb": env["TMDB_KEY"] = "synthetic-key"
    elif change == "openai": env["OPENAI_API_KEY"] = "synthetic-key"
    elif change == "redis": config["services"]["redis"]["command"][2] = "old-password"
    elif change == "pg_buffers": config["services"]["postgres"]["command"][2] = "shared_buffers=4GB"
    elif change == "image": api["image"] = "me-nu-backend-labs:" + "a" * 40
    elif change == "build_context": api["build"]["context"] = str(guard.ROOT)
    elif change == "entrypoint": api["entrypoint"] = ["sh"]
    elif change == "api_command": api["command"] = ["python", "other.py"]
    elif change == "migration_head": config["services"]["migrate"]["command"][-1] = "heads"
    elif change == "migration_dob": config["services"]["migrate"]["command"][-1] = "d2f6a9c4e731"
    elif change == "startup_gate": api["depends_on"]["migrate"]["condition"] = "service_started"
    elif change == "api_mount": api["volumes"] = [{"type": "bind", "source": "/", "target": "/host"}]
    elif change == "db_mount": config["services"]["postgres"]["volumes"][0]["source"] = "labs-data"
    elif change == "privileged": api["privileged"] = True
    elif change == "host_network": api["network_mode"] = "host"
    elif change == "writeable": api["read_only"] = False
    elif change == "capabilities": api["cap_drop"] = []
    elif change == "proxy": env["FORWARDED_ALLOW_IPS"] = "*"
    elif change == "oauth": env["KAKAO_OAUTH_ENABLED"] = "true"
    with pytest.raises(guard.GuardError):
        guard.validate_config(config, values())


def test_initialize_is_exclusive_and_never_prints_secret_values(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "deploy/checking"
    folder.mkdir(parents=True)
    shutil.copyfile(MODULE.with_name(".env.example"), folder / ".env.example")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "ENV_FILE", folder / ".env")
    passwords = iter([values()["CHECKING_POSTGRES_PASSWORD"], values()["CHECKING_REDIS_PASSWORD"]])
    monkeypatch.setattr(guard.secrets, "token_hex", lambda _: next(passwords))
    guard.initialize("a" * 40)
    original = guard.ENV_FILE.read_bytes()
    parsed = guard.read_environment(guard.ENV_FILE)
    assert parsed["CHECKING_KAKAO_OAUTH_ENABLED"] == parsed["CHECKING_NAVER_OAUTH_ENABLED"] == "false"
    output = capsys.readouterr().out
    assert parsed["CHECKING_POSTGRES_PASSWORD"] not in output
    assert parsed["CHECKING_REDIS_PASSWORD"] not in output
    monkeypatch.setattr(guard.secrets, "token_hex", lambda _: "a" * 64)
    with pytest.raises(FileExistsError):
        guard.initialize("b" * 40)
    assert guard.ENV_FILE.read_bytes() == original


def test_failed_compose_check_does_not_expose_raw_output(monkeypatch):
    monkeypatch.setattr(guard, "read_environment", lambda **_: values())
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="private-output", stderr="private-secret"))
    with pytest.raises(guard.GuardError) as error:
        guard.check()
    assert "private" not in str(error.value)


def test_check_uses_only_dedicated_compose_and_suppresses_resolved_values(rendered, monkeypatch, capsys):
    monkeypatch.setattr(guard, "read_environment", lambda **_: values())
    for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
        monkeypatch.setenv(key, "other-environment")
    calls = []

    def render(command, **options):
        calls.append(command)
        assert command[:4] == ["docker", "--context", "default", "compose"]
        assert command[-3:] == ["config", "--format", "json"]
        assert command[command.index("-p") + 1] == "me-nu-checking"
        assert command[command.index("-f") + 1] == str(guard.ROOT / "compose.checking.yaml")
        assert not any(key in options["env"] for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"))
        assert options["capture_output"] and options["timeout"] == 30
        return SimpleNamespace(returncode=0, stdout=json.dumps(rendered))

    monkeypatch.setattr(guard.subprocess, "run", render)
    guard.check()
    assert len(calls) == 1
    output = capsys.readouterr().out
    assert "Checking configuration valid" in output
    assert values()["CHECKING_POSTGRES_PASSWORD"] not in output
    assert values()["CHECKING_REDIS_PASSWORD"] not in output


def test_build_source_accepts_matching_head_and_clean_backend(monkeypatch):
    calls = []
    monkeypatch.setenv("GIT_DIR", "unrelated-repository")
    monkeypatch.setenv("GIT_WORK_TREE", "unrelated-worktree")

    def git(command, **options):
        calls.append(command)
        assert command[:6] == ["git", "--no-optional-locks", "-c", f"safe.directory={guard.ROOT}", "-C", str(guard.ROOT)]
        assert not any(key.startswith("GIT_") for key in options["env"])
        assert options["cwd"] == guard.ROOT and options["capture_output"] and options["timeout"] == 10
        if command[6:] == ["rev-parse", "--verify", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n")
        assert command[6:] == ["status", "--porcelain=v1", "--untracked-files=all", "--", "backend"]
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(guard.subprocess, "run", git)
    guard.validate_build_source(values())
    assert len(calls) == 2


@pytest.mark.parametrize("head_code,head_output,status_code,status_output", [
    (0, "b" * 40, 0, ""), (128, "private-git-error", 0, ""),
    (0, "a" * 40, 0, " M backend/app/main.py"),
    (0, "a" * 40, 0, "M  backend/app/main.py"),
    (0, "a" * 40, 0, "?? backend/app/untracked.py"),
    (0, "a" * 40, 128, "private-status-error"),
])
def test_build_source_rejects_wrong_head_dirty_backend_and_git_failures(monkeypatch, head_code, head_output, status_code, status_output):
    results = iter([SimpleNamespace(returncode=head_code, stdout=head_output),
                    SimpleNamespace(returncode=status_code, stdout=status_output)])
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **kw: next(results))
    with pytest.raises(guard.GuardError) as error:
        guard.validate_build_source(values())
    assert "private" not in str(error.value)


def test_check_build_rejects_bad_source_before_compose(monkeypatch):
    monkeypatch.setattr(guard, "read_environment", lambda **_: values())
    def reject(_):
        raise guard.GuardError("source mismatch")
    monkeypatch.setattr(guard, "validate_build_source", reject)
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **kw: pytest.fail("Compose must not run before source validation"))
    with pytest.raises(guard.GuardError, match="source mismatch"):
        guard.check(build=True)


def test_check_build_validates_source_before_rendering_config(rendered, monkeypatch):
    order = []
    monkeypatch.setattr(guard, "read_environment", lambda **_: values())
    monkeypatch.setattr(guard, "validate_build_source", lambda _: order.append("source"))
    def render(*args, **options):
        order.append("compose")
        return SimpleNamespace(returncode=0, stdout=json.dumps(rendered))
    monkeypatch.setattr(guard.subprocess, "run", render)
    guard.check(build=True)
    assert order == ["source", "compose"]


@pytest.mark.parametrize("action,build", [("check", False), ("check-build", True)])
def test_cli_selects_source_check_only_for_build(monkeypatch, action, build):
    calls = []
    monkeypatch.setattr(guard.sys, "argv", ["validate.py", action])
    monkeypatch.setattr(guard, "check", lambda **options: calls.append(options))
    guard.main()
    assert calls == [{"build": build}]


def test_shell_helper_is_fixed_to_checking_and_has_no_destructive_actions():
    helper = MODULE.with_name("checking.sh").read_text()
    assert "python3 deploy/checking/validate.py check" in helper
    assert "-p me-nu-checking -f compose.checking.yaml" in helper
    assert "unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES" in helper
    assert "(( $# > 1 ))" in helper
    assert 'if [[ "${1:-check}" == build ]]; then' in helper
    assert "python3 deploy/checking/validate.py check-build" in helper
    assert helper.index("validate.py check-build") < helper.index('"${compose[@]}" build api')
    assert "stop api" in helper
    assert not any(command in helper for command in (" down", "--volumes", "deploy/labs", "compose.labs", "seed-apply"))
