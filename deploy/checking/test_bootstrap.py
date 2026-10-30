"""No-network Checking bootstrap tests using a temporary in-memory database."""
from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, text

import bootstrap as seed

NOW = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.connection = self.engine.connect()
        self.connection.execute(text("CREATE TABLE topic (topic_id INTEGER PRIMARY KEY, topic_code TEXT UNIQUE, topic_name TEXT, description TEXT, sort_order INTEGER, is_active BOOLEAN)"))
        self.connection.execute(text("CREATE TABLE policy (policy_id INTEGER PRIMARY KEY, policy_type TEXT, policy_version TEXT, title TEXT, content TEXT, is_required BOOLEAN, is_active BOOLEAN, effective_at TIMESTAMP, UNIQUE(policy_type,policy_version))"))
        for table in seed.PRIVATE_TABLES:
            self.connection.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.engine.dispose()

    def apply(self):
        with self.connection.begin():
            plan = seed.build_plan(self.connection, NOW)
            seed.apply_plan(self.connection, plan, NOW)
        return plan

    def test_dry_plan_does_not_write(self):
        plan = seed.build_plan(self.connection, NOW)
        self.assertTrue(plan["new_topic"])
        self.assertEqual(len(plan["new_policies"]), 3)
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM topic")), 0)
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM policy")), 0)

    def test_apply_and_repeat_only_create_topic_and_three_policies(self):
        self.apply()
        plan = self.apply()
        self.assertFalse(plan["new_topic"])
        self.assertEqual(plan["new_policies"], [])
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM topic")), 1)
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM policy")), 3)
        for table in seed.PRIVATE_TABLES:
            self.assertEqual(self.connection.scalar(text(f"SELECT count(*) FROM {table}")), 0)

    def test_existing_active_policy_is_preserved(self):
        self.connection.execute(text("INSERT INTO policy VALUES (1,'terms','existing','Existing policy','Existing body',true,true,'2026-01-01')"))
        self.connection.commit()
        plan = self.apply()
        self.assertEqual(len(plan["new_policies"]), 2)
        self.assertEqual(self.connection.scalar(text("SELECT content FROM policy WHERE policy_type='terms'")), "Existing body")

    def test_inactive_topic_is_not_reactivated(self):
        self.connection.execute(text("INSERT INTO topic (topic_code,is_active) VALUES ('anime',false)"))
        with self.assertRaises(seed.BootstrapError):
            seed.build_plan(self.connection, NOW)
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM policy")), 0)

    def test_conflicting_own_policy_version_is_not_overwritten(self):
        self.connection.execute(text("INSERT INTO policy VALUES (1,'terms',:version,'Different title','Different text',true,true,'2026-01-01')"), {"version": seed.POLICY_VERSION})
        with self.assertRaises(seed.BootstrapError):
            seed.build_plan(self.connection, NOW)

    def test_future_policy_is_not_superseded(self):
        self.connection.execute(text("INSERT INTO policy VALUES (1,'terms','future','Scheduled','Scheduled body',true,true,'2099-01-01')"))
        with self.assertRaises(seed.BootstrapError):
            seed.build_plan(self.connection, NOW)

    def test_partial_seed_in_used_database_is_rejected(self):
        for table in seed.PRIVATE_TABLES:
            with self.subTest(table=table):
                self.connection.execute(text(f"INSERT INTO {table} DEFAULT VALUES"))
                with self.assertRaises(seed.BootstrapError):
                    seed.build_plan(self.connection, NOW)
                self.connection.rollback()

    def test_complete_seed_remains_noop_after_team_signup(self):
        self.apply()
        self.connection.execute(text("INSERT INTO user_account DEFAULT VALUES"))
        self.connection.commit()
        plan = self.apply()
        self.assertFalse(plan["new_topic"])
        self.assertEqual(plan["new_policies"], [])

    def test_transaction_rolls_back_all_inserts_on_failure(self):
        with self.assertRaises(RuntimeError):
            with self.connection.begin():
                seed.apply_plan(self.connection, seed.build_plan(self.connection, NOW), NOW)
                raise RuntimeError("injected failure")
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM topic")), 0)
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM policy")), 0)

    def test_target_guards_do_not_allow_development_or_other_databases(self):
        valid = "postgresql+psycopg://me_nu_checking:test-only@postgres:5432/me_nu_checking"
        seed.validate_target(valid, seed.DATABASE, "checking")
        invalid = ((valid, "other", "checking"), (valid, seed.DATABASE, "development"),
                   (valid.replace("/me_nu_checking", "/carousel_preview"), seed.DATABASE, "checking"),
                   (valid.replace("me_nu_checking:test-only", "postgres:test-only"), seed.DATABASE, "checking"),
                   (valid + "?options=x", seed.DATABASE, "checking"), ("", seed.DATABASE, "checking"))
        for values in invalid:
            with self.subTest(target=values[1:]), self.assertRaises(seed.BootstrapError):
                seed.validate_target(*values)


class RuntimeGuardTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connection.scalar.side_effect = [seed.DATABASE, seed.DATABASE]
        self.connection.execute.return_value.scalars.return_value.all.return_value = [seed.REVISION]
        self.engine = MagicMock()
        self.engine.connect.return_value = self.connection
        self.target = {"database_url": "postgresql+psycopg://me_nu_checking:test-only@postgres:5432/me_nu_checking",
                       "expected_database": seed.DATABASE, "deployment": "checking"}
        self.plan = {"new_topic": True, "new_policies": list(seed.POLICIES), "preserved_policies": []}

    def test_default_runtime_is_read_only(self):
        with patch.object(seed, "create_engine", return_value=self.engine), patch.object(seed, "build_plan", return_value=self.plan), patch.object(seed, "apply_plan") as apply:
            report = seed.run(**self.target)
        self.assertEqual(report["mode"], "dry_run")
        self.assertIn("SET TRANSACTION READ ONLY", [str(call.args[0]) for call in self.connection.execute.call_args_list])
        apply.assert_not_called()
        self.engine.dispose.assert_called_once()

    def test_apply_runtime_locks_and_applies_single_transaction(self):
        with patch.object(seed, "create_engine", return_value=self.engine), patch.object(seed, "build_plan", return_value=self.plan), patch.object(seed, "apply_plan") as apply:
            report = seed.run(**self.target, apply=True)
        self.assertEqual(report["mode"], "apply")
        self.assertIn("SELECT pg_advisory_xact_lock(20261028, 3)", [str(call.args[0]) for call in self.connection.execute.call_args_list])
        apply.assert_called_once()
        self.connection.begin.assert_called_once()

    def test_actual_database_identity_is_checked_before_seeding(self):
        self.connection.scalar.side_effect = ["wrong_database"]
        with patch.object(seed, "create_engine", return_value=self.engine), patch.object(seed, "build_plan") as plan, self.assertRaises(seed.BootstrapError):
            seed.run(**self.target)
        plan.assert_not_called()

    def test_wrong_migration_prevents_seeding(self):
        self.connection.execute.return_value.scalars.return_value.all.return_value = ["e3a7b9c2d451"]
        with patch.object(seed, "create_engine", return_value=self.engine), patch.object(seed, "build_plan") as plan, self.assertRaises(seed.BootstrapError):
            seed.run(**self.target)
        plan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
