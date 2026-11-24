"""Run focused PostgreSQL tests in two disposable, network-isolated containers.

Default plan performs no Docker actions. Explicit apply uses existing images
only; never builds, pulls, migrates production, or reads a service environment.
The host needs Python's standard library and Docker, not application packages.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import signal
import sys
import uuid

import main_runtime as runtime

DATABASE = "main_withdrawal_test"
PASSWORD = "synthetic-disposable-rehearsal-only"
URL = f"postgresql+psycopg://rehearsal:{PASSWORD}@127.0.0.1:5432/{DATABASE}"
LABEL = "me-nu.rehearsal"
RUN_LABEL = "me-nu.rehearsal.run"
ROLE_LABEL = "me-nu.rehearsal.role"
PURPOSE = "main-promotion-postgres"
PG_TAG = "pgvector/pgvector:pg17"
SOURCES = ("app", "alembic", "tests", "alembic.ini")
TEST_FILES = ("tests/test_main_withdrawal_postgres.py", "tests/test_main_migration_postgres.py")
DRIVER = r'''
import contextlib, io, json, os, time
receipt = {"exitCode": 1, "collected": 0, "passed": 0, "failed": 0, "skipped": 0}
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    try:
        import psycopg
        import pytest
        dsn = os.environ["MAIN_WITHDRAWAL_TEST_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
        for attempt in range(30):
            try:
                with psycopg.connect(dsn, connect_timeout=1) as conn:
                    assert conn.execute("SELECT current_database()").fetchone()[0] == "main_withdrawal_test"
                break
            except Exception:
                if attempt == 29:
                    raise RuntimeError("disposable_postgres_unavailable") from None
                time.sleep(1)
        class Receipt:
            def pytest_collection_finish(self, session):
                receipt["collected"] = len(session.items)
            def pytest_runtest_logreport(self, report):
                if report.failed:
                    receipt["failed"] += 1
                elif report.skipped:
                    receipt["skipped"] += 1
                elif report.when == "call" and report.passed:
                    receipt["passed"] += 1
        receipt["exitCode"] = int(pytest.main(["-q", "-p", "no:cacheprovider", "--basetemp=/tmp/pytest",
            "tests/test_main_withdrawal_postgres.py", "tests/test_main_migration_postgres.py"], plugins=[Receipt()]))
    except BaseException:
        receipt["exitCode"] = 1
print(json.dumps(receipt))
'''


class RehearsalError(ValueError):
    """Fixed diagnostic codes only; never include command output."""


def image_id(tag, *, postgres=False):
    try:
        items = json.loads(runtime.run([*runtime.DOCKER, "image", "inspect", tag]))
        item = items[0]
        config = item.get("Config", {})
        if len(items) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("Id", "")):
            raise ValueError
        volumes = set(config.get("Volumes") or {})
        if volumes != ({"/var/lib/postgresql/data"} if postgres else set()):
            raise ValueError
        if not postgres and config.get("User") != "10001:10001":
            raise ValueError
        return item["Id"]
    except (ValueError, TypeError, IndexError, AttributeError):
        raise RehearsalError("existing_reviewed_image_required") from None


def source_mounts():
    backend = runtime.ROOT / "backend"
    result = []
    for relative in SOURCES:
        path = backend / relative
        if not path.exists() or "," in str(path):
            raise RehearsalError("reviewed_backend_sources_required")
        entries = [path, *path.parents]
        if path.is_dir():
            entries.extend(path.rglob("*"))
        for entry in entries:
            if entry.is_symlink() or entry.name == ".env" or entry.name.startswith(".env."):
                raise RehearsalError("source_links_or_environment_files_rejected")
            if not entry.is_dir() and not entry.is_file():
                raise RehearsalError("source_must_be_regular_files_or_directories")
        result += ["--mount", f"type=bind,src={path},dst=/rehearsal/{relative},readonly"]
    if any(not (backend / relative).is_file() for relative in TEST_FILES):
        raise RehearsalError("focused_postgres_tests_missing")
    return result


def common(name, run_id, role):
    if not re.fullmatch(r"[0-9a-f]{32}", run_id) or role not in {"postgres", "tests"}:
        raise RehearsalError("invalid_rehearsal_identity")
    if name != f"me-nu-main-rehearsal-{run_id}-{role}":
        raise RehearsalError("invalid_rehearsal_name")
    return [*runtime.DOCKER, "create", "--pull", "never", "--name", name,
            "--label", f"{LABEL}={PURPOSE}", "--label", f"{RUN_LABEL}={run_id}",
            "--label", f"{ROLE_LABEL}={role}", "--restart", "no", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--memory", "512m", "--memory-swap", "512m", "--cpus", "0.5",
            "--pids-limit", "64", "--log-driver", "none", "--no-healthcheck"]


def postgres_command(image, run_id):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise RehearsalError("immutable_image_required")
    name = f"me-nu-main-rehearsal-{run_id}-postgres"
    return common(name, run_id, "postgres") + [
        "--network", "none", "--user", "999:999", "--shm-size", "32m",
        "--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m,uid=999,gid=999,mode=0700",
        "--tmpfs", "/var/run/postgresql:rw,noexec,nosuid,size=16m,uid=999,gid=999,mode=0700",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        "--env", f"POSTGRES_DB={DATABASE}", "--env", "POSTGRES_USER=rehearsal",
        "--env", f"POSTGRES_PASSWORD={PASSWORD}", "--env", "PGDATA=/var/lib/postgresql/data",
        image, "postgres", "-c", "listen_addresses=127.0.0.1", "-c", "max_connections=15",
        "-c", "shared_buffers=32MB", "-c", "fsync=off", "-c", "synchronous_commit=off",
        "-c", "full_page_writes=off", "-c", "wal_level=minimal", "-c", "max_wal_senders=0",
        "-c", "max_wal_size=64MB", "-c", "min_wal_size=32MB"]


def test_command(image, postgres_id, run_id):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image) or not re.fullmatch(r"[0-9a-f]{64}", postgres_id):
        raise RehearsalError("immutable_image_and_owned_postgres_id_required")
    name = f"me-nu-main-rehearsal-{run_id}-tests"
    environment = {
        "DATABASE_URL": URL, "MAIN_WITHDRAWAL_TEST_URL": URL, "MENU_DEPLOYMENT_ENV": "local",
        "EVENT_DATA_SOURCE": "automated_test", "SESSION_BACKEND": "postgres",
        "CHECKING_CAROUSEL_TEST_PREVIEW_ENABLED": "false", "DEBATE_ENABLED": "false",
        "OPENAI_API_KEY": "", "LLM_KEY": "", "TMDB_KEY": "", "DICT_DATABASE_URL": "",
        "REDIS_URL": "", "ENABLE_DEV_API": "false", "ENABLE_DEV_AUTH_BYPASS": "false",
        "KAKAO_OAUTH_ENABLED": "false", "NAVER_OAUTH_ENABLED": "false",
        "KAKAO_CLIENT_ID": "", "KAKAO_CLIENT_SECRET": "", "NAVER_CLIENT_ID": "", "NAVER_CLIENT_SECRET": "",
        "PYTHONPATH": "/rehearsal", "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONUNBUFFERED": "1", "HOME": "/tmp", "PYTEST_ADDOPTS": "",
    }
    command = common(name, run_id, "tests") + ["--network", f"container:{postgres_id}",
        "--user", "10001:10001", "--workdir", "/rehearsal",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m,mode=1777", *source_mounts()]
    for key, value in environment.items():
        command += ["--env", f"{key}={value}"]
    return command + ["--entrypoint", "python", image, "-c", DRIVER]


def inspect_owned(container_id, run_id, role):
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise RehearsalError("invalid_owned_container_id")
    try:
        items = json.loads(runtime.run([*runtime.DOCKER, "inspect", container_id]))
        item = items[0]
        labels = item.get("Config", {}).get("Labels") or {}
        if (len(items) != 1 or item.get("Id") != container_id
                or item.get("Name") != f"/me-nu-main-rehearsal-{run_id}-{role}"
                or labels.get(LABEL) != PURPOSE or labels.get(RUN_LABEL) != run_id
                or labels.get(ROLE_LABEL) != role):
            raise ValueError
        return item
    except (ValueError, TypeError, IndexError, AttributeError):
        raise RehearsalError("container_ownership_not_verified") from None


def create(command, run_id, role):
    identifier = runtime.run(command)
    inspect_owned(identifier, run_id, role)
    return identifier


def validate_isolation(item, image, role, postgres_id=None):
    """Verify the actual created container before starting either process."""
    host = item.get("HostConfig", {})
    config = item.get("Config", {})
    expected_network = "none" if role == "postgres" else f"container:{postgres_id}"
    expected_user = "999:999" if role == "postgres" else "10001:10001"
    if (item.get("Image") != image or config.get("Image") != image or config.get("User") != expected_user
            or host.get("NetworkMode") != expected_network or host.get("ReadonlyRootfs") is not True
            or host.get("Privileged") is not False or host.get("CapDrop") != ["ALL"]
            or host.get("CapAdd") or host.get("Devices") or host.get("Binds")
            or host.get("PidMode") or host.get("IpcMode") == "host" or host.get("PortBindings")
            or host.get("PublishAllPorts") or host.get("RestartPolicy", {}).get("Name") != "no"
            or host.get("Memory") != 512 * 1024**2 or host.get("MemorySwap") != 512 * 1024**2
            or host.get("NanoCpus") != 500_000_000 or host.get("PidsLimit") != 64
            or host.get("SecurityOpt") != ["no-new-privileges:true"]):
        raise RehearsalError("created_container_isolation_mismatch")
    tmpfs = host.get("Tmpfs") or {}
    expected_tmpfs = ({"/var/lib/postgresql/data", "/var/run/postgresql", "/tmp"}
                      if role == "postgres" else {"/tmp"})
    if set(tmpfs) != expected_tmpfs:
        raise RehearsalError("created_container_tmpfs_mismatch")
    if role == "postgres":
        if set(item.get("NetworkSettings", {}).get("Networks", {})) - {"none"}:
            raise RehearsalError("postgres_network_mismatch")
        expected_binds = {}
    else:
        expected_binds = {f"/rehearsal/{relative}": str(runtime.ROOT / "backend" / relative) for relative in SOURCES}
    mounts = item.get("Mounts", [])
    binds = {}
    for mount in mounts:
        if mount.get("Type") == "tmpfs" and mount.get("Destination") in expected_tmpfs:
            continue
        if mount.get("Type") != "bind" or mount.get("RW") is not False:
            raise RehearsalError("persistent_or_writable_mount_rejected")
        destination = mount.get("Destination")
        if destination in binds:
            raise RehearsalError("duplicate_source_mount")
        binds[destination] = mount.get("Source")
    if binds != expected_binds:
        raise RehearsalError("created_container_source_mount_mismatch")


def cleanup(run_id):
    """Discover only this invocation's labels, then remove verified IDs, tests first."""
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise RehearsalError("invalid_rehearsal_identity")
    found = runtime.run([*runtime.DOCKER, "ps", "--all", "--no-trunc", "--filter", f"label={LABEL}={PURPOSE}",
                         "--filter", f"label={RUN_LABEL}={run_id}", "--format", "{{.ID}} {{.Label \"me-nu.rehearsal.role\"}}"])
    containers = []
    for line in found.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[1] not in {"tests", "postgres"}:
            raise RehearsalError("cleanup_ownership_unknown")
        inspect_owned(fields[0], run_id, fields[1])
        containers.append(fields)
    if len(containers) > 2 or len({role for _, role in containers}) != len(containers):
        raise RehearsalError("cleanup_ownership_unknown")
    for identifier, role in sorted(containers, key=lambda item: item[1] != "tests"):
        inspect_owned(identifier, run_id, role)
        runtime.run([*runtime.DOCKER, "rm", "--force", identifier])


