"""Publish a completed evaluation to Checking, using the deployed schema in isolation.

Run with sudo on the Checking host. No host Python packages, database access,
API restart, or credentials are needed. Validation errors never echo input data.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

ROOT = Path("/home/ubuntu/apps/me-nu-checking")
CONTAINER = "me-nu-checking-api-1"
DOCKER = ["docker", "--context", "default"]
CONTAINER_PATH = "/app/admin-evaluation/evaluation.json"
MAX_BYTES = 16 * 1024 * 1024
LOCK = Path("/run/lock/me-nu-checking-api-rollout.lock")


class PublishError(ValueError):
    """An operational error safe to print without input or environment data."""


def run(command: list[str]) -> str:
    environment = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                   "LANG": "C.UTF-8"}
    try:
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                                text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise PublishError("A required local validation command could not complete.") from None
    if result.returncode:
        raise PublishError("Local validation failed; input and command output were withheld.")
    return result.stdout.strip()


def no_symlinks(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise PublishError("Use an absolute path without parent traversal.")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise PublishError("Symbolic links are not accepted for publication paths.")


def read_regular(path: Path) -> bytes:
    no_symlinks(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise PublishError("Input must be a regular evaluation file no larger than 16 MiB.")
            data = stream.read(MAX_BYTES + 1)
    except OSError:
        raise PublishError("The evaluation file could not be safely read.") from None
    if not data or len(data) > MAX_BYTES:
        raise PublishError("Input must be nonempty and no larger than 16 MiB.")
    return data


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def private_directory(path: Path) -> None:
    no_symlinks(path)
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.stat()
    if not path.is_dir() or (os.name != "nt" and
                            (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700)):
        raise PublishError("Publication staging and backups must be private operator-owned directories.")


def runtime_identity(artifact_dir: Path) -> tuple[str, str]:
    git = ["git", "--no-optional-locks", "-c", f"safe.directory={ROOT}", "-C", str(ROOT)]
    sha = run([*git, "rev-parse", "HEAD"])
    if (not re.fullmatch(r"[0-9a-f]{40}", sha)
            or run([*git, "status", "--porcelain", "--untracked-files=all"])):
        raise PublishError("The Checking checkout must be clean at its deployed commit.")
    fields = (".Image", ".Config.Image", ".Config.User", ".HostConfig.ReadonlyRootfs",
              ".State.Status", ".State.Health.Status", ".Mounts")
    template = "\n".join("{{json " + field + "}}" for field in fields)
    try:
        values = [json.loads(line) for line in
                  run([*DOCKER, "inspect", "--format", template, CONTAINER]).splitlines()]
        image, tag, user, readonly, status, health, mounts = values
    except (ValueError, TypeError):
        raise PublishError("The Checking runtime identity could not be verified.") from None
    expected_tag = f"me-nu-backend-checking:{sha}"
    if (not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image)
            or tag != expected_tag or user != "10001:10001" or readonly is not True
            or status != "running" or health != "healthy"):
        raise PublishError("Checking must be healthy, non-root, read-only, and match the checkout.")
    if run([*DOCKER, "image", "inspect", "--format", "{{.Id}}", expected_tag]) != image:
        raise PublishError("The deployed image tag no longer identifies the running image.")
    if (not isinstance(mounts, list) or len(mounts) != 1
            or mounts[0].get("Type") != "bind" or mounts[0].get("RW") is not False
            or mounts[0].get("Source") != str(artifact_dir)
            or mounts[0].get("Destination") != "/app/admin-evaluation"):
        raise PublishError("The API must use only the expected read-only evaluation directory mount.")
    configured = run([*DOCKER, "exec", CONTAINER, "python", "-c",
                      "import os; print(os.environ.get('ADMIN_RECOMMENDATION_EVALUATION_PATH', ''))"])
    if configured != CONTAINER_PATH:
        raise PublishError("Enable the fixed Checking evaluation path before publishing.")
    return sha, image


def validator_command(image: str, input_path: Path, stage: Path) -> list[str]:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise PublishError("Validation must use a verified immutable image ID.")
    sources = (ROOT / "model/eval", input_path, stage)
    for source in sources:
        no_symlinks(source)
        if "," in str(source):
            raise PublishError("Docker bind sources must not contain commas.")
    return [*DOCKER, "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "64", "--memory", "512m", "--cpus", "1", "--user", "0:0",
            "--env", "PYTHONPATH=/publisher", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--mount", f"type=bind,src={sources[0]},dst=/publisher/model/eval,readonly",
            "--mount", f"type=bind,src={input_path},dst=/input/evaluation.json,readonly",
            "--mount", f"type=bind,src={stage},dst=/staging",
            "--entrypoint", "python", image, "-m", "model.eval.publish_admin_evaluation",
            "--input", "/input/evaluation.json", "--output", "/staging/evaluation.json"]


def sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def publish(input_path: Path) -> dict:
    no_symlinks(ROOT)
    artifact_dir = ROOT / "deploy/checking/admin-evaluation"
    no_symlinks(artifact_dir)
    if (not artifact_dir.is_dir() or
            (os.name != "nt" and stat.S_IMODE(artifact_dir.stat().st_mode) != 0o755)):
        raise PublishError("The deployed evaluation directory must already exist with mode 0755.")
    target = artifact_dir / "evaluation.json"
    no_symlinks(target)
    data = read_regular(input_path)
    if input_path.resolve() == target.resolve():
        raise PublishError("Input must be outside the live evaluation file.")
    previous = read_regular(target) if target.exists() else None
    identity = runtime_identity(artifact_dir)
    state_dir = ROOT.parent / ".checking-evaluation-artifacts"
    private_directory(state_dir)
    backups = state_dir / "backups"
    private_directory(backups)
    if state_dir.stat().st_dev != artifact_dir.stat().st_dev:
        raise PublishError("Staging and the live evaluation must share a filesystem for atomic replacement.")
    with tempfile.TemporaryDirectory(prefix="publish-", dir=state_dir) as temporary:
        work = Path(temporary)
        input_copy = work / "input.json"
        write_private(input_copy, data)
        stage = work / "validated"
        private_directory(stage)
        # Only the isolated child can write staging. It cannot see the live file,
        # backups, Docker socket, host network, or API/database environment.
        run(validator_command(identity[1], input_copy, stage))
        prepared = stage / "evaluation.json"
        canonical = read_regular(prepared)
        if os.name != "nt" and stat.S_IMODE(prepared.stat().st_mode) != 0o644:
            raise PublishError("The validated artifact must already be readable by API UID 10001.")
        if runtime_identity(artifact_dir) != identity:
            raise PublishError("Checking changed during validation; no result was published.")
        no_symlinks(target)
        current = read_regular(target) if target.exists() else None
        if current != previous:
            raise PublishError("The published evaluation changed during validation; retry from the current state.")
        backup_name = None
        if previous is not None:
            backup_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + digest(previous)
            backup = backups / (backup_name + ".json")
            write_private(backup, previous)
            write_private(backups / (backup_name + ".sha256"), (digest(previous) + "\n").encode())
            sync_directory(backups)
        os.replace(prepared, target)
        sync_directory(artifact_dir)
        return {"published": True, "checkoutSha": identity[0], "sha256": digest(canonical),
                "previousSha256": digest(previous) if previous is not None else None,
                "backup": backup_name, "refresh": "next recommendation-performance GET"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    try:
        if sys.platform != "linux" or os.geteuid() != 0:
            raise PublishError("Run this fixed Checking-host entrypoint with sudo on Linux.")
        if Path(__file__).resolve() != ROOT / "deploy/checking/publish_evaluation.py":
            raise PublishError("Run the entrypoint from the fixed Checking deployment checkout.")
        import fcntl
        with LOCK.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PublishError("A Checking API rollout or evaluation publication is already running.") from None
            print(json.dumps(publish(args.input), ensure_ascii=False))
    except (PublishError, OSError):
        print("Evaluation publication did not complete. Check deployment, input validity, and file permissions; "
              "no input or command output was printed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
