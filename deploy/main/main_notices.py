"""Preview or publish due notices through the verified Main API.

Publication requires the legacy Checking notice timer disabled and its service
inactive. These fixed actions never stop Checking or alter data infrastructure.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

import main_runtime as runtime

SERVICE = "me-nu-main-notices.service"
TIMER = "me-nu-main-notices.timer"
UNIT_DIRECTORY = Path("/etc/systemd/system")
LEGACY_LOCK = Path("/run/lock/me-nu-checking-api-rollout.lock")
UNIT_ROOT = "/home/ubuntu/apps/me-nu-main-20261118"


def safe_receipt(value, *, apply):
    if not isinstance(value, dict) or value.get("mode") != ("apply" if apply else "preview"):
        raise runtime.RuntimeGuardError("invalid_notice_receipt")
    result = {"mode": value["mode"]}
    for key in ("due", "published", "deliveries"):
        count = value.get(key)
        if type(count) is not int or not 0 <= count <= 1_000_000_000:
            raise runtime.RuntimeGuardError("invalid_notice_receipt")
        result[key] = count
    return result


def dispatch(*, apply=False):
    with runtime.lock(), runtime.lock(LEGACY_LOCK):
        runtime.inspect_main()
        if apply:
            runtime.require_legacy_stopped("notices")
        command = [*runtime.DOCKER, "exec", "--user", "10001:10001", "-w", "/app", runtime.API,
                   "python", "-m", "app.jobs.publish_scheduled_notices", "--expected-database", "me_nu_checking",
                   "--limit", "100"]
        if apply:
            command.append("--apply")
        output = runtime.run(command, timeout=240)
        if len(output) > 4096:
            raise runtime.RuntimeGuardError("invalid_notice_receipt")
        return safe_receipt(json.loads(output), apply=apply)


def units():
    result = {}
    for name in (SERVICE, TIMER):
        path = runtime.ROOT / "deploy/main/systemd" / name
        if path.is_symlink() or not path.is_file():
            raise runtime.RuntimeGuardError("notice_unit_missing")
        content = path.read_text(encoding="utf-8")
        if len(content) > 8192 or "\0" in content:
            raise runtime.RuntimeGuardError("invalid_notice_unit")
        result[name] = content.replace(UNIT_ROOT, str(runtime.ROOT)).encode()
    return result


def enable():
    with runtime.lock(), runtime.lock(LEGACY_LOCK):
        runtime.require_legacy_stopped("notices")
        runtime.inspect_main()
        if any(path.is_symlink() for path in (UNIT_DIRECTORY, *UNIT_DIRECTORY.parents)):
            raise runtime.RuntimeGuardError("unsafe_unit_directory")
        for name, data in units().items():
            target = UNIT_DIRECTORY / name
            if target.is_symlink() or target.exists() and not stat.S_ISREG(target.stat().st_mode):
                raise runtime.RuntimeGuardError("unsafe_unit_file")
            descriptor, temporary = tempfile.mkstemp(prefix=".main-notices-", dir=UNIT_DIRECTORY)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o644)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        runtime.run(["/usr/bin/systemctl", "daemon-reload"])
        runtime.run(["/usr/bin/systemctl", "enable", "--now", TIMER])
    return {"timer_enabled": True, "legacy_timer_modified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preview", "run", "status", "enable", "disable", "dry-run"), nargs="?", default="preview")
    args = parser.parse_args(argv)
    try:
        if args.action == "dry-run":
            for name, data in units().items():
                print(f"# {name}\n" + data.decode())
            return 0
        runtime.require_host(__file__)
        if args.action == "enable":
            result = enable()
        elif args.action == "disable":
            runtime.run(["/usr/bin/systemctl", "disable", "--now", TIMER])
            result = {"timer_enabled": False, "active_job_may_finish": True}
        elif args.action == "status":
            print(runtime.run(["/usr/bin/systemctl", "show", TIMER, SERVICE,
                               "--property=Id,LoadState,ActiveState,SubState,Result,LastTriggerUSec,NextElapseUSecRealtime"]))
            return 0
        else:
            result = dispatch(apply=args.action == "run")
        print(json.dumps(result))
        return 0
    except (runtime.RuntimeGuardError, OSError, ValueError):
        print("Main notice action did not complete; command output was withheld.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