def receipt(output):
    try:
        value = json.loads(output)
    except ValueError:
        raise RehearsalError("invalid_rehearsal_receipt") from None
    keys = {"exitCode", "collected", "passed", "failed", "skipped"}
    if (not isinstance(value, dict) or set(value) != keys
            or any(type(number) is not int or not 0 <= number <= 10000 for number in value.values())):
        raise RehearsalError("invalid_rehearsal_receipt")
    if (value["exitCode"] != 0 or value["failed"] or value["skipped"]
            or value["collected"] < 6 or value["passed"] != value["collected"]):
        raise RehearsalError("focused_postgres_tests_not_all_passed")
    return value


def apply(tag):
    runtime.require_host(__file__)
    if not re.fullmatch(r"[0-9a-f]{40}", tag or "") or runtime.checkout_sha() != tag:
        raise RehearsalError("image_tag_must_match_clean_reviewed_checkout")
    source_mounts()
    backend_image = image_id(f"me-nu-backend-main:{tag}")
    postgres_image = image_id(PG_TAG, postgres=True)
    run_id = uuid.uuid4().hex
    try:
        postgres = create(postgres_command(postgres_image, run_id), run_id, "postgres")
        validate_isolation(inspect_owned(postgres, run_id, "postgres"), postgres_image, "postgres")
        runtime.run([*runtime.DOCKER, "start", postgres])
        tested = create(test_command(backend_image, postgres, run_id), run_id, "tests")
        validate_isolation(inspect_owned(tested, run_id, "tests"), backend_image, "tests", postgres)
        output = runtime.run([*runtime.DOCKER, "start", "--attach", tested], timeout=900)
        result = receipt(output)
        state = inspect_owned(tested, run_id, "tests").get("State", {})
        if state.get("Status") != "exited" or state.get("ExitCode") != 0 or state.get("OOMKilled"):
            raise RehearsalError("rehearsal_container_did_not_exit_cleanly")
        return {"focusedPostgresTestsPassed": True, "tests": result, "checkoutSha": tag,
                "productionChanged": False, "temporaryContainersRemoved": True}
    finally:
        cleanup(run_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply"), nargs="?", default="plan")
    parser.add_argument("--image-tag", help="Existing Main backend image's reviewed full Git commit")
    args = parser.parse_args(argv)
    try:
        if args.action == "plan":
            if args.image_tag and not re.fullmatch(r"[0-9a-f]{40}", args.image_tag):
                raise RehearsalError("full_commit_required")
            print(json.dumps({"action": "plan", "containersStarted": False, "productionChanged": False,
                "existingImagesOnly": True, "postgresImage": PG_TAG, "postgresNetwork": "none",
                "testNetwork": "owned PostgreSQL container namespace (loopback only)",
                "postgresData": "512 MiB tmpfs; no volume or host port", "tests": list(TEST_FILES)}))
            return 0
        def interrupted(*_):
            raise RehearsalError("rehearsal_interrupted")
        signal.signal(signal.SIGTERM, interrupted)
        print(json.dumps(apply(args.image_tag)))
        return 0
    except (RehearsalError, runtime.RuntimeGuardError, OSError, ValueError, KeyboardInterrupt):
        print(json.dumps({"focusedPostgresTestsPassed": False, "productionChanged": False,
                          "error": "rehearsal_incomplete_output_withheld_check_owned_containers"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
