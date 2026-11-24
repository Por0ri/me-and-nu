"""Run the deployed Checking API image's due-notice job with rollout exclusion.

No credentials, titles, bodies or member IDs are accepted by this wrapper or
written to its output. The timer uses --apply; manual invocation previews.
"""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
CONTAINER = "me-nu-checking-api-1"
LOCK = Path("/run/lock/me-nu-checking-api-rollout.lock")
DOCKER = "/usr/bin/docker"


class DispatchError(ValueError):
    pass


@contextmanager
def rollout_lock():
    import fcntl
    with LOCK.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DispatchError("checking_rollout_in_progress") from None
        yield


def validate_container(data):
    if not isinstance(data, list) or len(data) != 1:
        raise DispatchError("unexpected_container")
    item = data[0]
    config = item.get("Config", {})
    labels = config.get("Labels", {})
    env = config.get("Env", [])
    if (item.get("Name") != "/" + CONTAINER or not item.get("State", {}).get("Running")
            or labels.get("com.docker.compose.project") != "me-nu-checking"
            or labels.get("com.docker.compose.service") != "api"
            or "MENU_DEPLOYMENT_ENV=checking" not in env
            or not re.fullmatch(r"me-nu-backend-checking:[0-9a-f]{40}", config.get("Image", ""))):
        raise DispatchError("checking_container_guard_failed")


def safe_receipt(value, *, apply):
    if not isinstance(value, dict) or value.get("mode") != ("apply" if apply else "preview"):
        raise DispatchError("invalid_dispatch_receipt")
    result = {"mode": value["mode"]}
    for field in ("due", "published", "deliveries"):
        count = value.get(field)
        if type(count) is not int or not 0 <= count <= 1_000_000_000:
            raise DispatchError("invalid_dispatch_receipt")
        result[field] = count
    return result


def run(*, apply=False):
    if ROOT != Path("/home/ubuntu/apps/me-nu-checking") or not (ROOT / "compose.checking.yaml").is_file():
        raise DispatchError("unexpected_checking_checkout")
    with rollout_lock():
        inspected = subprocess.run([DOCKER, "--context", "default", "inspect", CONTAINER],
                                   capture_output=True, timeout=10, check=False)
        if inspected.returncode:
            raise DispatchError("checking_api_unavailable")
        validate_container(json.loads(inspected.stdout))
        command = [DOCKER, "--context", "default", "exec", "--user", "10001:10001", "-w", "/app", CONTAINER,
                   "python", "-m", "app.jobs.publish_scheduled_notices", "--expected-database", "me_nu_checking",
                   "--limit", "100"]
        if apply:
            command.append("--apply")
        finished = subprocess.run(command, capture_output=True, timeout=240, check=False)
        if finished.returncode or len(finished.stdout) > 4096:
            raise DispatchError("dispatch_incomplete_retry_next_run")
        return safe_receipt(json.loads(finished.stdout), apply=apply)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(apply=args.apply)))
    except (DispatchError, OSError, subprocess.SubprocessError, ValueError):
        parser.exit(1, "Checking notice dispatch did not complete; inspect service health and retry.\n")


if __name__ == "__main__":
    main()
