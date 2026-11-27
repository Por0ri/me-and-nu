"""Append an approved same-day limit revision while every generator is stopped.

This file-only migration never calls Docker, a database, a provider or systemd.
The caller supplies read-only quiescence checks and separately changes systemd's
policy binding after verifying the receipt. The same reviewed plan is replayable
after a partial write; all original records and recovery evidence stay intact.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import scheduled_run as schedule
from release_migration import encoded, hash_file, raw_sha


class Migration:
    def __init__(self, old, new_policy_path, backup_dir, *, old_policy_path,
                 reason, approved_at, quiescent):
        self.old, self.quiescent = old, quiescent
        self.old_policy_path = schedule.checked_path(old_policy_path)
        self.new_policy_path = schedule.checked_path(new_policy_path, exists=False)
        self.backup = schedule.checked_path(backup_dir, exists=False)
        self.reason, self.approved_at = reason, approved_at
        schedule.require(callable(quiescent), "quiescence_callback_required")
        schedule.require(old.policy.get("schema_version") == 2
                         and type(old.policy.get("max_attempts")) is int and old.policy["max_attempts"] == 40,
                         "success_only_requires_legacy_mixed_schedule")
        schedule.require(self.new_policy_path.name == "success-target-policy.json"
                         and self.new_policy_path.parent.is_dir()
                         and self.new_policy_path != self.old_policy_path
                         and not self.new_policy_path.is_relative_to(old.root)
                         and not self.backup.is_relative_to(old.root)
                         and self.backup.parent.is_dir()
                         and not self.new_policy_path.is_relative_to(self.backup), "unsafe_limit_migration_paths")
        self.new_policy = {**copy.deepcopy(old.policy), "schema_version": 3, "max_attempts": None}
        self.new = schedule.Supervisor(policy=self.new_policy, reviewed_sha256=schedule.digest(self.new_policy),
            state_root=old.root, runtime_env=old.env_path, release_dir=old.release,
            now=old.now, runner=old.runner, owner=old.owner)
        self.old_policy_sha = schedule.file_sha(self.old_policy_path)
        self.inputs()

    def inputs(self):
        schedule.require(schedule.file_sha(self.old_policy_path) == self.old_policy_sha
                         and schedule.read_json(self.old_policy_path) == self.old.policy
                         and schedule.validate_policy(self.old.policy) == self.old.policy_sha
                         and schedule.validate_policy(self.new.policy) == self.new.policy_sha
                         and schedule.read_bytes(self.old.env_path) == self.old.base_env
                         and self.old.compose_hashes == self.new.compose_hashes
                         and all(schedule.file_sha(self.old.release / name) == sha
                                 for name, sha in self.old.compose_hashes.items()), "limit_migration_input_changed")
        self.old.rag_inputs()

    def quiet(self):
        proof = self.quiescent()
        schedule.require(isinstance(proof, dict) and proof.get("writers_stopped") is True
                         and proof.get("broker_empty") is True
                         and type(proof.get("active_jobs")) is int and proof["active_jobs"] == 0,
                         "limit_migration_requires_quiescence")

    def names(self, journal):
        schedule.require(journal.get("days") and journal["days"][-1].get("phase") == "reconciled_paused",
                         "limit_migration_requires_reconciled_pause")
        entry = journal["days"][-1]
        schedule.require(entry["date"] == self.old.today() and "daily_limit_revision_sha256" not in entry,
                         "limit_migration_day_changed")
        return [entry["checkpoint"], "scheduled/journal.json",
                entry["directory"] + "/rag-inbox/.approvals/daily-limit-revision.json"]

    def inventory(self, excluded):
        result = {}
        for path in sorted(self.old.root.rglob("*")):
            schedule.checked_path(path)
            name = path.relative_to(self.old.root).as_posix()
            if path.is_file() and name not in excluded | {"daily-schedule.lock"}:
                result[name] = hash_file(path)
        return result

    def base_plan(self):
        return {"schema_version": 1, "purpose": "inactive_daily_success_only_migration",
            "state_root": str(self.old.root), "backup_directory": str(self.backup),
            "old_policy_path": str(self.old_policy_path), "old_policy_file_sha256": self.old_policy_sha,
            "new_policy_path": str(self.new_policy_path), "old_policy": self.old.policy,
            "new_policy": self.new_policy, "runtime_environment_sha256": raw_sha(self.old.base_env),
            "compose_sha256": self.old.compose_hashes, "reason": self.reason, "approved_at": self.approved_at,
            "generation_enabled": False, "llm_calls": 0, "queue_writes": 0, "service_db_writes": 0}

    def plan(self):
        self.inputs(); self.quiet()
        journal = self.old.load_journal()
        names = self.names(journal)
        entry = journal["days"][-1]
        original_plan = self.old.artifacts(entry)
        child = schedule.read_json(self.old.host(names[0]))
        schedule.require(child.get("status") == "stopped"
                         and child.get("stop_reason") == "failure_reconciled_paused"
                         and child.get("config") == original_plan["config"]
                         and child.get("daily_policy") == original_plan["daily_policy"]
                         and child.get("daily_plan_sha256") == entry["plan_sha256"],
                         "limit_migration_checkpoint_changed")
        for reference in entry["failure_recoveries"]:
            schedule.require(schedule.digest(schedule.read_json(self.old.host(reference["receipt"])))
                             == reference["receipt_sha256"], "limit_migration_recovery_changed")
        try:
            revision = schedule.daily_limits.build_limit_revision(child, reason=self.reason, approved_at=self.approved_at)
        except (ValueError, KeyError, TypeError):
            raise schedule.ScheduleError("limit_migration_revision_invalid") from None
        schedule.require(not self.new_policy_path.exists(), "limit_migration_new_policy_exists")
        schedule.require(not self.old.host(names[2]).exists(), "limit_migration_revision_file_exists")
        return {**self.base_plan(), "source_journal": journal, "revision": revision,
            "before_files": {**{name: schedule.file_sha(self.old.host(name)) for name in names[:2]}, names[2]: None},
            "preserved_files": self.inventory(set(names))}

    def before_checkpoint(self, plan):
        name = self.names(plan["source_journal"])[0]
        path = self.old.host(name)
        if schedule.file_sha(path) != plan["before_files"][name]:
            path = self.backup / "before-0.bin"
        schedule.require(path.exists() and schedule.file_sha(path) == plan["before_files"][name],
                         "limit_migration_original_checkpoint_missing")
        return schedule.read_json(path)

    def outputs(self, plan):
        names = self.names(plan["source_journal"])
        journal_path = self.old.host(names[1])
        if schedule.file_sha(journal_path) != plan["before_files"][names[1]]:
            journal_path = self.backup / "before-1.bin"
        schedule.require(journal_path.exists() and schedule.file_sha(journal_path) == plan["before_files"][names[1]]
                         and schedule.read_json(journal_path) == plan["source_journal"],
                         "limit_migration_original_journal_changed")
        child = self.before_checkpoint(plan)
        try:
            revision = schedule.daily_limits.build_limit_revision(child, reason=self.reason, approved_at=self.approved_at)
        except (ValueError, KeyError, TypeError):
            raise schedule.ScheduleError("limit_migration_revision_invalid") from None
        schedule.require(revision == plan["revision"], "limit_migration_revision_changed")
        child["daily_limit_revisions"] = [*child.get("daily_limit_revisions", []), revision]
        journal = copy.deepcopy(plan["source_journal"])
        journal["policy_sha256"] = self.new.policy_sha
        journal["days"][-1]["daily_limit_revision_sha256"] = schedule.digest(revision)
        journal.setdefault("daily_limit_approval_history", []).append({
            "plan_sha256": schedule.digest(plan), "revision_sha256": schedule.digest(revision),
            "before_policy_sha256": self.old.policy_sha, "after_policy_sha256": self.new.policy_sha,
            "before_checkpoint_sha256": plan["before_files"][names[0]],
            "before_journal_sha256": plan["before_files"][names[1]],
            "backup_directory": str(self.backup), "approved_at": self.approved_at, "reason": self.reason})
        canonical_revision = json.dumps(revision, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False, allow_nan=False).encode()
        return {names[2]: canonical_revision, names[0]: encoded(child), names[1]: encoded(journal)}

    def apply(self, plan, reviewed_sha256):
        schedule.require(schedule.digest(plan) == reviewed_sha256, "reviewed_limit_migration_digest_required")
        schedule.require(set(plan) == set(self.base_plan()) | {"source_journal", "revision", "before_files", "preserved_files"}
                         and all(plan.get(key) == value for key, value in self.base_plan().items()),
                         "limit_migration_plan_changed")
        names = self.names(plan["source_journal"])
        schedule.require(set(plan["before_files"]) == set(names), "limit_migration_file_scope_changed")
        with schedule.exclusive_lock(self.old.root / "daily-schedule.lock"):
            self.inputs(); self.quiet()
            outputs = self.outputs(plan)
            new_policy_raw = encoded(self.new_policy)
            manifest = {"plan_sha256": reviewed_sha256, "before_files": plan["before_files"],
                "after_files": {name: raw_sha(raw) for name, raw in outputs.items()},
                "new_policy_sha256": raw_sha(new_policy_raw),
                "preserved_files_sha256": schedule.digest(plan["preserved_files"])}

            def check():
                self.inputs(); self.quiet()
                schedule.require(self.inventory(set(names)) == plan["preserved_files"], "limit_migration_preserved_state_changed")
                current = {name: schedule.file_sha(self.old.host(name)) if self.old.host(name).exists() else None
                           for name in names}
                schedule.require(all(sha in {plan["before_files"][name], manifest["after_files"][name]}
                                     for name, sha in current.items()), "limit_migration_compare_and_swap_failed")
                schedule.checked_path(self.new_policy_path, exists=False)
                schedule.require(not self.new_policy_path.exists()
                                 or schedule.read_bytes(self.new_policy_path) == new_policy_raw,
                                 "limit_migration_new_policy_changed")
                return current

            current = check()
            schedule.checked_path(self.backup, exists=False)
            if not self.backup.exists():
                schedule.require(current == plan["before_files"] and not self.new_policy_path.exists(),
                                 "limit_migration_backup_missing")
                self.backup.mkdir(mode=0o700)
            schedule.require(self.backup.is_dir(), "invalid_limit_migration_backup")
            os.chmod(self.backup, 0o700)
            for number, name in enumerate(names):
                if plan["before_files"][name] is None:
                    schedule.require(name == names[2], "limit_migration_file_scope_changed")
                    continue
                backup = self.backup / f"before-{number}.bin"
                if backup.exists():
                    schedule.require(schedule.file_sha(backup) == plan["before_files"][name], "limit_migration_backup_changed")
                else:
                    schedule.require(current[name] == plan["before_files"][name], "limit_migration_backup_missing")
                    schedule.atomic_bytes(backup, schedule.read_bytes(self.old.host(name)), immutable=True)
            schedule.save_json(self.backup / "plan.json", plan, immutable=True)
            schedule.atomic_bytes(self.backup / "old-policy.bin", schedule.read_bytes(self.old_policy_path), immutable=True)
            schedule.save_json(self.backup / "manifest.json", manifest, immutable=True)
            check()
            schedule.atomic_bytes(self.new_policy_path, new_policy_raw, immutable=True)
            # Checkpoint first and journal last; both normal scheduler bindings
            # reject a partial transition until this reviewed plan finishes.
            for name, raw in outputs.items():
                current = check()
                if current[name] != manifest["after_files"][name]:
                    path = self.old.host(name)
                    if name == names[2]:
                        schedule.checked_path(path.parent, exists=False)
                        path.parent.mkdir(mode=0o755, exist_ok=True)
                        schedule.atomic_bytes(path, raw, mode=0o444, owner=0 if os.name != "nt" else None, immutable=True)
                        os.chmod(path.parent, 0o555)
                    else:
                        stat = path.stat()
                        schedule.require(os.name == "nt" or stat.st_uid == stat.st_gid, "limit_migration_owner_requires_review")
                        schedule.atomic_bytes(path, raw, mode=stat.st_mode & 0o777, owner=stat.st_uid)
            check()
            os.chmod(self.old.host(names[2]).parent, 0o555)
            journal = self.new.load_journal()
            self.new.artifacts(journal["days"][-1])
            receipt = {"status": "success_only_migrated_inactive", "plan_sha256": reviewed_sha256,
                "new_policy_sha256": self.new.policy_sha, "new_policy_file_sha256": manifest["new_policy_sha256"],
                "revision_sha256": schedule.digest(plan["revision"]), "phase": "reconciled_paused",
                "daily_attempts": plan["revision"]["prior_daily_attempts"],
                "daily_passes": plan["revision"]["prior_daily_successes"], "max_attempts": None,
                "success_target": 20, "generation_enabled": False, "llm_calls": 0,
                "queue_writes": 0, "service_db_writes": 0}
            schedule.save_json(self.backup / "receipt.json", receipt, immutable=True)
            return receipt
