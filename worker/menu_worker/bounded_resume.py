"""Prepare a bounded continuation; queue publication requires --apply and a plan digest.

Planning reads only the supplied completed checkpoint. It does not connect to a
DB, Redis or the Agent. An operator must supply the newly approved cumulative
budget, additional attempt limit and timezone-qualified end time. No defaults
grant more attempts. Budgets are estimates, not provider billing hard caps.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import time

from menu_worker.bounded_generation import (
    Controller, StopRun, audit_existing_jobs, database_lock,
    local_lock, money, read_json, result_cost, timestamp, validate_reservations, write_json,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def require(condition, reason):
    if not condition:
        raise StopRun(reason)


def checkpoint_source(path, expected_sha256):
    state = read_json(path)
    require(re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or "") is not None
            and hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256,
            "source_checkpoint_digest_mismatch")
    require(state.get("schema_version") == 1 and state.get("status") == "complete"
            and state.get("stop_reason") == "new_job_slots_consumed", "source_not_completed")
    config = state.get("config", {})
    require(config.get("run_id") == "beta-open-20261116"
            and config.get("source_id") == "one_piece_official"
            and config.get("content_type") == "goods", "source_scope_changed")
    # This continuation intentionally supports the audited beta ledger only.
    # Its first paid job lives outside the dedicated DB, so DB row validation
    # alone cannot detect someone lowering the inherited baseline accounting.
    require(type(config.get("baseline_count")) is int and config["baseline_count"] == 1
            and money(config.get("baseline_estimated_cost_usd")) == Decimal("0.01178341"),
            "legacy_baseline_changed")
    require(type(config.get("max_new_jobs")) is int and config["max_new_jobs"] >= 4
            and config.get("job_timeout_seconds") == 1500
            and money(config.get("next_job_reserve_usd")) == Decimal("0.04"), "legacy_execution_limits_changed")
    rows = state.get("records")
    require(isinstance(rows, list) and rows and isinstance(state.get("seen"), list), "invalid_source_history")
    require(all(isinstance(r, dict) and type(r.get("counts_slot")) is bool
                and r.get("state") in {"complete", "duplicate"} for r in rows), "source_has_unfinished_record")
    require(sum(r["counts_slot"] for r in rows) == config.get("max_new_jobs"), "source_slot_count_mismatch")
    require(all((r["state"] == "complete") == r["counts_slot"] for r in rows), "source_slot_state_mismatch")
    for row in rows:
        require(row.get("canonical_url") in state["seen"], "source_seen_history_missing")
        if row["counts_slot"]:
            require(row.get("job_id") and row.get("request", {}).get("idempotency_key", "").startswith(config["run_id"] + ":"),
                    "source_job_identity_missing")
    return state


def inherited_reservations(state):
    reservations = {}
    for row in state["records"]:
        if row.get("accounting_method") != "reserve":
            require(not row.get("reserved_cost_usd"), "unlabelled_reservation")
            continue
        receipt = row.get("accounting_reservation")
        require(isinstance(receipt, dict), "missing_legacy_reservation_receipt")
        original = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        validated = validate_reservations({"schema_version": 1, "records": [original]})
        require(validated.get(row["job_id"]) == receipt
                and row.get("estimated_cost_usd") is None and row.get("measured_cost") is None
                and money(row.get("reserved_cost_usd")) == money(receipt["reserved_usd"]), "legacy_reservation_changed")
        reservations.update(validated)
    return reservations


def totals(state):
    inherited_reservations(state)
    known = money(state["config"]["baseline_estimated_cost_usd"])
    reserved = Decimal(0)
    for row in state["records"]:
        if not row["counts_slot"]:
            continue
        if row.get("accounting_method") == "reserve":
            reserved += money(row["reserved_cost_usd"])
        else:
            require(row.get("estimated_cost_usd") is not None, "source_cost_missing")
            known += money(row["estimated_cost_usd"])
    return known, reserved


def build_plan(*, source, source_sha256, checkpoint, total_budget_usd, additional_jobs, deadline, now=None):
    """Pure local plan. The supplied limits are a proposal until explicitly applied."""
    now = time.time() if now is None else now
    source, checkpoint = Path(source), Path(checkpoint)
    require(source.is_absolute() and checkpoint.is_absolute()
            and source.parent.resolve() == source.parent and checkpoint.parent == source.parent
            and checkpoint not in {source, claim_path(source), source.with_name(source.name + ".lock")}
            and not checkpoint.is_symlink(), "unsafe_resume_checkpoint_path")
    state = checkpoint_source(source, source_sha256)
    require(type(additional_jobs) is int and 1 <= additional_jobs <= 20, "additional_jobs_must_be_1_to_20")
    require(isinstance(total_budget_usd, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]{1,8})?", total_budget_usd),
            "explicit_total_budget_required")
    require(timestamp(deadline) > now, "deadline_reached")
    known, reserved = totals(state)
    config = copy.deepcopy(state["config"])
    require(money(config["next_job_reserve_usd"]) > 0, "invalid_next_job_reserve")
    require(money(total_budget_usd) >= known + reserved + money(config["next_job_reserve_usd"]),
            "estimated_budget_reserve_unavailable")
    require(now + config["job_timeout_seconds"] <= timestamp(deadline), "insufficient_time_for_new_job")
    config.update(total_budget_usd=total_budget_usd, max_new_jobs=config["max_new_jobs"] + additional_jobs,
                  deadline=deadline)
    return {"schema_version": 1, "source_checkpoint": str(source), "source_sha256": source_sha256,
            "checkpoint": str(checkpoint), "additional_jobs": additional_jobs, "config": config,
            "prior_record_count": len(state["records"]), "prior_records_sha256": digest(state["records"]),
            "prior_known_estimated_usd": str(known), "prior_reserved_usd": str(reserved),
            "prior_accounted_usd": str(known + reserved),
            "remaining_budget_usd": str(money(total_budget_usd) - known - reserved),
            "prior_attempts": state["config"]["baseline_count"] + state["config"]["max_new_jobs"],
            "maximum_total_attempts": config["baseline_count"] + config["max_new_jobs"],
            "provider_hard_cap": False, "generation_enabled": False}


def verify_completed_history(store, state, read_result):
    """Recheck every recorded completion against DB and immutable sealed results."""
    audit_existing_jobs(store, state)
    reservations = inherited_reservations(state)
    verifier = object.__new__(Controller)
    verifier.reservations = reservations
    for record in state["records"]:
        if record["state"] != "complete":
            continue
        job = store.get(record["job_id"])
        require(job is not None and job["execution_status"] == record.get("execution_status"), "completed_job_state_changed")
        if job["execution_status"] in {"failed", "cancelled"}:
            require(not job.get("generation_started_at") and not job.get("artifact_path")
                    and not any(a.get("generation_started_at") for a in job.get("attempts", []))
                    and money(record["estimated_cost_usd"]) == 0, "failed_generation_cost_unknown")
            continue
        require(job["execution_status"] == "succeeded"
                and job.get("outcome") == record.get("outcome")
                and job.get("producer_status") == record.get("producer_status")
                and job.get("result_sha256") == record.get("result_sha256")
                and job.get("generation_attempt_id") == record.get("generation_attempt_id"), "completed_job_evidence_changed")
        result = read_result(job)
        try:
            cost = result_cost(result)
        except StopRun as error:
            require(str(error) == "missing_cost_evidence" and record.get("accounting_method") == "reserve",
                    "completed_cost_evidence_invalid")
            receipt = verifier.matching_reservation(job, result)
            require(receipt == record["accounting_reservation"], "legacy_reservation_changed")
        else:
            require(record.get("accounting_method") != "reserve" and cost == money(record["estimated_cost_usd"]),
                    "completed_cost_evidence_changed")
    return reservations


def claim_path(source):
    return source.with_name(source.name + ".successor.json")


def validate_successor(state, parent, plan):
    require(state.get("schema_version") == 1 and state.get("config") == plan["config"]
            and state.get("resume_plan_sha256") == digest(plan), "resume_checkpoint_plan_changed")
    require(state["records"][:len(parent["records"])] == parent["records"], "inherited_history_changed")
    require(state["seen"][:len(parent["seen"])] == parent["seen"], "inherited_seen_history_changed")
    require(state.get("accounting_resume_audit", []) == parent.get("accounting_resume_audit", []),
            "inherited_accounting_audit_changed")
    require(state.get("status") in {"running", "stopped", "complete"}, "invalid_resume_status")
    require(sum(bool(r.get("counts_slot")) for r in state["records"]) <= plan["config"]["max_new_jobs"],
            "resume_job_cap_exceeded")


def activate_plan(plan, *, store, read_result, now=None):
    """Caller holds the parent file lock and run-wide DB advisory lock throughout execution."""
    source, target = Path(plan["source_checkpoint"]), Path(plan["checkpoint"])
    parent = checkpoint_source(source, plan["source_sha256"])
    expected = build_plan(source=source, source_sha256=plan["source_sha256"], checkpoint=target,
                          total_budget_usd=plan["config"]["total_budget_usd"], additional_jobs=plan["additional_jobs"],
                          deadline=plan["config"]["deadline"], now=now)
    require(expected == plan, "resume_plan_changed")
    claim = claim_path(source)
    receipt = {"schema_version": 1, "plan_sha256": digest(plan), "checkpoint": str(target),
               "source_sha256": plan["source_sha256"]}
    if claim.exists():
        require(read_json(claim) == receipt, "source_already_has_another_successor")
        require(target.is_file(), "claimed_successor_checkpoint_missing")
    if target.exists():
        state = read_json(target)
        validate_successor(state, parent, plan)
    else:
        verify_completed_history(store, parent, read_result)
        state = copy.deepcopy(parent)
        state.update(config=copy.deepcopy(plan["config"]), status="running", stop_reason=None,
                     resume_plan_sha256=digest(plan))
        state.setdefault("continuation_history", []).append({"source_checkpoint": str(source),
            "source_sha256": plan["source_sha256"], "plan_sha256": digest(plan),
            "previous_config": copy.deepcopy(parent["config"]), "additional_jobs": plan["additional_jobs"]})
        write_json(target, state)
    reservations = verify_completed_history(store, state, read_result)
    if not claim.exists():
        write_json(claim, receipt)
    return state, reservations


def publish_with_deadline(job_id, *, app, task, queue, deadline, job_timeout_seconds, now=None):
    """Expire delayed deliveries before the reserved processing window starts."""
    now = time.time() if now is None else now
    latest_start = timestamp(deadline) - job_timeout_seconds
    require(now < latest_start, "insufficient_time_for_new_job")
    return app.send_task(task, kwargs={"job_id": str(job_id)}, queue=queue,
                         expires=datetime.fromtimestamp(latest_start, tz=timezone.utc)).id


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-checkpoint", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--total-budget-usd", required=True, help="Cumulative target including all prior spend and reservations")
    parser.add_argument("--additional-jobs", type=int, required=True, help="Newly approved attempts, including skipped/failed attempts")
    parser.add_argument("--deadline", required=True, help="Newly approved ISO8601 end time including timezone")
    parser.add_argument("--apply", action="store_true", help="Publish bounded jobs; never starts a Worker or enables Beat")
    parser.add_argument("--expected-plan-sha256", help="Required with --apply; exact digest reviewed from plan output")
    args = parser.parse_args(argv)
    controller = None
    try:
        plan = build_plan(source=args.source_checkpoint, source_sha256=args.source_sha256,
                          checkpoint=args.checkpoint, total_budget_usd=args.total_budget_usd,
                          additional_jobs=args.additional_jobs, deadline=args.deadline)
        if not args.apply:
            print(json.dumps({"mode": "plan", "plan": plan, "plan_sha256": digest(plan),
                              "llm_calls": 0, "queue_writes": 0, "service_db_writes": 0}))
            return 0
        require(args.expected_plan_sha256 == digest(plan), "explicit_reviewed_plan_digest_required")
        from menu_worker.discovery_agent import discover_articles
        from menu_worker.job_contract import validate_job_request
        from menu_worker.job_store import JobStore
        from menu_worker.jobs import job_route
        from menu_worker.real_generation import read_sealed_staging_result
        from menu_worker.staging_export import export_staging_bundle
        store = JobStore()
        generation_app, generation_task, generation_queue = job_route("anime_generate")
        artifact_root = Path(plan["config"]["artifact_root"])
        bundle_root = Path(plan["config"]["bundle_root"])
        require(artifact_root.is_dir() and bundle_root.is_dir(), "existing_artifact_directories_required")
        def read_result(job):
            return read_sealed_staging_result(artifact_root, job)
        def validate(fields):
            _, canonical, key = validate_job_request(fields, "anime_generate")
            return canonical, key
        with local_lock(args.source_checkpoint), database_lock(store, plan["config"]["run_id"]) as check:
            state, reservations = activate_plan(plan, store=store, read_result=read_result)
            def guard():
                check()
                parent = checkpoint_source(args.source_checkpoint, args.source_sha256)
                validate_successor(controller.state, parent, plan)
                require(read_json(claim_path(args.source_checkpoint))["plan_sha256"] == digest(plan), "successor_claim_changed")
                audit_existing_jobs(store, controller.state)
            controller = Controller(config=plan["config"], checkpoint=args.checkpoint, store=store,
                discover=discover_articles, validate=validate,
                publish=lambda job_id: publish_with_deadline(job_id, app=generation_app, task=generation_task,
                    queue=generation_queue, deadline=plan["config"]["deadline"],
                    job_timeout_seconds=plan["config"]["job_timeout_seconds"]),
                read_result=read_result, export=lambda job_id, output: export_staging_bundle(artifact_root, store, job_id, output),
                reservations=reservations, lock_check=guard)
            try:
                report = controller.run()
            except Exception as error:
                # Never persist an old in-memory checkpoint after releasing the
                # file/DB locks: another process could already have advanced it.
                reason = str(error) if isinstance(error, StopRun) else "resume_error_inspect_checkpoint"
                controller.state.update(status="stopped", stop_reason=reason)
                controller.save()
                raise
            print(json.dumps({**report, "plan_sha256": digest(plan), "source_checkpoint_unchanged": True}))
            return 0 if report["status"] == "complete" else 2
    except Exception as error:
        reason = str(error) if isinstance(error, StopRun) else "resume_error_inspect_checkpoint"
        print(json.dumps({"error": reason, "category": type(error).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
