"""Synthetic stopped-release migration: no actual Docker, DB, service or provider."""
import copy
import importlib
import json
import os
import sys
import unittest
from unittest.mock import patch

import test_scheduled_run as fixtures

scheduler = fixtures.scheduler

sys.modules["scheduled_run"] = scheduler
migration_module = importlib.import_module("release_migration")


class ReleaseMigrationTests(unittest.TestCase):
    def setUp(self):
        fixture = self.fixture = fixtures.ScheduleTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.env.write_bytes(fixture.env.read_bytes() + b"DAILY_RELEASE_TAG='" + b"a" * 40 + b"'\n")
        fixture.generation_stop = "job_requires_attention"
        with self.assertRaises(scheduler.ScheduleError):
            fixture.supervisor().run(apply=True)
        self.old = fixture.supervisor()
        self.new_release = fixture.root / "new-release"
        self.new_release.mkdir()
        for name in self.old.compose_hashes:
            (self.new_release / name).write_bytes((fixture.release / name).read_bytes())
        self.new_env = self.new_release / "private.env"
        self.new_env.write_bytes(fixture.env.read_bytes().replace(b"a" * 40, b"b" * 40))
        os.chmod(self.new_env, 0o600)
        self.new_policy = {**copy.deepcopy(fixture.policy), "release_sha": "b" * 40}
        self.backup = fixture.root / "migration-backup"
        self.probes = []
        self.migration = self.make_migration()
        self.plan = self.migration.plan()
        self.plan_sha = scheduler.digest(self.plan)

    def make_migration(self):
        new = scheduler.Supervisor(policy=self.new_policy, reviewed_sha256=scheduler.digest(self.new_policy),
            state_root=self.fixture.state, runtime_env=self.new_env, release_dir=self.new_release,
            now=lambda: self.fixture.now, runner=self.fixture.runner, owner=None)
        return migration_module.Migration(self.old, new, self.backup,
                                          quiescent=lambda: self.probes.append("read_only_probe"))

    def bytes(self):
        return {path.relative_to(self.fixture.state).as_posix(): path.read_bytes()
                for path in self.fixture.state.rglob("*") if path.is_file()}

    def test_plan_is_local_read_only_and_contains_no_credentials(self):
        before, calls = self.bytes(), len(self.fixture.calls)
        plan = self.migration.plan()
        self.assertEqual(plan, self.plan)
        self.assertEqual(self.bytes(), before)
        self.assertEqual(len(self.fixture.calls), calls)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.probes, [])
        self.assertNotIn("synthetic", json.dumps(plan))
        self.assertNotIn("CONTENT_JOB_DB_PASSWORD", json.dumps(plan))

    def test_apply_only_rebinds_stopped_metadata_and_preserves_every_history_file(self):
        before, calls = self.bytes(), len(self.fixture.calls)
        report = self.migration.apply(self.plan, self.plan_sha)
        self.assertEqual(report["status"], "migrated_inactive")
        self.assertEqual((report["phase"], report["llm_calls"], report["checkpoint_writes"], report["service_db_writes"]),
                         ("blocked", 0, 0, 0))
        after = self.bytes()
        changed = {name for name in before if before[name] != after[name]}
        self.assertEqual(changed, set(self.plan["before_files"]))
        self.assertEqual(len(self.fixture.calls), calls)
        journal = self.migration.new.load_journal()
        self.assertEqual(journal["days"], self.plan["source_journal"]["days"])
        self.migration.new.artifacts(journal["days"][-1])
        self.assertEqual(len(journal["release_binding_history"]), 1)
        with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_requires_reconciliation"):
            self.migration.new.run(apply=True)
        for number, name in enumerate(self.migration.paths(self.plan["source_journal"])):
            self.assertEqual((self.backup / f"before-{number}.bin").read_bytes(), before[name])
        self.assertEqual(self.old.env_path.read_bytes(), self.old.base_env)
        self.assertEqual(len(self.probes), 4)

    def test_same_reviewed_plan_is_idempotent_without_second_history_entry(self):
        first = self.migration.apply(self.plan, self.plan_sha)
        before = self.bytes()
        receipt = (self.backup / "receipt.json").read_bytes()
        self.assertEqual(self.migration.apply(self.plan, self.plan_sha), first)
        self.assertEqual(self.bytes(), before)
        self.assertEqual((self.backup / "receipt.json").read_bytes(), receipt)

    def test_crash_between_environment_replacements_resumes_exact_backup_and_plan(self):
        before = self.bytes()
        interrupted = self.old.host(self.migration.paths(self.plan["source_journal"])[1])
        actual = scheduler.atomic_bytes
        def crash(path, raw, **kwargs):
            if path == interrupted:
                raise fixtures.SimulatedCrash("runtime-env-write")
            return actual(path, raw, **kwargs)
        with patch.object(scheduler, "atomic_bytes", crash), self.assertRaises(fixtures.SimulatedCrash):
            self.migration.apply(self.plan, self.plan_sha)
        self.assertEqual(self.old.load_journal()["days"][-1]["phase"], "blocked")
        with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_configuration_changed"):
            self.migration.new.load_journal()
        self.migration.apply(self.plan, self.plan_sha)
        for number, name in enumerate(self.migration.paths(self.plan["source_journal"])):
            self.assertEqual((self.backup / f"before-{number}.bin").read_bytes(), before[name])

    def test_preserved_history_change_blocks_before_any_metadata_write(self):
        checkpoint = self.fixture.state / self.fixture.entry()["checkpoint"]
        checkpoint.write_bytes(checkpoint.read_bytes() + b" ")
        before = self.bytes()
        with self.assertRaisesRegex(scheduler.ScheduleError, "migration_preserved_state_changed"):
            self.migration.apply(self.plan, self.plan_sha)
        self.assertEqual(self.bytes(), before)
        self.assertFalse(self.backup.exists())

    def test_compare_and_swap_rejects_unreviewed_journal_or_derived_environment(self):
        journal = self.fixture.state / "scheduled/journal.json"
        journal.write_bytes(journal.read_bytes() + b" ")
        before = self.bytes()
        with self.assertRaisesRegex(scheduler.ScheduleError, "migration_compare_and_swap_failed"):
            self.migration.apply(self.plan, self.plan_sha)
        self.assertEqual(self.bytes(), before)

    def test_changed_backup_cannot_be_used_to_finish_or_repeat_migration(self):
        self.migration.apply(self.plan, self.plan_sha)
        (self.backup / "before-0.bin").write_bytes(b"changed")
        before = self.bytes()
        with self.assertRaisesRegex(scheduler.ScheduleError, "migration_backup_changed"):
            self.migration.apply(self.plan, self.plan_sha)
        self.assertEqual(self.bytes(), before)

    def test_scope_credentials_and_compose_changes_cannot_hide_in_release_update(self):
        self.new_policy["routes"][0]["menu_work_id"] = 9
        with self.assertRaisesRegex(scheduler.ScheduleError, "migration_scope_changed"):
            self.make_migration()
        self.new_policy["routes"][0]["menu_work_id"] = 8
        raw = self.new_env.read_bytes()
        self.new_env.write_bytes(raw.replace(b"synthetic", b"changed"))
        with self.assertRaisesRegex(scheduler.ScheduleError, "migration_environment_change_forbidden"):
            self.make_migration()
        self.new_env.write_bytes(raw)
        (self.new_release / "compose.runtime.yaml").write_text("changed network or mounts")
        with self.assertRaisesRegex(scheduler.ScheduleError, "migration_compose_change_forbidden"):
            self.make_migration()

    def test_reviewed_digest_and_quiescence_fail_before_backups_or_writes(self):
        before = self.bytes()
        with self.assertRaisesRegex(scheduler.ScheduleError, "reviewed_migration_digest_required"):
            self.migration.apply(self.plan, "0" * 64)
        def active():
            raise scheduler.ScheduleError("schedule_must_be_stopped")
        self.migration.quiescent = active
        with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_must_be_stopped"):
            self.migration.apply(self.plan, self.plan_sha)
        self.assertEqual(self.bytes(), before)
        self.assertFalse(self.backup.exists())

    def test_active_day_cannot_be_rebound(self):
        journal = self.old.load_journal()
        journal["days"][-1]["phase"] = "generating"
        self.old.save(journal)
        with self.assertRaisesRegex(scheduler.ScheduleError, "migration_requires_stopped_journal"):
            self.migration.plan()

    @unittest.skipIf(os.name == "nt", "Disposable Linux POSIX flock test")
    def test_existing_supervisor_lock_blocks_migration(self):
        with scheduler.exclusive_lock(self.fixture.state / "daily-schedule.lock"):
            with self.assertRaisesRegex(scheduler.ScheduleError, "schedule_already_running"):
                self.migration.apply(self.plan, self.plan_sha)
        self.assertFalse(self.backup.exists())


