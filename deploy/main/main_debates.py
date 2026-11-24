"""Preview or apply debate finalization through the verified Main API.

The timer runs app.jobs.finalize_debates every 10 minutes: it deletes topics that
missed the vote threshold, closes ended rooms and deletes their chat, and purges
rooms past the result retention period. Screens compute these states from
timestamps, so a late run only delays the stored cleanup. There is no Checking
counterpart to retire; these fixed actions never stop Checking or alter data
infrastructure. Run preview before enable. Failures print only a fixed reason
code; a timer run that meets a held rollout lock is logged as skipped.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

import main_runtime as runtime

SERVICE = "me-nu-main-debates.service"
TIMER = "me-nu-main-debates.timer"
UNIT_DIRECTORY = Path("/etc/systemd/system")
LEGACY_LOCK = Path("/run/lock/me-nu-checking-api-rollout.lock")
UNIT_ROOT = "/home/ubuntu/apps/me-nu-main-20261118"
COUNTS = ("expired", "closed", "deleted_messages", "purged_debates")


def safe_receipt(value, *, apply):
    if not isinstance(value, dict) or value.get("mode") != ("apply" if apply else "dry_run"):
        raise runtime.RuntimeGuardError("invalid_debate_receipt")
    result = {"mode": value["mode"]}
    for key in COUNTS:
        count = value.get(key)
        if type(count) is not int or not 0 <= count <= 1_000_000_000:
            raise runtime.RuntimeGuardError("invalid_debate_receipt")
        result[key] = count
    return result


def dispatch(*, apply=False):
    # Checking still shares this database: hold both rollout locks, like the notice job.
    with runtime.lock(), runtime.lock(LEGACY_LOCK):
        runtime.inspect_main()
        command = [*runtime.DOCKER, "exec", "--user", "10001:10001", "-w", "/app", runtime.API,
                   "python", "-m", "app.jobs.finalize_debates", "--expected-database", "me_nu_checking"]
        if apply:
            command.append("--apply")
        output = runtime.run(command, timeout=240)
        if len(output) > 4096:
            raise runtime.RuntimeGuardError("invalid_debate_receipt")
        return safe_receipt(json.loads(output), apply=apply)


def units():
    result = {}
    for name in (SERVICE, TIMER):
        path = runtime.ROOT / "deploy/main/systemd" / name
        if path.is_symlink() or not path.is_file():
            raise runtime.RuntimeGuardError("debate_unit_missing")
        content = path.read_text(encoding="utf-8")
        if len(content) > 8192 or "\0" in content:
            raise runtime.RuntimeGuardError("invalid_debate_unit")
        result[name] = content.replace(UNIT_ROOT, str(runtime.ROOT)).encode()
    return result


def enable():
    with runtime.lock(), runtime.lock(LEGACY_LOCK):
        runtime.inspect_main()
        if any(path.is_symlink() for path in (UNIT_DIRECTORY, *UNIT_DIRECTORY.parents)):
            raise runtime.RuntimeGuardError("unsafe_unit_directory")
        for name, data in units().items():
            target = UNIT_DIRECTORY / name
            if target.is_symlink() or target.exists() and not stat.S_ISREG(target.stat().st_mode):
                raise runtime.RuntimeGuardError("unsafe_unit_file")
            descriptor, temporary = tempfile.mkstemp(prefix=".main-debates-", dir=UNIT_DIRECTORY)
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
    return {"timer_enabled": True}


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
    except (runtime.RuntimeGuardError, OSError, ValueError) as error:
        # Guard errors carry fixed lowercase codes only; anything else stays withheld.
        code = error.args[0] if type(error) is runtime.RuntimeGuardError and error.args else "unexpected_error"
        if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,64}", code):
            code = "runtime_guard_failed"
        # Another Main job or a rollout holds the lock: the next timer run catches up.
        skipped = args.action == "run" and code == "operation_in_progress"
        print(json.dumps({"state": "skipped" if skipped else "failed", "reason": code}), file=sys.stderr)
        return 0 if skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
