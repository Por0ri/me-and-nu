"""Synthetic budgets/dates are test data and do not authorize a paid run."""
import contextlib
import copy
from datetime import datetime
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from menu_worker.bounded_generation import APPROVED_RESERVATION, StopRun, local_lock, validate_reservations, write_json
from menu_worker.bounded_resume import claim_path, digest
from menu_worker.daily_generation import DailyController, KST
from menu_worker.daily_resume import activate_plan, build_plan, main, preflight_plan, source_checkpoint, verify_empty_generation_broker


class FakeStore:
    def __init__(self):
        self.jobs, self.results, self.publications, self.outcomes = {}, {}, [], []
        self.selected = {}

    def select_staging_result(self, job):
        selected = self.selected.setdefault(job["canonical_url"], job["job_id"])
        if selected != job["job_id"]:
            raise StopRun("staging_result_already_selected")

    def _connect(self):
        store = self
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, query): return self
            def fetchall(self):
                return [{"job_id": key, "idempotency_key": row["idempotency_key"]} for key, row in store.jobs.items()]
        return Connection()

    def get(self, job_id):
        return copy.deepcopy(self.jobs.get(job_id))

    def submit(self, request, job_kind):
        for row in self.jobs.values():
            if row["canonical_url"] == request["canonical_url"]:
                return {**row, "created": False}
        n = len(self.jobs) + 1
        job_id = str(UUID(int=n))
        outcome = self.outcomes.pop(0) if self.outcomes else "ready_for_staging"
        job = {"job_id": job_id, "generation_attempt_id": str(UUID(int=n + 100)), "result_sha256": f"{n:064x}",
               "idempotency_key": request["idempotency_key"], "canonical_url": request["canonical_url"],
               "execution_status": "queued", "outcome": outcome, "producer_status": "ready_for_feed",
               "generation_started_at": None, "attempts": [], "last_task_id": None}
        self.jobs[job_id] = job
        image_sha = hashlib.sha256(b"synthetic-image").hexdigest()
        self.results[job_id] = {"status": "ready_for_feed", "usage_records": [{"estimated_cost_usd": "0.01"}],
            "package": {"request_id": "test-" + str(n), "cards": [{"page": 1, "rendered_image_key": image_sha}]}}
        return {**job, "created": True}

    def publish_pending(self, job_id, publish, expected_kind):
        self.publications.append(job_id)
        self.jobs[job_id]["last_task_id"] = publish(job_id)
        self.jobs[job_id]["execution_status"] = "succeeded"
        return True


class DailyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source, self.child = self.root / "legacy.json", self.root / "day.json"
        self.bundles, self.inbox = self.root / "bundles", self.root / "inbox"
        self.bundles.mkdir(); self.inbox.mkdir()
        self.now = datetime(2026, 11, 24, 10, tzinfo=KST).timestamp()
        self.store = FakeStore()
        self.articles = [self.article(i) for i in range(60)]
        self.scans, self.exports = 0, 0
        config = {"run_id": "beta-open-20261116", "source_id": "one_piece_official", "content_type": "goods",
            "total_budget_usd": "5.00", "baseline_count": 1, "baseline_estimated_cost_usd": "0.01178341",
            "max_new_jobs": 4, "next_job_reserve_usd": "0.04", "job_timeout_seconds": 1500,
            "deadline": "2026-11-16T17:00:00+09:00", "scan_interval_seconds": 60,
            "artifact_root": str(self.root), "bundle_root": str(self.bundles), "excluded_urls": ["baseline"]}
        records = []
        for i, cost in enumerate(("0.00772830", None, "0.00933559", "0.00381376")):
            fields = self.article(-i - 1)
            fields["idempotency_key"] = "beta-open-20261116:old" + str(i)
            job = self.store.submit(fields, "anime_generate")
            job.update(execution_status="succeeded", outcome="skipped", producer_status="content_type_mismatch_skipped")
            job_id = job["job_id"]
            if cost is None:
                self.store.jobs.pop(job_id); self.store.results.pop(job_id)
                job.update(job_id=APPROVED_RESERVATION["job_id"], generation_attempt_id=APPROVED_RESERVATION["generation_attempt_id"],
                           result_sha256=APPROVED_RESERVATION["source_result_sha256"])
                job_id = job["job_id"]
            self.store.jobs[job_id] = job
            self.store.results[job_id] = ({"usage_records": [{"estimated_cost_usd": cost}]} if cost else {"status": "content_type_mismatch_skipped"})
            record = {"request": fields, "canonical_url": fields["canonical_url"], "state": "complete", "counts_slot": True,
                "job_id": job_id, "generation_attempt_id": job["generation_attempt_id"], "result_sha256": job["result_sha256"],
                "execution_status": "succeeded", "outcome": "skipped", "producer_status": "content_type_mismatch_skipped",
                "estimated_cost_usd": cost, "publish_intent": True, "published": True}
            if cost is None:
                receipt = {**APPROVED_RESERVATION, "accounting_method": "reserve", "measured_cost": None,
                           "reserved_usd": "1.00", "reason": "Historical extraction omission; retained unmeasured reservation."}
                receipt = validate_reservations({"schema_version": 1, "records": [receipt]})[job_id]
                record.update(accounting_method="reserve", reserved_cost_usd="1.00", measured_cost=None, accounting_reservation=receipt)
            records.append(record)
        state = {"schema_version": 1, "config": config, "status": "complete", "stop_reason": "new_job_slots_consumed",
                 "records": records, "seen": ["baseline", *(r["canonical_url"] for r in records)], "scans": 1,
                 "accounting_resume_audit": [records[1]["accounting_reservation"]]}
        write_json(self.source, state)
        self.original = self.source.read_bytes()
        self.sha = hashlib.sha256(self.original).hexdigest()

    def article(self, i):
        return {"source_id": "one_piece_official", "content_type": "goods", "menu_work_id": 8,
                "source_url": f"https://one-piece.com/news/test{i}/index.html", "canonical_url": f"https://one-piece.com/news/test{i}/index.html"}

    def plan(self, **changes):
        args = dict(source=self.source, source_sha256=self.sha, checkpoint=self.child, policy_date="2026-11-24",
                    daily_budget_usd="2.00", deadline="2026-11-24T23:00:00+09:00", delivery_root=self.inbox, now=self.now)
        args.update(changes)
        return build_plan(**args)

    def read_result(self, job):
        return copy.deepcopy(self.store.results[job["job_id"]])

    def activate(self, plan=None):
        return activate_plan(plan or self.plan(), store=self.store, read_result=self.read_result, now=self.now)

    def export(self, job_id, directory):
        self.exports += 1
        job = self.store.jobs[job_id]
        result = self.read_result(job)
        directory.mkdir(); (directory / "cards").mkdir()
        assets = []
        for card in result["package"]["cards"]:
            relative = f"cards/card_{card['page']:02d}.png"
            (directory / relative).write_bytes(b"synthetic-image")
            assets.append({"page": card["page"], "path": relative, "sha256": card["rendered_image_key"]})
        evidence = {key: value for key, value in result.items() if key in {"status", "usage_records"}}
        evidence.update(jobId=job_id, generationAttemptId=job["generation_attempt_id"], resultSha256=job["result_sha256"], operatorReviewCreated=False)
        document = {"schemaVersion": 1, "producer": "ani_menu_agent", "topicCode": "anime",
            "items": [{"package": result["package"], "assets": assets, "reviewEvidence": evidence}]}
        path = directory / "bundle.json"
        write_json(path, document)
        self.store.select_staging_result(job)
        return {"bundle_path": str(path)}

    def discover(self, *args):
        batch = self.articles[self.scans * 20:(self.scans + 1) * 20]
        self.scans += 1
        return {"articles": copy.deepcopy(batch), "discovered": len(batch)}

    def controller(self, plan, reservations):
        return DailyController(config=plan["config"], checkpoint=Path(plan["checkpoint"]), store=self.store,
            discover=self.discover, validate=lambda fields: (fields["canonical_url"], hashlib.sha256(fields["canonical_url"].encode()).hexdigest()),
            publish=lambda key: "task-" + key, read_result=self.read_result, export=self.export,
            now=lambda: self.now, sleep=lambda seconds: setattr(self, "now", self.now + seconds), reservations=reservations)

    def test_plan_preserves_prior_cost_and_has_no_activation_side_effects(self):
        before = sorted(p.name for p in self.root.iterdir())
        plan = self.plan()
        self.assertEqual(plan["config"]["total_budget_usd"], "3.03266106")
        self.assertEqual(plan["daily_policy"]["prior_accounted_usd"], "1.03266106")
        self.assertEqual(plan["daily_policy"]["max_attempts"], 40)
        self.assertEqual(plan["daily_policy"]["success_target"], 20)
        self.assertEqual(plan["config"]["max_new_jobs"], 44)
        self.assertFalse(plan["generation_enabled"])
        self.assertEqual(before, sorted(p.name for p in self.root.iterdir()))

    def test_preflight_verifies_evidence_but_creates_no_checkpoint_or_successor(self):
        plan = self.plan()
        before = sorted(p.name for p in self.root.iterdir())
        report = preflight_plan(plan, store=self.store, read_result=self.read_result, now=self.now)
        self.assertTrue(report["verified"])
        self.assertEqual(report["historical_jobs_verified"], 4)
        self.assertEqual(report["checkpoint_writes"], 0)
        self.assertEqual(before, sorted(p.name for p in self.root.iterdir()))
        self.assertEqual(self.store.publications, [])
        self.assertEqual(self.source.read_bytes(), self.original)
        first = next(iter(self.store.jobs.values()))
        first["result_sha256"] = "0" * 64
        with self.assertRaisesRegex(StopRun, "completed_job_evidence_changed"):
            preflight_plan(plan, store=self.store, read_result=self.read_result, now=self.now)
        self.assertFalse(self.child.exists())
        self.assertFalse(claim_path(self.source).exists())

    def test_broker_preflight_checks_every_priority_and_unacked_receipt_without_writes(self):
        class Client:
            def __init__(self): self.pending, self.calls = {}, []
            def llen(self, key): self.calls.append(("llen", key)); return self.pending.get(key, 0)
            def hlen(self, key): self.calls.append(("hlen", key)); return self.pending.get(key, 0)
            def zcard(self, key): self.calls.append(("zcard", key)); return self.pending.get(key, 0)
        app = type("App", (), {"conf": type("Config", (), {"broker_url": "redis://generation-redis:6379/0", "broker_transport_options": {}})()})()
        client = Client()
        self.assertEqual(verify_empty_generation_broker(app, client=client)["ready"], 0)
        self.assertEqual(len(client.calls), 6)
        for key in ("anime-generation-live", "anime-generation-live\x06\x163", "anime-generation-live\x06\x166",
                    "anime-generation-live\x06\x169", "unacked", "unacked_index"):
            client.pending = {key: 1}
            with self.subTest(key=key), self.assertRaisesRegex(StopRun, "generation_broker_not_empty"):
                verify_empty_generation_broker(app, client=client)
        app.conf.broker_url = "redis://session-redis:6379/0"
        with self.assertRaisesRegex(StopRun, "unexpected_generation_broker_layout"):
            verify_empty_generation_broker(app, client=client)

    def test_stops_at_twenty_reviewed_results_and_emits_bound_relative_envelopes(self):
        plan = self.plan(); _, reservations = self.activate(plan)
        report = self.controller(plan, reservations).run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"], report["daily_deliveries"]), (20, 20, 20))
        self.assertEqual(report["stop_reason"], "daily_success_target_reached")
        self.assertEqual(Decimal(report["daily_spent_usd"]), Decimal("0.20"))
        self.assertEqual(report["reserved_cost_usd"], "1.00")
        self.assertEqual(len(self.store.publications), 20)
        envelope = read_one(self.inbox)
        self.assertEqual(envelope["day_policy_sha256"], plan["day_policy_sha256"])
        self.assertEqual(envelope["bundle_path"], envelope["job_id"] + "/bundle.json")
        saved = self.child.read_bytes()
        self.activate(plan)
        self.controller(plan, reservations).run()
        self.assertEqual(len(self.store.publications), 20)
        self.assertEqual(self.child.read_bytes(), saved)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_skipped_held_rejected_attempts_hit_forty_cap_without_success_inflation(self):
        plan = self.plan(); _, reservations = self.activate(plan)
        self.store.outcomes = ["held", "skipped", "rejected", "ready_for_staging"] * 10
        report = self.controller(plan, reservations).run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (40, 10))
        self.assertEqual(report["stop_reason"], "daily_attempt_limit_reached")
        self.assertEqual(len(self.store.publications), 40)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_legacy_and_repeated_candidates_do_not_count_as_new_attempts(self):
        plan = self.plan(); _, reservations = self.activate(plan)
        self.articles = [self.article(-1), self.article(0), self.article(0), *[self.article(i) for i in range(1, 30)]]
        report = self.controller(plan, reservations).run()
        self.assertEqual((report["daily_attempts"], report["daily_successes"]), (20, 20))
        self.assertEqual(len(set(self.store.publications)), 20)

    def test_daily_budget_stops_without_erasing_old_reserve_and_next_day_can_be_planned(self):
        plan = self.plan(daily_budget_usd="0.05"); _, reservations = self.activate(plan)
        controller = self.controller(plan, reservations)
        with self.assertRaisesRegex(StopRun, "estimated_budget_reserve_unavailable"):
            controller.run()
        report = controller.summary()
        self.assertEqual(report["daily_attempts"], 2)
        self.assertEqual(Decimal(report["daily_spent_usd"]), Decimal("0.02"))
        self.assertEqual(report["reserved_cost_usd"], "1.00")
        child_sha = hashlib.sha256(self.child.read_bytes()).hexdigest()
        tomorrow = self.plan(source=self.child, source_sha256=child_sha, checkpoint=self.root / "tomorrow.json",
                             policy_date="2026-11-25", deadline="2026-11-25T23:00:00+09:00")
        self.assertEqual(Decimal(tomorrow["daily_policy"]["prior_accounted_usd"]), Decimal("1.05266106"))
        self.assertEqual(tomorrow["daily_policy"]["prior_slots"], 6)

    def test_same_day_new_plan_and_rewound_database_history_are_blocked(self):
        plan = self.plan(); _, reservations = self.activate(plan)
        snapshot = self.child.read_bytes()
        self.controller(plan, reservations).run()
        child_sha = hashlib.sha256(self.child.read_bytes()).hexdigest()
        with self.assertRaisesRegex(StopRun, "daily_limit_cannot_be_reset"):
            self.plan(source=self.child, source_sha256=child_sha, checkpoint=self.root / "more.json")
        self.child.write_bytes(snapshot)
        with self.assertRaisesRegex(StopRun, "database_history_not_owned_by_checkpoint"):
            self.activate(plan)

    def test_parallel_successors_and_changed_budget_or_date_are_blocked(self):
        self.activate()
        for change in ({"checkpoint": self.root / "fork.json"}, {"daily_budget_usd": "3"},
                       {"policy_date": "2026-11-25", "deadline": "2026-11-25T23:00:00+09:00"}):
            with self.subTest(change=change), self.assertRaisesRegex(StopRun, "source_already_has_another_successor"):
                self.activate(self.plan(**change))
        with local_lock(self.source):
            with self.assertRaisesRegex(StopRun, "controller_already_running"):
                with local_lock(self.source): pass

    def test_today_cannot_activate_tomorrow_policy(self):
        plan = self.plan(policy_date="2026-11-25", deadline="2026-11-25T23:00:00+09:00")
        with self.assertRaisesRegex(StopRun, "daily_window_not_open"):
            self.activate(plan)
        self.assertFalse(self.child.exists())

    def test_kst_date_deadline_and_explicit_budget_are_required(self):
        for change in ({"daily_budget_usd": "0"}, {"daily_budget_usd": "NaN"}, {"policy_date": "2026-02-30"},
                       {"deadline": "2026-11-25T01:00:00+09:00"}, {"deadline": "2026-11-24T23:00:00"}):
            with self.subTest(change=change), self.assertRaises(StopRun): self.plan(**change)
        self.assertEqual(self.plan(deadline="2026-11-24T14:00:00+00:00")["daily_policy"]["policy_date"], "2026-11-24")

    def test_unknown_cost_stops_and_cannot_be_rolled_into_next_day(self):
        plan = self.plan(); _, reservations = self.activate(plan)
        controller = self.controller(plan, reservations)
        controller.read_result = lambda job: {}
        with self.assertRaisesRegex(StopRun, "missing_cost_evidence"): controller.run()
        self.assertEqual(len(self.store.publications), 1)
        with self.assertRaisesRegex(StopRun, "source_has_unfinished_record"):
            self.plan(source=self.child, source_sha256=hashlib.sha256(self.child.read_bytes()).hexdigest(),
                      checkpoint=self.root / "tomorrow.json", policy_date="2026-11-25", deadline="2026-11-25T23:00:00+09:00")

    def test_crash_after_export_recovers_original_bundle_without_regeneration(self):
        plan = self.plan(); _, reservations = self.activate(plan)
        controller = self.controller(plan, reservations)
        def crash(job_id, destination):
            self.export(job_id, destination)
            raise SystemExit("crash after atomic export")
        controller.export = crash
        with self.assertRaises(SystemExit): controller.run()
        self.assertEqual((self.exports, len(self.store.publications)), (1, 1))
        self.activate(plan)
        restart = self.controller(plan, reservations)
        record = restart.state["records"][-1]
        restart.finish_job(record)
        self.assertEqual((self.exports, len(self.store.publications)), (1, 1))
        self.assertEqual(len(list(self.inbox.glob("*.json"))), 1)
        self.assertEqual(record["state"], "complete")

    def test_crash_after_completion_repairs_missing_envelope_even_after_deadline(self):
        plan = self.plan(); _, reservations = self.activate(plan)
        controller = self.controller(plan, reservations)
        controller._emit_delivery = lambda record: (_ for _ in ()).throw(SystemExit("crash before envelope"))
        with self.assertRaises(SystemExit): controller.run()
        self.now = datetime(2026, 11, 25, 1, tzinfo=KST).timestamp()
        self.activate(plan)
        with self.assertRaisesRegex(StopRun, "deadline_reached"):
            self.controller(plan, reservations).run()
        self.assertEqual((self.exports, len(self.store.publications)), (1, 1))
        self.assertEqual(len(list(self.inbox.glob("*.json"))), 1)

    def test_lost_db_lock_prevents_date_stop_and_restart_envelope_writes(self):
        plan = self.plan(); _, reservations = self.activate(plan)
        controller = self.controller(plan, reservations)
        controller._emit_delivery = lambda record: (_ for _ in ()).throw(SystemExit("before delivery"))
        with self.assertRaises(SystemExit): controller.run()
        original = self.child.read_bytes()
        self.now = datetime(2026, 11, 25, 1, tzinfo=KST).timestamp()
        restarted = self.controller(plan, reservations)
        def lost(): raise StopRun("simulated_db_lock_lost")
        restarted.lock_check = lost
        with self.assertRaisesRegex(StopRun, "simulated_db_lock_lost"):
            restarted.check()
        self.assertEqual(self.child.read_bytes(), original)
        with self.assertRaisesRegex(StopRun, "simulated_db_lock_lost"):
            restarted.run()
        self.assertEqual(self.child.read_bytes(), original)
        self.assertEqual(list(self.inbox.glob("*.json")), [])
        self.assertEqual((self.exports, len(self.store.publications)), (1, 1))

    def test_tampered_bundle_or_envelope_is_not_overwritten(self):
        plan = self.plan(); _, reservations = self.activate(plan)
        controller = self.controller(plan, reservations)
        controller._emit_delivery = lambda record: (_ for _ in ()).throw(SystemExit())
        with self.assertRaises(SystemExit): controller.run()
        record = controller.state["records"][-1]
        bundle = Path(record["bundle_path"])
        original = bundle.read_bytes()
        document = json.loads(original); document["items"][0]["reviewEvidence"]["operatorReviewCreated"] = True
        write_json(bundle, document)
        with self.assertRaisesRegex(StopRun, "delivery_bundle_evidence_changed"):
            self.controller(plan, reservations).run()
        bundle.write_bytes(original)
        write_json(self.inbox / (record["job_id"] + ".json"), {"wrong": True})
        with self.assertRaisesRegex(StopRun, "delivery_envelope_changed"):
            self.controller(plan, reservations).run()

    def test_restored_exact_bundle_cannot_bypass_another_selected_article_result(self):
        plan = self.plan(); _, reservations = self.activate(plan)
        controller = self.controller(plan, reservations)
        def crash(job_id, destination):
            self.export(job_id, destination)
            raise SystemExit("crash after export")
        controller.export = crash
        with self.assertRaises(SystemExit): controller.run()
        record = controller.state["records"][-1]
        self.store.selected[record["canonical_url"]] = "another-result"
        with self.assertRaisesRegex(StopRun, "staging_result_already_selected"):
            self.controller(plan, reservations).finish_job(record)
        self.assertEqual(list(self.inbox.glob("*.json")), [])
        self.assertEqual((self.exports, len(self.store.publications)), (1, 1))

    def test_cli_plan_and_unapproved_apply_never_connect_or_create_checkpoint(self):
        args = ["--source-checkpoint", str(self.source), "--source-sha256", self.sha, "--checkpoint", str(self.child),
                "--policy-date", "2026-11-24", "--daily-budget-usd", "2", "--deadline", "2026-11-24T23:00:00+09:00",
                "--delivery-root", str(self.inbox)]
        with patch("menu_worker.daily_resume.time.time", return_value=self.now), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(out.getvalue())["queue_writes"], 0)
        with patch("menu_worker.daily_resume.time.time", return_value=self.now), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main([*args, "--apply"]), 2)
            self.assertEqual(json.loads(out.getvalue())["error"], "explicit_reviewed_plan_digest_required")
        self.assertFalse(self.child.exists())


def read_one(directory):
    return json.loads(next(directory.glob("*.json")).read_text())


if __name__ == "__main__":
    unittest.main()