class QuiescenceTests(unittest.TestCase):
    def test_only_read_only_commands_are_used_and_all_generation_containers_block(self):
        for fault in (None, "timer_active", "timer_enabled", "service_active", "generation", "importer"):
            with self.subTest(fault=fault):
                calls = []
                def run(args):
                    calls.append(args)
                    if args[0] == "systemctl":
                        active = fault == "timer_active" and args[2].endswith(".timer") or fault == "service_active" and args[2].endswith(".service")
                        enabled = fault == "timer_enabled" and args[2].endswith(".timer")
                        return f"ActiveState={'active' if active else 'inactive'}\nUnitFileState={'enabled' if enabled else 'disabled'}\n"
                    self.assertEqual(args[:3], ["docker", "--context", "default"])
                    if args[3] == "ps":
                        return "abc\n" if fault in {"generation", "importer"} else ""
                    self.assertEqual(args[3], "inspect")
                    return json.dumps([{"Config": {"Env": ["CONTENT_JOB_KIND=anime_generate"] if fault == "generation" else [],
                        "Labels": {"com.docker.compose.project": scheduler.PROJECT} if fault == "importer" else {}}}])
                if fault is None:
                    migration_module.verify_quiescent(run)
                else:
                    with self.assertRaises(scheduler.ScheduleError):
                        migration_module.verify_quiescent(run)
                self.assertTrue(all(args[1] == "show" if args[0] == "systemctl" else args[3] in {"ps", "inspect"} for args in calls))


if __name__ == "__main__":
    unittest.main()
