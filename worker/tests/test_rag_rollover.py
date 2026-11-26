"""Two real daily checkpoints with synthetic native seals, no network/providers."""
from copy import deepcopy
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import test_daily_generation as legacy
import test_rag_task_contract as native_fixture
from menu_worker.bounded_generation import StopRun, audit_existing_jobs, read_json, write_json
from menu_worker.bounded_resume import digest, totals, verify_completed_history
from menu_worker.daily_generation import KST
from menu_worker.daily_resume import activate_plan, build_plan, main, preflight_plan, source_checkpoint
from menu_worker.rag_contract import canonical
from menu_worker.rag_daily import MixedDailyController
from menu_worker.rag_daily_runtime import (compose_runtime, export_native_delivery,
                                           verify_empty_rag_broker)
from menu_worker.rag_tasks import _directory, _identity, _write, read_sealed_result


class SealedRagStore:
    def __init__(self, root):
        self.root, self.rows, self.published = root, {}, []
        self.jobs = self
        self.outcome = "ready_for_staging"
        fixture = native_fixture.RagTaskContractTests()
        fixture.setUp()
        self.template = fixture.result

    def _connect(self):
        store = self
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def execute(self, _query): return self
            def fetchall(self): return list(deepcopy(store.rows).values())
        return Connection()

    def submit(self, request, receipt):
        for row in self.rows.values():
            if row["identity_key"] == request.identity_key:
                return deepcopy(row) | {"created": False}
        row = {"job_id": str(uuid4()), "generation_attempt_id": str(uuid4()),
            "request": request.as_dict(), "identity_key": request.identity_key,
            "input_sha256": request.input_sha256, "admission_receipt": deepcopy(receipt),
            "admission_sha256": digest(receipt), "execution_status": "queued"}
        self.rows[row["job_id"]] = row
        return deepcopy(row) | {"created": True}

    def get(self, key):
        return deepcopy(self.rows.get(key))

    def publish(self, key):
        self.published.append(key)
        job = self.rows[key]
        request = job["request"]
        result = deepcopy(self.template)
        result.update(job_id=key, generation_attempt_id=job["generation_attempt_id"],
            namespace=request["namespace"], work_id=request["work_id"], outcome=self.outcome,
            request={"work_id": request["work_id"], "menu_work_ids": [request["menu_work_id"]],
                "content_type": request["content_type"], "query": request["query"],
                "seed_evidence_ids": [row["document_id"] for row in request["seed_evidence"]]},
            source_ids=request["allowed_source_ids"])
        directory = _directory(self.root, job)
        directory.mkdir(parents=True)
        launch = {"identity": _identity(job), "request": request, "admission_receipt": job["admission_receipt"]}
        launch_sha = _write(directory / "launch.json", launch)
        result_sha = _write(directory / "result.json", result)
        _write(directory / "manifest.json", {"schema_version": 1, "identity": _identity(job),
            "files": {"launch.json": launch_sha, "result.json": result_sha}})
        job.update(execution_status="succeeded", outcome=self.outcome, result_sha256=result_sha,
            producer_status=result["producer_status"], usage_records=result["usage_records"], cost_complete=True,
            artifact_path=(directory / "manifest.json").relative_to(self.root).as_posix())
        return "synthetic-rag-" + key

    def result(self, job):
        return read_sealed_result(self.root, job)


