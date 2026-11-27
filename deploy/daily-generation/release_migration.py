"""Rebind a stopped schedule to a reviewed code-only release, without resuming it.

Default mode is a read-only local plan. Apply only replaces the journal's release
bindings and the unfinished day's two derived environment files, with immutable
private backups and compare-and-swap checks. It never starts or stops a service.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import scheduled_run as schedule


def raw_sha(raw):
    return hashlib.sha256(raw).hexdigest()


def hash_file(path):
    path = schedule.checked_path(path)
    schedule.require(path.is_file(), "migration_expected_file")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode()


def read_command(args):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("DOCKER_", "COMPOSE_", "DAILY_", "ANIME_", "CONTENT_JOB_", "MAIN_POSTGRES_"))}
    result = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                            env=environment, timeout=60)
    schedule.require(result.returncode == 0, "migration_quiescence_check_failed")
    return result.stdout


def verify_quiescent(runner=read_command):
    """Read-only OS checks; no enable, stop, restart, exec or container run commands."""
    for unit in ("me-nu-daily-schedule.timer", "me-nu-daily-schedule.service"):
        output = runner(["systemctl", "show", unit, "--property=ActiveState,UnitFileState"])
        values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        allowed = {"inactive"} if unit.endswith(".timer") else {"inactive", "failed"}
        schedule.require(values.get("ActiveState") in allowed, "schedule_must_be_stopped")
        if unit.endswith(".timer"):
            schedule.require(values.get("UnitFileState") in {"disabled", "masked"}, "schedule_timer_must_be_disabled")
    ids = runner(["docker", "--context", "default", "ps", "-q"]).split()
    if ids:
        rows = json.loads(runner(["docker", "--context", "default", "inspect", *ids]))
        for row in rows:
            config = row["Config"]
            values = dict(item.split("=", 1) for item in config.get("Env", []) if "=" in item)
            labels = config.get("Labels") or {}
            schedule.require(values.get("CONTENT_JOB_KIND") != "anime_generate"
                             and labels.get("com.docker.compose.project") != schedule.PROJECT,
                             "generation_process_must_be_stopped")


class Migration:
    def __init__(self, old, new, backup_dir, *, quiescent=verify_quiescent):
        self.old, self.new, self.quiescent = old, new, quiescent
        self.backup = schedule.checked_path(backup_dir, exists=False)
        schedule.require(self.old.root == self.new.root and self.old.release != self.new.release,
                         "migration_paths_changed")
        schedule.require(not self.backup.is_relative_to(self.old.root)
                         and self.backup.parent.is_dir(), "backup_must_be_outside_state")
        before, after = copy.deepcopy(old.policy), copy.deepcopy(new.policy)
        old_release, new_release = before.pop("release_sha"), after.pop("release_sha")
        schedule.require(before == after and old_release != new_release, "migration_scope_changed")
        # This migration deliberately does not authorize networks, mounts,
        # commands, credentials, routes, quotas, or target changes.
        schedule.require(old.compose_hashes == new.compose_hashes, "migration_compose_change_forbidden")
        old_tag = f"DAILY_RELEASE_TAG='{old_release}'".encode()
        new_tag = f"DAILY_RELEASE_TAG='{new_release}'".encode()
        old_lines, new_lines = old.base_env.splitlines(), new.base_env.splitlines()
        replaced = b"".join(new_tag + line[len(old_tag):] if line.rstrip(b"\r\n") == old_tag else line
                            for line in old.base_env.splitlines(keepends=True))
        schedule.require(old_lines.count(old_tag) == new_lines.count(new_tag) == 1
                         and sum(line.startswith(b"DAILY_RELEASE_TAG=") for line in old_lines) == 1
                         and replaced == new.base_env,
                         "migration_environment_change_forbidden")

    def bindings(self, supervisor):
        return {"policy_sha256": supervisor.policy_sha,
                "runtime_environment_sha256": raw_sha(supervisor.base_env),
                "compose_sha256": supervisor.compose_hashes}

    def inputs(self):
        for supervisor in (self.old, self.new):
            schedule.require(schedule.validate_policy(supervisor.policy) == supervisor.policy_sha
                             and schedule.read_bytes(supervisor.env_path) == supervisor.base_env
                             and all(schedule.file_sha(supervisor.release / name) == sha
                                     for name, sha in supervisor.compose_hashes.items()),
                             "migration_input_changed")

    def inventory(self, excluded):
        values = {}
        for path in sorted(self.old.root.rglob("*")):
            schedule.checked_path(path)
            name = path.relative_to(self.old.root).as_posix()
            if path.is_file() and name not in excluded | {"daily-schedule.lock"}:
                values[name] = hash_file(path)
        return values

    def base_plan(self):
        return {"schema_version": 1, "purpose": "inactive_schedule_release_migration",
                "state_root": str(self.old.root), "backup_directory": str(self.backup),
                "old_release_directory": str(self.old.release), "new_release_directory": str(self.new.release),
                "old_runtime_environment": str(self.old.env_path), "new_runtime_environment": str(self.new.env_path),
                "old_policy": self.old.policy, "new_policy": self.new.policy,
                "before_bindings": self.bindings(self.old), "after_bindings": self.bindings(self.new),
                "generation_enabled": False, "service_db_writes": 0, "checkpoint_writes": 0}

    def paths(self, journal):
        schedule.require(journal.get("days") and journal["days"][-1].get("phase") in {"blocked", "reconciled_paused"},
                         "migration_requires_stopped_journal")
        entry = journal["days"][-1]
        return [entry["directory"] + "/planning.env", entry["directory"] + "/runtime.env", "scheduled/journal.json"]

    def plan(self):
        self.inputs()
        journal = self.old.load_journal()
        names = self.paths(journal)
        entry = journal["days"][-1]
        self.old.artifacts(entry)
        schedule.require(schedule.read_bytes(self.old.host(names[0])) == self.old.env_bytes(entry),
                         "old_planning_environment_changed")
        return {**self.base_plan(), "source_journal": journal,
                "before_files": {name: schedule.file_sha(self.old.host(name)) for name in names},
                "preserved_files": self.inventory(set(names))}

    def outputs(self, plan):
        names = self.paths(plan["source_journal"])
        entry = plan["source_journal"]["days"][-1]
        journal = copy.deepcopy(plan["source_journal"])
        journal.update(plan["after_bindings"])
        journal.setdefault("release_binding_history", []).append({
            "plan_sha256": schedule.digest(plan), "backup_directory": str(self.backup),
            "before_bindings": plan["before_bindings"], "after_bindings": plan["after_bindings"],
            "before_journal_sha256": plan["before_files"][names[-1]]})
        return {names[0]: self.new.env_bytes(entry),
                names[1]: self.new.env_bytes(entry, entry["plan_sha256"], entry["import_policy_sha256"]),
                names[2]: encoded(journal)}

    def apply(self, plan, reviewed_sha256):
        schedule.require(schedule.digest(plan) == reviewed_sha256, "reviewed_migration_digest_required")
        schedule.require(set(plan) == set(self.base_plan()) | {"source_journal", "before_files", "preserved_files"}
                         and all(plan.get(key) == value for key, value in self.base_plan().items()),
                         "migration_plan_changed")
        names = self.paths(plan["source_journal"])
        schedule.require(set(plan["before_files"]) == set(names), "migration_file_scope_changed")
        outputs = self.outputs(plan)
        manifest = {"plan_sha256": reviewed_sha256, "before_files": plan["before_files"],
                    "after_files": {name: raw_sha(raw) for name, raw in outputs.items()},
                    "preserved_files_sha256": schedule.digest(plan["preserved_files"])}
        with schedule.exclusive_lock(self.old.root / "daily-schedule.lock"):
            self.inputs()
            self.quiescent()
            schedule.require(self.inventory(set(names)) == plan["preserved_files"], "migration_preserved_state_changed")
            current = {name: schedule.file_sha(self.old.host(name)) for name in names}
            for name, sha in current.items():
                schedule.require(sha in {plan["before_files"][name], manifest["after_files"][name]},
                                 "migration_compare_and_swap_failed")
            schedule.checked_path(self.backup, exists=False)
            if not self.backup.exists():
                schedule.require(current == plan["before_files"], "migration_backup_missing")
                self.backup.mkdir(mode=0o700)
            schedule.require(self.backup.is_dir(), "invalid_migration_backup")
            os.chmod(self.backup, 0o700)
            for number, name in enumerate(names):
                backup = self.backup / f"before-{number}.bin"
                if backup.exists():
                    schedule.require(schedule.file_sha(backup) == plan["before_files"][name], "migration_backup_changed")
                else:
                    schedule.require(current[name] == plan["before_files"][name], "migration_backup_missing")
                    schedule.atomic_bytes(backup, schedule.read_bytes(self.old.host(name)), immutable=True)
            schedule.save_json(self.backup / "plan.json", plan, immutable=True)
            schedule.atomic_bytes(self.backup / "old-base-runtime.env", self.old.base_env, immutable=True)
            schedule.atomic_bytes(self.backup / "new-base-runtime.env", self.new.base_env, immutable=True)
            schedule.save_json(self.backup / "manifest.json", manifest, immutable=True)
            # Journal is last: a crash with partially replaced env files still
            # cannot pass either release's normal immutable-file validation.
            for name, raw in outputs.items():
                self.inputs()
                self.quiescent()
                schedule.require(self.inventory(set(names)) == plan["preserved_files"], "migration_preserved_state_changed")
                sha = schedule.file_sha(self.old.host(name))
                schedule.require(sha in {plan["before_files"][name], manifest["after_files"][name]},
                                 "migration_compare_and_swap_failed")
                if sha != manifest["after_files"][name]:
                    schedule.atomic_bytes(self.old.host(name), raw)
            journal = self.new.load_journal()
            self.new.artifacts(journal["days"][-1])
            schedule.require(journal["days"] == plan["source_journal"]["days"]
                             and self.inventory(set(names)) == plan["preserved_files"], "migration_history_changed")
            receipt = {"status": "migrated_inactive", "plan_sha256": reviewed_sha256,
                       "phase": journal["days"][-1]["phase"], "generation_enabled": False,
                       "service_db_writes": 0, "checkpoint_writes": 0, "llm_calls": 0,
                       "new_policy_sha256": self.new.policy_sha}
            schedule.save_json(self.backup / "receipt.json", receipt, immutable=True)
            return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for prefix in ("old", "new"):
        parser.add_argument(f"--{prefix}-policy", type=Path, required=True)
        parser.add_argument(f"--{prefix}-policy-sha256", required=True)
        parser.add_argument(f"--{prefix}-runtime-env", type=Path, required=True)
        parser.add_argument(f"--{prefix}-release-dir", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--plan-file", type=Path)
    parser.add_argument("--reviewed-plan-sha256")
    args = parser.parse_args(argv)
    try:
        endpoints = [schedule.Supervisor(policy=schedule.read_json(getattr(args, prefix + "_policy")),
            reviewed_sha256=getattr(args, prefix + "_policy_sha256"), state_root=args.state_root,
            runtime_env=getattr(args, prefix + "_runtime_env"), release_dir=getattr(args, prefix + "_release_dir"))
            for prefix in ("old", "new")]
        migration = Migration(*endpoints, args.backup_dir)
        if args.apply:
            schedule.require(os.name == "posix" and os.geteuid() == 0, "migration_requires_server_root")
            schedule.require(args.plan_file is not None, "reviewed_migration_plan_required")
            report = migration.apply(schedule.read_json(args.plan_file), args.reviewed_plan_sha256)
        else:
            schedule.require(args.plan_file is None and args.reviewed_plan_sha256 is None, "apply_required_for_reviewed_plan")
            plan = migration.plan()
            report = {"plan": plan, "plan_sha256": schedule.digest(plan), "generation_enabled": False,
                      "service_db_writes": 0, "checkpoint_writes": 0}
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error) if isinstance(error, schedule.ScheduleError) else "release_migration_failed",
                          "generation_enabled": False, "service_db_writes": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
