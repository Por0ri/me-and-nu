"""Read-only runtime evidence for Main promotion; never emit environment secrets.

Run on the existing server: python3 deploy/main/preflight.py --sudo
This does not deploy, migrate, stop services, or attest that backups/UI checks passed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
from urllib.request import urlopen


REQUIRED = (
    "me-nu-main-api-1", "me-nu-checking-api-1",
    "me-nu-checking-postgres-1", "me-nu-checking-redis-1",
)
OPTIONAL = ("me-nu-checking-recommend", "me-nu-main-recommend")
TIMERS = (
    "me-nu-checking-notices.timer", "me-nu-checking-notices.service",
    "me-nu-checking-evaluation.timer", "me-nu-checking-evaluation.service",
    "me-nu-main-notices.timer", "me-nu-main-notices.service",
    "me-nu-main-evaluation.timer", "me-nu-main-evaluation.service",
    "me-nu-main-debates.timer", "me-nu-main-debates.service",
)
SAFE_ENV = (
    "MENU_DEPLOYMENT_ENV", "SESSION_BACKEND", "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_SECURE", "REDIS_KEY_PREFIX", "CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED",
    "DEBATE_ENABLED", "DEBATE_VOTE_THRESHOLD",
)
PROBE = r'''
import json, os, socket
from urllib.parse import urlsplit
import psycopg
from redis import Redis
result = {}
try:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn, connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=5000") as conn:
        row = conn.execute("SELECT current_database(), inet_server_addr()::text, inet_server_port()").fetchone()
        revisions = [r[0] for r in conn.execute("SELECT version_num FROM alembic_version ORDER BY version_num")]
    result["database"] = dict(zip(("name", "server", "port"), row))
    result["revisions"] = revisions
except Exception as exc:
    result["databaseErrorType"] = type(exc).__name__
try:
    parsed = urlsplit(os.environ["REDIS_URL"])
    with Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=5, socket_timeout=5) as client:
        result["redis"] = {"ping": bool(client.ping()), "server": socket.gethostbyname(parsed.hostname),
                           "port": parsed.port or 6379, "db": parsed.path.lstrip("/") or "0"}
except Exception as exc:
    result["redisErrorType"] = type(exc).__name__
print(json.dumps(result))
'''


class ProbeError(RuntimeError):
    """Only allowlisted diagnostic labels, never process output or credentials."""


def run(args: list[str], *, timeout: int = 30) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(type(exc).__name__) from None
    if result.returncode:
        raise ProbeError("command_failed")
    return result.stdout


def container_summary(item: dict) -> dict:
    config = item.get("Config") or {}
    state = item.get("State") or {}
    env = dict(entry.split("=", 1) for entry in config.get("Env", []) if "=" in entry)
    labels = config.get("Labels") or {}
    # Explicit selection: State.Error, health logs, Cmd, Args and Env are secret-bearing.
    return {
        "name": item.get("Name", "").lstrip("/"),
        "id": item.get("Id"), "imageId": item.get("Image"), "image": config.get("Image"),
        "status": state.get("Status"), "health": (state.get("Health") or {}).get("Status"),
        "project": labels.get("com.docker.compose.project"),
        "workingDirectory": labels.get("com.docker.compose.project.working_dir"),
        "composeFiles": labels.get("com.docker.compose.project.config_files"),
        "networks": sorted((item.get("NetworkSettings") or {}).get("Networks", {})),
        "ports": (item.get("NetworkSettings") or {}).get("Ports", {}),
        "mounts": [{key: mount.get(key) for key in ("Type", "Name", "Source", "Destination", "RW")}
                   for mount in item.get("Mounts", [])],
        "settings": {key: env[key] for key in SAFE_ENV if key in env},
    }


def resource_summary() -> dict:
    disk = shutil.disk_usage("/")
    memory = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                memory[key + "KiB"] = int(value.split()[0])
    except (OSError, ValueError):
        pass
    return {"rootDiskBytes": {"total": disk.total, "used": disk.used, "free": disk.free},
            "memory": memory}


def collect(sudo: bool = False) -> dict:
    docker = (["sudo", "-n"] if sudo else []) + ["docker", "--context", "default"]
    report = {"observationsOnly": True, "containers": [], "apiConnections": {},
              "systemd": {}, "health": {}, "errors": [], "resources": resource_summary()}
    for name in REQUIRED + OPTIONAL:
        try:
            data = json.loads(run(docker + ["inspect", name]))
            if len(data) != 1:
                raise ProbeError("unexpected_inspect_result")
            report["containers"].append(container_summary(data[0]))
        except (ProbeError, ValueError, KeyError, TypeError) as exc:
            if name in REQUIRED:
                report["errors"].append({"check": name, "errorType": type(exc).__name__})
    for name in REQUIRED[:2]:
        try:
            result = json.loads(run(docker + ["exec", name, "python", "-c", PROBE]))
            report["apiConnections"][name] = result
            if "databaseErrorType" in result or "redisErrorType" in result:
                report["errors"].append({"check": name + ":connections", "errorType": "probe_failed"})
        except (ProbeError, ValueError, TypeError) as exc:
            report["errors"].append({"check": name + ":connections", "errorType": type(exc).__name__})
    for unit in TIMERS:
        try:
            output = run(["systemctl", "show", unit, "--property=LoadState,ActiveState,UnitFileState"])
            report["systemd"][unit] = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        except ProbeError:
            report["systemd"][unit] = {"observation": "unavailable"}
    for port in (8002, 8003):
        for endpoint in ("/health", "/health/db", "/health/redis"):
            key = str(port) + endpoint
            try:
                with urlopen("http://127.0.0.1:" + key, timeout=8) as response:
                    report["health"][key] = {"httpStatus": response.status}
            except Exception as exc:
                report["health"][key] = {"errorType": type(exc).__name__}
                report["errors"].append({"check": key, "errorType": "health_failed"})
    connections = list(report["apiConnections"].values())
    report["sameDatabaseObserved"] = (
        len(connections) == 2 and all("database" in item for item in connections)
        and connections[0]["database"] == connections[1]["database"]
    )
    report["sameRedisObserved"] = (
        len(connections) == 2 and all(item.get("redis", {}).get("ping") for item in connections)
        and connections[0]["redis"] == connections[1]["redis"]
    )
    report["runtimeChecksPassed"] = not report["errors"] and report["sameDatabaseObserved"] and report["sameRedisObserved"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sudo", action="store_true", help="Use sudo -n for Docker reads only")
    args = parser.parse_args()
    report = collect(args.sudo)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["runtimeChecksPassed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
