"""Build, preview, or explicitly start the Main recommendation worker.

The existing Checking worker must first be stopped with restart disabled in a
reviewed handover. No action here stops it or replaces a running Main worker.
Database credentials are inherited privately from the verified Main API.
"""
import argparse
import json
from pathlib import Path
import re
import stat
import sys

import main_runtime as runtime

NAME = "me-nu-main-recommend"
CONTEXT = Path("/home/ubuntu/me-nu-model/service_context.pkl.gz")


def model_image(sha):
    tag = f"me-nu-recommend-main:{sha}"
    try:
        items = json.loads(runtime.run([*runtime.DOCKER, "image", "inspect", tag]))
        item = items[0]
        if (len(items) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("Id", ""))
                or item.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision") != sha):
            raise ValueError
        return item["Id"]
    except (ValueError, TypeError, IndexError, AttributeError):
        raise runtime.RuntimeGuardError("reviewed_main_recommendation_image_required") from None


def command(image, *, apply):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise runtime.RuntimeGuardError("immutable_recommendation_image_required")
    args = [*runtime.DOCKER, "run", "--network", runtime.NETWORK,
            "--memory", "1536m", "--memory-swap", "1536m", "--cpus", "0.5", "--pids-limit", "64",
            "--read-only", "--user", "10001:10001", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
            "--mount", f"type=bind,src={CONTEXT},dst=/data/service_context.pkl.gz,readonly",
            "--env", "DATABASE_URL", "--label", "me-nu.operation=main-recommend"]
    args += (["--detach", "--name", NAME, "--restart", "unless-stopped"] if apply
             else ["--rm", "--env", "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=30000"])
    args += [image, "--expected-database", "me_nu_checking", "--context", "/data/service_context.pkl.gz"]
    args += ["--apply", "--every", "1", "--daily-at", "06:00"] if apply else ["--show", "3"]
    return args


def execute(action):
    if action == "status":
        # Deliberately exclude Docker's Command field and worker logs.
        output = runtime.run([*runtime.DOCKER, "ps", "--all", "--filter", f"name=^/{NAME}$",
                              "--format", "{{.Names}} {{.Status}}"])
        return {"worker": NAME, "state": "present" if output else "absent"}
    if action == "build":
        sha = runtime.checkout_sha()
        runtime.run([*runtime.DOCKER, "build", "-f", str(runtime.ROOT / "model/Dockerfile"),
                     "--label", f"org.opencontainers.image.revision={sha}",
                     "-t", f"me-nu-recommend-main:{sha}", str(runtime.ROOT / "model")], timeout=900)
        model_image(sha)
        return {"built": True, "checkoutSha": sha, "worker_started": False}
    with runtime.lock():
        identity = runtime.inspect_main()
        image = model_image(identity["sha"])
        if (any(path.is_symlink() for path in (CONTEXT, *CONTEXT.parents))
                or not CONTEXT.is_file() or not stat.S_ISREG(CONTEXT.stat().st_mode)
                or CONTEXT.stat().st_size == 0 or not CONTEXT.stat().st_mode & stat.S_IROTH):
            raise runtime.RuntimeGuardError("existing_readable_context_required")
        if action == "up":
            runtime.require_legacy_stopped("recommend")
            if runtime.run([*runtime.DOCKER, "ps", "--all", "--filter", f"name=^/{NAME}$", "--format", "{{.ID}}"]):
                raise runtime.RuntimeGuardError("main_worker_already_exists_no_replacement_performed")
        if runtime.inspect_main() != identity:
            raise runtime.RuntimeGuardError("main_changed_before_worker_start")
        runtime.run(command(image, apply=action == "up"), timeout=600,
                    environment={"DATABASE_URL": identity["database_url"]})
        return {"action": action, "completed": True, "worker_started": action == "up", "checkoutSha": identity["sha"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "build", "preview", "up"), nargs="?", default="status")
    args = parser.parse_args(argv)
    try:
        runtime.require_host(__file__)
        print(json.dumps(execute(args.action)))
        return 0
    except (runtime.RuntimeGuardError, OSError, ValueError):
        print("Main recommendation action did not complete; command output was withheld.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