class RolloverTests(unittest.TestCase):
    def setUp(self):
        self.f = legacy.DailyTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.inbox = self.f.root / "rag-inbox"
        self.inbox.mkdir()
        self.rag = SealedRagStore(self.f.root)
        self.scope = {"schema_version": 1, "purpose": "rag_daily_generation", "routes": [
            {"namespace": "dictionary", "menu_work_id": 8, "content_type": "character",
             "allowed_source_ids": ["kitsu_anime_catalog"]}]}
        self.plan = self.make_plan()

    def make_plan(self, **changes):
        values = dict(source=self.f.source, source_sha256=self.f.sha, checkpoint=self.f.child,
            policy_date="2026-11-24", delivery_root=self.f.inbox, now=self.f.now,
            routes=[{"source_id": "one_piece_official", "content_type": "goods", "menu_work_id": 8}],
            rag_scope=self.scope, rag_scope_sha256=digest(self.scope), rag_delivery_root=self.inbox,
            rag_runtime_sha256="b" * 64)
        values.update(changes)
        return build_plan(**values)

    def activate(self, plan):
        return activate_plan(plan, store=self.f.store, read_result=self.f.read_result,
            rag_store=self.rag, rag_read_result=self.rag.result, now=self.f.now)

    def discover(self, route, limit, *, excluded_document_ids):
        return [{**route, "work_id": "topic:" + str(i), "query": "Topic " + str(i),
                 "seed_evidence": [{"document_id": "doc:" + str(i), "content_sha256": f"{i:064x}"}]}
                for i in range(100) if "doc:" + str(i) not in excluded_document_ids][:limit]

    def controller(self, plan, reservations):
        old = self.f.controller(plan, reservations)
        return MixedDailyController(config=old.config, checkpoint=old.path, store=old.store,
            discover=lambda *_args: {"articles": [], "discovered": 0}, validate=old.validate,
            publish=old.publish, read_result=old.read_result, export=old.export, now=old.now, sleep=old.sleep,
            reservations=reservations, rag_store=self.rag, rag_discover=self.discover,
            rag_publish=self.rag.publish, rag_read_result=self.rag.result,
            rag_export=lambda job, _output: export_native_delivery(self.f.root, self.inbox, job,
                daily_policy=plan["daily_policy"], rag_scope=self.scope), rag_scope_sha256=digest(self.scope))

    def finish(self):
        _, reservations = self.activate(self.plan)
        controller = self.controller(self.plan, reservations)
        report = controller.run()
        return controller, report

    def next_plan(self, **changes):
        self.f.now = datetime(2026, 11, 25, 10, tzinfo=KST).timestamp()
        return self.make_plan(source=self.f.child, source_sha256=hashlib.sha256(self.f.child.read_bytes()).hexdigest(),
            checkpoint=self.f.root / "tomorrow.json", policy_date="2026-11-25", **changes)

    def test_reviewed_plan_and_preflight_are_disabled_and_write_nothing(self):
        before = set(self.f.root.iterdir())
        report = preflight_plan(self.plan, store=self.f.store, read_result=self.f.read_result,
            rag_store=self.rag, rag_read_result=self.rag.result, now=self.f.now)
        self.assertEqual(set(self.f.root.iterdir()), before)
        self.assertFalse(self.plan["generation_enabled"])
        self.assertEqual((report["llm_calls"], report["queue_writes"], report["checkpoint_writes"]), (0, 0, 0))
        self.assertEqual(self.rag.published, [])
        with self.assertRaisesRegex(StopRun, "rag_scope_not_reviewed"):
            self.make_plan(rag_scope_sha256="0" * 64)
        with self.assertRaisesRegex(StopRun, "separate_rag_delivery_root_required"):
            self.make_plan(rag_delivery_root=self.f.inbox)

    def test_next_day_preserves_seals_history_and_does_not_generate_old_topics(self):
        controller, first = self.finish()
        before = self.f.child.read_bytes()
        original_records = deepcopy(controller.state["records"])
        original_jobs = deepcopy(self.rag.rows)
        self.assertEqual((first["daily_attempts"], first["daily_successes"]), (20, 20))
        plan = self.next_plan()
        self.assertEqual(plan["config"]["max_new_jobs"], 64)
        self.assertEqual(plan["daily_policy"]["prior_records_sha256"], digest(original_records))
        state, reservations = self.activate(plan)
        self.assertEqual(state["records"], original_records)
        second_controller = self.controller(plan, reservations)
        second = second_controller.run()
        self.assertEqual((second["daily_attempts"], second["daily_successes"]), (20, 20))
        self.assertEqual(len(self.rag.published), 40)
        self.assertEqual(len({row["identity_key"] for row in self.rag.rows.values()}), 40)
        self.assertEqual({key: self.rag.rows[key] for key in original_jobs}, original_jobs)
        self.assertEqual(second_controller.state["records"][:len(original_records)], original_records)
        self.assertEqual(self.f.child.read_bytes(), before)
        self.assertEqual(len(list(self.inbox.iterdir())), 40)
        self.assertEqual(list(self.f.inbox.iterdir()), [])

    def test_forty_holds_roll_over_to_new_shared_forty_attempts(self):
        self.rag.outcome = "held"
        _, report = self.finish()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (40, 0))
        plan = self.next_plan()
        self.assertEqual(plan["config"]["max_new_jobs"], 84)
        _, reservations = self.activate(plan)
        next_report = self.controller(plan, reservations).run()
        self.assertEqual((next_report["daily_attempts"], next_report["daily_successes"]), (40, 0))
        self.assertEqual(len(self.rag.published), 80)
        self.assertEqual(list(self.inbox.iterdir()), [])

    def test_missing_native_verifier_and_unowned_jobs_cannot_reset_history(self):
        controller, _ = self.finish()
        with self.assertRaisesRegex(StopRun, "rag_history_verifier_required"):
            verify_completed_history(self.f.store, controller.state, self.f.read_result)
        extra = deepcopy(next(iter(self.rag.rows.values())))
        extra["job_id"] = str(uuid4())
        self.rag.rows[extra["job_id"]] = extra
        with self.assertRaisesRegex(StopRun, "rag_database_history_not_owned_by_checkpoint"):
            audit_existing_jobs(self.f.store, controller.state, rag_store=self.rag)

    def test_modified_native_seal_or_cost_or_delivery_stops_rollover(self):
        controller, _ = self.finish()
        record = next(row for row in controller.state["records"] if row.get("producer") == "ani_rag")
        job = self.rag.rows[record["job_id"]]
        path = _directory(self.f.root, job) / "result.json"
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "rag_artifact_changed"):
            self.activate(self.next_plan())
        path.write_bytes(original)
        old_cost = record["estimated_cost_usd"]
        record["estimated_cost_usd"] = "0"
        with self.assertRaisesRegex(StopRun, "rag_completed_cost_changed"):
            verify_completed_history(self.f.store, controller.state, self.f.read_result,
                rag_store=self.rag, rag_read_result=self.rag.result)
        record["estimated_cost_usd"] = old_cost
        envelope = Path(record["rag_delivery"]["envelope_path"])
        envelope.write_bytes(envelope.read_bytes() + b" ")
        with self.assertRaisesRegex(StopRun, "rag_delivery_changed"):
            self.activate(self.next_plan())

    def test_next_day_requires_explicit_unchanged_scope_and_unknown_rag_is_not_recovered(self):
        controller, _ = self.finish()
        with self.assertRaisesRegex(StopRun, "explicit_rag_scope_required_for_rollover"):
            self.next_plan(rag_scope=None, rag_scope_sha256=None, rag_delivery_root=None, rag_runtime_sha256=None)
        changed = deepcopy(self.scope)
        changed["routes"][0]["menu_work_id"] = 9
        with self.assertRaisesRegex(StopRun, "rag_daily_scope_changed"):
            self.next_plan(rag_scope=changed, rag_scope_sha256=digest(changed))
        record = next(row for row in controller.state["records"] if row.get("producer") == "ani_rag")
        record.update(rag_cost_incomplete=True, estimated_cost_usd=None, rag_known_partial_usd="0.01")
        with self.assertRaisesRegex(StopRun, "rag_usage_requires_reconciliation"):
            totals(controller.state)
        self.assertNotIn("failure_recovery_receipt", record)
        self.assertTrue(record["counts_slot"])

    def test_raw_delivery_rechecks_completed_seals_without_overwrite(self):
        controller, _ = self.finish()
        before = {path.name: path.read_bytes() for path in self.inbox.iterdir()}
        controller.run()
        self.assertEqual({path.name: path.read_bytes() for path in self.inbox.iterdir()}, before)
        self.assertEqual(len(self.rag.published), 20)
        envelope = json.loads(next(iter(before.values())))
        self.assertEqual(envelope["day_policy_sha256"], digest(self.plan["daily_policy"]))
        self.assertEqual(envelope["policy_sha256"], digest({"daily": self.plan["daily_policy"], "rag": self.scope}))

    def test_rag_broker_checks_priority_lists_and_global_inflight(self):
        app = Mock()
        app.conf.broker_url = "redis://generation-redis:6379/0"
        app.conf.broker_transport_options = {}
        client = Mock()
        client.llen.return_value = client.hlen.return_value = client.zcard.return_value = 0
        self.assertIn("rag_priority_counts", verify_empty_rag_broker(app, client=client))
        client.llen.side_effect = lambda key: 1 if key == "content.rag_generate\x06\x163" else 0
        with self.assertRaisesRegex(StopRun, "rag_generation_broker_not_empty"):
            verify_empty_rag_broker(app, client=client)

    def test_cli_plan_never_imports_runtime_or_publishes(self):
        scope_file = self.f.root / "scope.json"
        write_json(scope_file, self.scope)
        with patch("menu_worker.daily_resume.time.time", return_value=self.f.now), \
             patch("menu_worker.rag_daily_runtime.compose_runtime") as compose, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            code = main(["--source-checkpoint", str(self.f.source), "--source-sha256", self.f.sha,
                "--checkpoint", str(self.f.child), "--policy-date", "2026-11-24",
                "--delivery-root", str(self.f.inbox), "--rag-scope", str(scope_file),
                "--expected-rag-scope-sha256", digest(self.scope), "--rag-delivery-root", str(self.inbox),
                "--expected-rag-runtime-sha256", "b" * 64])
        self.assertEqual(code, 0, output.getvalue())
        compose.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())["plan"]["generation_enabled"])

    def test_runtime_defaults_off_and_changed_reviewed_file_blocks_publication(self):
        runtime_file = self.f.root / "runtime.json"
        database = self.f.root / "isolated.sqlite"
        database.write_bytes(b"not opened in composition")
        values = {"isolated_root": str(self.f.root), "database_path": str(database),
            "image_root": str(self.f.root), "api_key_file": "/not/read/key", "vector_dsn_file": "/not/read/dsn",
            "expected_vector_database": "rag_isolated", "catalog_work_ids": [8],
            "enabled": False, "confirm_isolated_copy": True, "offline": False,
            "pricing": {model: {"input": "1", "output": "1", "cached_input": "0.1"}
                        for model in ("gpt-6-luna", "text-embedding-3-small")}}  # Synthetic test rates.
        write_json(runtime_file, values)
        sha = hashlib.sha256(runtime_file.read_bytes()).hexdigest()
        plan = {**self.plan, "rag_runtime_sha256": sha}
        app = Mock()
        with patch("menu_worker.rag_store.RagStore", return_value=self.rag):
            runtime = compose_runtime(plan, app=app, runtime_path=runtime_file, runtime_sha256=sha)
        with self.assertRaisesRegex(StopRun, "rag_generation_disabled"):
            runtime.publish(str(uuid4()))
        app.send_task.assert_not_called()
        with self.assertRaisesRegex(StopRun, "rag_generation_disabled"):
            compose_runtime(plan, app=app, runtime_path=runtime_file, runtime_sha256=sha, enable=True)
        values["enabled"] = True
        write_json(runtime_file, values)
        sha = hashlib.sha256(runtime_file.read_bytes()).hexdigest()
        plan = {**self.plan, "rag_runtime_sha256": sha}
        with patch.dict("os.environ", {"RAG_GENERATION_ENABLED": "true", "RAG_RUNTIME_CONFIG_FILE": str(runtime_file)}), \
             patch("menu_worker.rag_store.RagStore", return_value=self.rag):
            runtime = compose_runtime(plan, app=app, runtime_path=runtime_file, runtime_sha256=sha, enable=True)
        runtime_file.write_bytes(runtime_file.read_bytes() + b" ")
        with self.assertRaisesRegex(StopRun, "rag_runtime_not_reviewed"):
            runtime.publish(str(uuid4()))
        app.send_task.assert_not_called()

    def test_original_two_unknown_period_costs_survive_mixed_day_activation(self):
        import test_daily_held_recovery as held_fixture
        held = held_fixture.DailyHeldRecoveryTests()
        held.setUp()
        self.addCleanup(held.doCleanups)
        source = held.f.child
        end = held.controller().deadline
        held.resume(now=end)  # Existing explicit recovery receipt; no new authorization.
        original = source.read_bytes()
        previous = read_json(source)
        inbox = held.f.root / "rag-inbox"
        inbox.mkdir()
        now = datetime(2026, 11, 25, 10, tzinfo=KST).timestamp()
        plan = build_plan(source=source, source_sha256=hashlib.sha256(original).hexdigest(),
            checkpoint=held.f.root / "mixed-next-day.json", policy_date="2026-11-25",
            delivery_root=held.f.inbox, now=now, routes=held.previous.plan["daily_policy"]["routes"],
            rag_scope=self.scope, rag_scope_sha256=digest(self.scope), rag_delivery_root=inbox,
            rag_runtime_sha256="b" * 64)
        rag = SealedRagStore(held.f.root)
        state, _ = activate_plan(plan, store=held.f.store, read_result=held.f.read_result,
            rag_store=rag, rag_read_result=rag.result, now=now)
        self.assertEqual(len(plan["daily_policy"]["prior_unknown_costs"]), 2)
        self.assertEqual(state["records"], previous["records"])
        self.assertEqual(state["failure_recovery_audit"], previous["failure_recovery_audit"])
        self.assertEqual(source.read_bytes(), original)
        held.assert_original_evidence()


if __name__ == "__main__":
    unittest.main()
