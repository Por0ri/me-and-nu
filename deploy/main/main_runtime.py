"""Fixed Main operational boundaries; subprocess output and credentials stay private.

These helpers do not migrate, seed, stop Checking, or create data stores. A
separate reviewed handover must disable the legacy counterpart before Main
can schedule the same work against the preserved production database.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ["/usr/bin/docker", "--context", "default"]
API = "me-nu-main-api-1"
NETWORK = "me-nu-checking_data"  # Existing production network: its name is intentional.
SAFE_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}
ROLLOUT_LOCK = Path("/run/lock/me-nu-main-api-rollout.lock")
SCHEMA_PROBE = r'''
import json
ready = False
try:
    import os
    import psycopg
    from app.db.migration_policy import service_schema_ready
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn, connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=5000") as conn:
        database = conn.execute("SELECT current_database()").fetchone()[0]
        revisions = {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}
    ready = database == "me_nu_checking" and service_schema_ready(revisions)
except Exception:
    pass
print(json.dumps({"schemaReady": ready}))
'''


class RuntimeGuardError(ValueError):
    """Only fixed error codes, never command output or supplied values."""


def run(command, *, timeout=30, environment=None):
    try:
        result = subprocess.run(command, cwd=ROOT, env=SAFE_ENV | (environment or {}),
                                capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeGuardError("local_command_failed") from None
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise RuntimeGuardError("local_command_failed")
    return result.stdout.strip()


def require_host(filename):
    path = Path(filename).resolve()
    if (sys.platform != "linux" or os.geteuid() != 0 or ROOT.parent != Path("/home/ubuntu/apps")
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}", ROOT.name)
            or path.parent != ROOT / "deploy/main" or not (ROOT / "compose.main.yaml").is_file()
            or any(part.is_symlink() for part in (ROOT, *ROOT.parents))):
        raise RuntimeGuardError("verified_main_checkout_and_sudo_required")


@contextmanager
def lock(path=ROLLOUT_LOCK):
    import fcntl
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeGuardError("operation_in_progress") from None
        yield


def checkout_sha():
    git = ["git", "--no-optional-locks", "-c", f"safe.directory={ROOT}", "-C", str(ROOT)]
    sha = run([*git, "rev-parse", "HEAD"])
    if (not re.fullmatch(r"[0-9a-f]{40}", sha)
            or run([*git, "status", "--porcelain", "--untracked-files=all"])):
        raise RuntimeGuardError("clean_reviewed_main_checkout_required")
    return sha


def validate_main(item, sha):
    """Validate captured Docker inspect data, returning the DSN only in memory."""
    config = item.get("Config", {})
    labels = config.get("Labels") or {}
    host = item.get("HostConfig", {})
    state = item.get("State", {})
    entries = config.get("Env", [])
    if not isinstance(entries, list) or any(not isinstance(entry, str) or "=" not in entry for entry in entries):
        raise RuntimeGuardError("invalid_main_environment")
    environment = dict(entry.split("=", 1) for entry in entries)
    if len(environment) != len(entries):
        raise RuntimeGuardError("duplicate_main_environment")
    required = {"MENU_DEPLOYMENT_ENV": "production", "CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED": "false",
                "ENABLE_DEV_API": "false", "ENABLE_DEV_AUTH_BYPASS": "false",
                "REDIS_KEY_PREFIX": "me_nu_main", "SESSION_COOKIE_NAME": "menu_main_session",
                "EVENT_DATA_SOURCE": "local_demo"}
    image = item.get("Image", "")
    if (item.get("Name") != "/" + API or labels.get("com.docker.compose.project") != "me-nu-main"
            or labels.get("com.docker.compose.service") != "api"
            or config.get("Image") != f"me-nu-backend-main:{sha}" or config.get("User") != "10001:10001"
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", image)
            or state.get("Running") is not True or state.get("Health", {}).get("Status") != "healthy"
            or host.get("ReadonlyRootfs") is not True or host.get("Privileged") is not False
            or host.get("NetworkMode") in {"host", "none"}
            or set(item.get("NetworkSettings", {}).get("Networks", {})) != {NETWORK, "me-nu-main_egress"}
            or any(environment.get(key) != value for key, value in required.items())):
        raise RuntimeGuardError("main_runtime_identity_failed")
    database_url = environment.get("DATABASE_URL", "")
    try:
        url = urlsplit(database_url)
        valid = (url.scheme == "postgresql+psycopg" and url.hostname == "postgres" and url.port == 5432
                 and url.path == "/me_nu_checking" and url.username == "me_nu_checking"
                 and bool(url.password) and not url.query and not url.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeGuardError("main_production_database_required")
    return {"sha": sha, "image": image, "database_url": database_url}


def inspect_main():
    sha = checkout_sha()
    try:
        items = json.loads(run([*DOCKER, "inspect", API]))
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ValueError
        identity = validate_main(items[0], sha)
        networks = json.loads(run([*DOCKER, "network", "inspect", NETWORK]))
        if (not isinstance(networks, list) or len(networks) != 1
                or networks[0].get("Name") != NETWORK or networks[0].get("Internal") is not True):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise RuntimeGuardError("main_runtime_validation_failed") from None
    if run([*DOCKER, "image", "inspect", "--format", "{{.Id}}", f"me-nu-backend-main:{sha}"]) != identity["image"]:
        raise RuntimeGuardError("main_image_tag_changed")
    require_schema_ready()
    return identity


def require_schema_ready():
    """The deployed service policy is authoritative; never infer readiness from health."""
    try:
        result = json.loads(run([*DOCKER, "exec", "--user", "10001:10001", "-w", "/app", API,
                                 "python", "-c", SCHEMA_PROBE], timeout=20))
    except (ValueError, TypeError):
        raise RuntimeGuardError("main_service_schema_not_ready") from None
    if not isinstance(result, dict) or set(result) != {"schemaReady"} or result["schemaReady"] is not True:
        raise RuntimeGuardError("main_service_schema_not_ready")


def _unit_properties(unit):
    output = run(["/usr/bin/systemctl", "show", unit, "--property=LoadState,ActiveState,UnitFileState"])
    values = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in {"LoadState", "ActiveState", "UnitFileState"} or key in values:
            raise RuntimeGuardError("legacy_unit_state_unknown")
        values[key] = value
    if set(values) != {"LoadState", "ActiveState", "UnitFileState"}:
        raise RuntimeGuardError("legacy_unit_state_unknown")
    return values


def require_legacy_stopped(kind):
    if kind == "recommend":
        identifiers = run([*DOCKER, "ps", "--all", "--filter", "name=^/me-nu-checking-recommend$", "--format", "{{.ID}}"])
        if not identifiers:
            return
        try:
            items = json.loads(run([*DOCKER, "inspect", "me-nu-checking-recommend"]))
            item = items[0]
            stopped = (len(items) == 1 and item.get("Name") == "/me-nu-checking-recommend"
                       and item.get("State", {}).get("Status") in {"exited", "created"}
                       and item.get("State", {}).get("Running") is False
                       and item.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") in {"", "no"})
        except (ValueError, TypeError, IndexError, AttributeError):
            stopped = False
        if not stopped:
            raise RuntimeGuardError("legacy_recommend_must_be_stopped_and_restart_disabled")
        return
    if kind not in {"notices", "evaluation"}:
        raise RuntimeGuardError("unknown_legacy_operation")
    for suffix in ("timer", "service"):
        state = _unit_properties(f"me-nu-checking-{kind}.{suffix}")
        if state["ActiveState"] not in {"inactive", "failed"}:
            raise RuntimeGuardError("legacy_scheduler_must_be_inactive")
        if state["LoadState"] == "not-found":
            continue
        if (state["LoadState"] != "loaded"
                or suffix == "timer" and state["UnitFileState"] not in {"disabled", "masked", "masked-runtime"}):
            raise RuntimeGuardError("legacy_timer_must_be_disabled")
