"""Plan one explicitly approved KST day; never registers a recurring schedule.

The default is a local-only, count-limited plan. --apply requires its exact
reviewed digest. Historical cost and reservations remain in the ledger. No
service DB, operator approval endpoint or LLM is called by this controller.
"""
from __future__ import annotations

import argparse
import copy
from datetime import date, datetime, time as datetime_time, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from menu_worker.bounded_generation import StopRun, audit_existing_jobs, database_lock, local_lock, money, read_json, timestamp, write_json
from menu_worker.bounded_resume import claim_path, digest, inherited_reservations, publish_with_deadline, require, totals, unknown_cost_references, verify_completed_history
from menu_worker.daily_generation import DAILY_COMPLETIONS, DAILY_SUCCESS_TARGET, KST, MAX_DAILY_ATTEMPTS, SAFE_DAY_STOPS, DailyController


def _validated_routes(routes, *, current_catalog=True):
    from menu_worker.daily_routes import validate_routes
    try:
        return validate_routes(routes, current_catalog=current_catalog)
    except ValueError as error:
        # Registry validation uses fixed codes, never publisher data or secrets.
        code = str(error)
        require(False, code if code in {"invalid_daily_routes", "daily_route_unavailable",
                                      "catalog_unavailable", "invalid_catalog"} else "invalid_daily_routes")


def day_bounds(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "invalid_policy_date")
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise StopRun("invalid_policy_date") from None
    start = datetime.combine(day, datetime_time.min, KST)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def validate_policy_history(state):
    policies = state.get("daily_policies", [])
    require(isinstance(policies, list), "invalid_daily_policy_history")
    if not policies:
        require("daily_policy" not in state, "daily_policy_history_missing")
        return
    require(state.get("daily_policy") == policies[-1], "daily_policy_history_changed")
    previous_date, previous_index = "", 0
    for policy in policies:
        require(isinstance(policy, dict), "invalid_daily_policy_history")
        require(type(policy.get("schema_version")) is int and policy["schema_version"] in {1, 2, 3},
                "invalid_daily_policy_schema")
        if policy["schema_version"] in {2, 3}:
            require(policy.get("allocation") == "balanced_work_type_source_v1"
                    and "source_id" not in policy and "content_type" not in policy,
                    "invalid_daily_policy_scope")
            require(_validated_routes(policy.get("routes"), current_catalog=False) == policy["routes"],
                    "invalid_daily_routes")
        else:
            require("routes" not in policy and "allocation" not in policy, "invalid_daily_policy_scope")
        _, end = day_bounds(policy["policy_date"])
        if policy["schema_version"] == 3:
            require(policy.get("limit_mode") == "daily_counts" and "daily_budget_usd" not in policy
                    and policy.get("deadline") == datetime.fromtimestamp(end, KST).isoformat(),
                    "invalid_daily_count_policy")
        else:
            require("limit_mode" not in policy, "invalid_daily_policy_schema")
        index = policy["start_record_index"]
        require(policy["policy_date"] > previous_date and type(index) is int and previous_index <= index <= len(state["records"]),
                "daily_policy_order_changed")
        require(policy["max_attempts"] == MAX_DAILY_ATTEMPTS and policy["success_target"] == DAILY_SUCCESS_TARGET,
                "daily_policy_limits_changed")
        prefix = {**state, "records": state["records"][:index]}
        known, reserved = totals(prefix)
        unknown = unknown_cost_references(prefix)
        require(policy.get("prior_unknown_costs", []) == unknown, "daily_unknown_cost_baseline_changed")
        require(policy["prior_records_sha256"] == digest(prefix["records"])
                and policy["prior_slots"] == sum(r["counts_slot"] for r in prefix["records"])
                and money(policy["prior_accounted_usd"]) == known + reserved, "daily_policy_baseline_changed")
        previous_date, previous_index = policy["policy_date"], index
    for number, policy in enumerate(policies):
        end = policies[number + 1]["start_record_index"] if number + 1 < len(policies) else len(state["records"])
        rows = state["records"][policy["start_record_index"]:end]
        require(all(row.get("policy_date") == policy["policy_date"] and row.get("day_policy_sha256") == digest(policy)
                    for row in rows), "daily_record_policy_changed")
        require(sum(bool(r.get("counts_slot")) for r in rows) <= MAX_DAILY_ATTEMPTS, "daily_attempt_limit_exceeded")
        if policy["schema_version"] in {2, 3}:
            require(all({key: row.get("request", {}).get(key) for key in ("source_id", "content_type", "menu_work_id")}
                        in policy["routes"] for row in rows if row.get("producer") != "ani_rag"), "daily_record_outside_scope")
            rag_rows = [row for row in rows if row.get("producer") == "ani_rag"]
            if rag_rows:
                from menu_worker.rag_daily import validate_scope
                from menu_worker.rag_contract import RagRequest, validate_admission_receipt
                scope = validate_scope(state.get("rag_scope"))
                require(digest(scope) == state.get("rag_scope_sha256"), "rag_daily_scope_changed")
                for row in rag_rows:
                    from menu_worker.rag_daily_runtime import record_request
                    from menu_worker.rag_daily import rag_route
                    request = record_request(row)
                    require(all(row["request"].get(key) == value for key, value in rag_route(request).items())
                            and row["request"].get("idempotency_key") == state["config"]["run_id"] + ":rag:" + request.identity_key,
                            "daily_rag_identity_changed")
                    require(any(request.namespace == route["namespace"]
                                and request.menu_work_id == route["menu_work_id"]
                                and request.content_type == route["content_type"]
                                and set(request.allowed_source_ids) <= set(route["allowed_source_ids"])
                                for route in scope["routes"]), "daily_rag_record_outside_scope")
                    receipt = validate_admission_receipt(row["rag_admission"], request)
                    require(receipt["policy_sha256"] == digest({"daily": policy, "rag": scope})
                            and receipt["policy_date"] == policy["policy_date"]
                            and row["canonical_url"] == "urn:menu-rag:" + request.identity_key,
                            "daily_rag_admission_changed")
        successes = {(r.get("request", {}).get("source_id"), r["canonical_url"]) if policy["schema_version"] in {2, 3}
                     else r["canonical_url"] for r in rows if r.get("counts_slot") and r.get("state") == "complete"
                     and r.get("execution_status") == "succeeded" and r.get("outcome") == "ready_for_staging"}
        require(len(successes) <= DAILY_SUCCESS_TARGET, "daily_success_limit_exceeded")
    if policies[-1]["schema_version"] == 3:
        config = state.get("config", {})
        require(config.get("limit_mode") == "daily_counts" and "total_budget_usd" in config
                and config["total_budget_usd"] is None and config.get("deadline") == policies[-1]["deadline"],
                "daily_count_config_changed")


def source_checkpoint(path, expected_sha256):
    state = read_json(path)
    require(re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or "") is not None
            and hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256, "source_checkpoint_digest_mismatch")
    require(state.get("schema_version") == 1, "invalid_checkpoint_schema")
    config = state.get("config", {})
    require(config.get("run_id") == "beta-open-20261116" and config.get("source_id") == "one_piece_official"
            and config.get("content_type") == "goods", "source_scope_changed")
    require(type(config.get("baseline_count")) is int and config["baseline_count"] == 1
            and money(config.get("baseline_estimated_cost_usd")) == Decimal("0.01178341"), "legacy_baseline_changed")
    require(type(config.get("max_new_jobs")) is int and config["max_new_jobs"] >= 4
            and config.get("job_timeout_seconds") == 1500 and money(config.get("next_job_reserve_usd")) == Decimal("0.04"),
            "legacy_execution_limits_changed")
    rows = state.get("records")
    require(isinstance(rows, list) and rows and isinstance(state.get("seen"), list), "invalid_source_history")
    require(all(isinstance(r, dict) and type(r.get("counts_slot")) is bool and r.get("state") in {"complete", "duplicate"}
                and ((r["state"] == "complete") == r["counts_slot"]) for r in rows), "source_has_unfinished_record")
    slots = sum(r["counts_slot"] for r in rows)
    require(slots <= config["max_new_jobs"], "source_slot_count_mismatch")
    identities = [r["job_id"] for r in rows if r["counts_slot"]]
    require(len(set(identities)) == len(identities), "source_repeated_job")
    for row in rows:
        require(row.get("canonical_url") in state["seen"], "source_seen_history_missing")
        if row["counts_slot"]:
            require(row.get("request", {}).get("idempotency_key", "").startswith(config["run_id"] + ":"), "source_job_identity_missing")
        if row.get("producer") == "ani_rag":
            from menu_worker.rag_daily_runtime import record_request
            request = record_request(row)
            require(row["request"]["idempotency_key"] == config["run_id"] + ":rag:" + request.identity_key,
                    "source_rag_identity_changed")
    validate_policy_history(state)
    from menu_worker.failure_recovery import validate_recovery_history
    validate_recovery_history(state)
    if state.get("daily_policies"):
        allowed = (state.get("status") == "complete" and state.get("stop_reason") in DAILY_COMPLETIONS
                   or state.get("status") == "stopped" and state.get("stop_reason") in SAFE_DAY_STOPS)
        require(allowed, "daily_source_requires_reconciliation")
    else:
        require(state.get("status") == "complete" and state.get("stop_reason") == "new_job_slots_consumed"
                and slots == config["max_new_jobs"], "source_not_completed")
    totals(state)  # Never treat unknown/omitted prior cost as zero.
    return state


def build_plan(*, source, source_sha256, checkpoint, policy_date, delivery_root,
               daily_budget_usd=None, deadline=None, now=None, routes=None,
               rag_scope=None, rag_scope_sha256=None, rag_delivery_root=None, rag_runtime_sha256=None):
    now = time.time() if now is None else now
    source, checkpoint, delivery_root = Path(source), Path(checkpoint), Path(delivery_root)
    require(source.is_absolute() and checkpoint.is_absolute() and source.parent.resolve() == source.parent
            and checkpoint.parent == source.parent and checkpoint not in {source, claim_path(source), source.with_name(source.name + ".lock")}
            and not checkpoint.is_symlink(), "unsafe_daily_checkpoint_path")
    require(delivery_root.is_absolute() and delivery_root.is_dir() and delivery_root.resolve() == delivery_root,
            "existing_delivery_root_required")
    parent = source_checkpoint(source, source_sha256)
    start, end = day_bounds(policy_date)
    count_only = daily_budget_usd is None and deadline is None
    rag_plan = {}
    require(not parent.get("rag_scope") or rag_scope is not None, "explicit_rag_scope_required_for_rollover")
    if rag_scope is not None:
        from menu_worker.rag_daily import validate_scope
        scope = validate_scope(rag_scope, current_catalog=True)
        require(count_only, "rag_requires_shared_daily_count_policy")
        require(digest(scope) == rag_scope_sha256, "rag_scope_not_reviewed")
        require(parent.get("rag_scope", scope) == scope
                and parent.get("rag_scope_sha256", rag_scope_sha256) == rag_scope_sha256,
                "rag_daily_scope_changed")
        require(re.fullmatch(r"[0-9a-f]{64}", rag_runtime_sha256 or "") is not None,
                "explicit_reviewed_rag_runtime_required")
        inbox = Path(rag_delivery_root) if rag_delivery_root is not None else Path()
        require(inbox.is_absolute() and inbox.resolve() == inbox and inbox.is_dir()
                and not inbox.is_relative_to(delivery_root) and not delivery_root.is_relative_to(inbox),
                "separate_rag_delivery_root_required")
        rag_plan = {"rag_scope": scope, "rag_scope_sha256": rag_scope_sha256,
                    "rag_delivery_root": str(inbox), "rag_runtime_sha256": rag_runtime_sha256}
    else:
        require(rag_scope_sha256 is None and rag_delivery_root is None and rag_runtime_sha256 is None,
                "rag_options_require_reviewed_scope")
    require(count_only or daily_budget_usd is not None and deadline is not None,
            "legacy_budget_and_deadline_required_together")
    if count_only:
        deadline = datetime.fromtimestamp(end, KST).isoformat()
        if routes is None:
            from menu_worker.daily_routes import catalog_routes
            routes = catalog_routes()["routes"]
    else:
        require(isinstance(daily_budget_usd, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]{1,8})?", daily_budget_usd)
                and money(daily_budget_usd) >= money(parent["config"]["next_job_reserve_usd"]), "explicit_daily_budget_required")
    require(start < timestamp(deadline) <= end, "deadline_outside_policy_day")
    require(checkpoint.exists() or now + parent["config"]["job_timeout_seconds"] < timestamp(deadline), "insufficient_time_for_new_day")
    history = parent.get("daily_policies", [])
    require(not history or policy_date > history[-1]["policy_date"], "daily_limit_cannot_be_reset")
    known, reserved = totals(parent)
    unknown = unknown_cost_references(parent)
    require(count_only or not unknown, "unknown_cost_in_budget_mode")
    slots = sum(r["counts_slot"] for r in parent["records"])
    policy = {"schema_version": 1, "policy_date": policy_date, "daily_budget_usd": daily_budget_usd,
        "deadline": deadline, "max_attempts": MAX_DAILY_ATTEMPTS, "success_target": DAILY_SUCCESS_TARGET,
        "run_id": parent["config"]["run_id"], "source_id": parent["config"]["source_id"],
        "content_type": parent["config"]["content_type"], "start_record_index": len(parent["records"]),
        "prior_records_sha256": digest(parent["records"]), "prior_slots": slots,
        "prior_accounted_usd": str(known + reserved), "delivery_root": str(delivery_root)}
    if unknown:
        policy["prior_unknown_costs"] = unknown
    if routes is not None:
        policy.update(schema_version=2, routes=_validated_routes(routes), allocation="balanced_work_type_source_v1")
        # The old source/type stays in config only as the immutable ledger origin.
        # Actual work scope is the reviewed route list and each record's request.
        del policy["source_id"], policy["content_type"]
    config = copy.deepcopy(parent["config"])
    if count_only:
        policy.update(schema_version=3, limit_mode="daily_counts")
        del policy["daily_budget_usd"]
        config["limit_mode"] = "daily_counts"
    else:
        config.pop("limit_mode", None)
    config.update(total_budget_usd=None if count_only else str(known + reserved + money(daily_budget_usd)),
                  max_new_jobs=slots + MAX_DAILY_ATTEMPTS, deadline=deadline)
    return {"schema_version": 1, "source_checkpoint": str(source), "source_sha256": source_sha256,
        "checkpoint": str(checkpoint), "config": config, "daily_policy": policy,
        "day_policy_sha256": digest(policy), "prior_known_estimated_usd": str(known),
        "prior_reserved_usd": str(reserved), "generation_enabled": False, "provider_hard_cap": False, **rag_plan}


def validate_child(state, parent, plan):
    require(state.get("schema_version") == 1 and state.get("config") == plan["config"]
            and state.get("daily_plan_sha256") == digest(plan), "daily_checkpoint_plan_changed")
    require(state["records"][:len(parent["records"])] == parent["records"]
            and state["seen"][:len(parent["seen"])] == parent["seen"], "inherited_history_changed")
    require(state.get("accounting_resume_audit", []) == parent.get("accounting_resume_audit", []), "inherited_accounting_audit_changed")
    for key in ("failure_recovery_audit", "failure_recovery_resume_audit"):
        inherited = parent.get(key, [])
        require(state.get(key, [])[:len(inherited)] == inherited, "inherited_failure_recovery_audit_changed")
    require(state.get("daily_policies") == [*parent.get("daily_policies", []), plan["daily_policy"]], "daily_policy_history_changed")
    require(state.get("rag_scope") == plan.get("rag_scope")
            and state.get("rag_scope_sha256") == plan.get("rag_scope_sha256"), "rag_daily_scope_changed")
    validate_policy_history(state)
    from menu_worker.failure_recovery import validate_recovery_history
    validate_recovery_history(state)
    require(state.get("status") in {"running", "complete", "stopped"}, "invalid_daily_status")


def _checked_state(plan, *, store, read_result, now=None, rag_store=None, rag_read_result=None):
    """Validate under both locks without changing checkpoints, claims or jobs."""
    now = time.time() if now is None else now
    source, target = Path(plan["source_checkpoint"]), Path(plan["checkpoint"])
    policy = plan["daily_policy"]
    parent = source_checkpoint(source, plan["source_sha256"])
    expected = build_plan(source=source, source_sha256=plan["source_sha256"], checkpoint=target,
        policy_date=policy["policy_date"], daily_budget_usd=policy.get("daily_budget_usd"),
        deadline=None if policy.get("schema_version") == 3 else policy["deadline"],
        delivery_root=Path(policy["delivery_root"]), now=now, routes=policy.get("routes"),
        **{key: plan[key] for key in ("rag_scope", "rag_scope_sha256", "rag_delivery_root", "rag_runtime_sha256") if key in plan})
    require(expected == plan, "daily_plan_changed")
    claim = claim_path(source)  # Shared with legacy continuation: never fork that ledger.
    receipt = {"schema_version": 1, "plan_sha256": digest(plan), "checkpoint": str(target), "source_sha256": plan["source_sha256"]}
    if claim.exists():
        require(read_json(claim) == receipt, "source_already_has_another_successor")
        require(target.is_file(), "claimed_successor_checkpoint_missing")
    if target.exists():
        require(not claim_path(target).exists(), "checkpoint_already_continued")
        state = read_json(target)
        validate_child(state, parent, plan)
    else:
        start, _ = day_bounds(policy["policy_date"])
        require(start <= now < timestamp(policy["deadline"]) - plan["config"]["job_timeout_seconds"], "daily_window_not_open")
        state = copy.deepcopy(parent)
        state.update(config=copy.deepcopy(plan["config"]), status="running", stop_reason=None,
            daily_plan_sha256=digest(plan), daily_policy=copy.deepcopy(policy),
            daily_policies=[*copy.deepcopy(parent.get("daily_policies", [])), copy.deepcopy(policy)])
        if plan.get("rag_scope"):
            state.update(rag_scope=copy.deepcopy(plan["rag_scope"]), rag_scope_sha256=plan["rag_scope_sha256"])
    reservations = verify_completed_history(store, state, read_result,
        rag_store=rag_store, rag_read_result=rag_read_result)
    return state, reservations, receipt


def activate_plan(plan, *, store, read_result, now=None, rag_store=None, rag_read_result=None):
    """Caller holds the source file lock and unchanged run-wide DB lock."""
    state, reservations, receipt = _checked_state(plan, store=store, read_result=read_result, now=now,
        rag_store=rag_store, rag_read_result=rag_read_result)
    target, claim = Path(plan["checkpoint"]), claim_path(Path(plan["source_checkpoint"]))
    if not target.exists():
        write_json(target, state)
    if not claim.exists():
        write_json(claim, receipt)
    return state, reservations


def current_failure_references(state):
    start = state["daily_policy"]["start_record_index"]
    return [row["failure_recovery_receipt"] for row in state["records"][start:]
            if row.get("accounting_method") == "unknown" or row.get("failure_recovery_receipt")]


def validate_failure_resume(state, reviewed_sha256, *, store, now=None):
    """Read-only gate for a separate, explicit resume; reconciliation grants none."""
    from menu_worker.failure_recovery import validate_recovery_history
    receipts = validate_recovery_history(state, store=store,
                                         artifact_root=Path(state["config"]["artifact_root"]))
    validate_policy_history(state)
    refs = current_failure_references(state)
    require(refs and refs[-1]["receipt_sha256"] == reviewed_sha256,
            "explicit_reviewed_failure_receipt_required")
    receipt = next((item for item in receipts if digest(item) == reviewed_sha256), None)
    require(receipt is not None, "failure_resume_receipt_missing")
    paused = state.get("status") == "stopped" and state.get("stop_reason") == "failure_reconciled_paused"
    audits = state.get("failure_recovery_resume_audit", [])
    already_authorized = (bool(audits) and audits[-1].get("receipt_sha256") == reviewed_sha256
                          and audits[-1].get("policy_date") == state["daily_policy"]["policy_date"])
    terminal = (state.get("status") == "complete" and state.get("stop_reason") in DAILY_COMPLETIONS
                or state.get("status") == "stopped" and state.get("stop_reason") in SAFE_DAY_STOPS)
    require(paused or already_authorized and (state.get("status") == "running" or terminal),
            "failure_resume_not_allowed")
    if paused:
        require(all(row.get("state") in {"complete", "duplicate"} for row in state["records"]),
                "failure_resume_unfinished_record")
    return receipt


def resume_failure_recovery(state, reviewed_sha256, *, store, now=None):
    """Caller holds file/DB locks; no job, queue, artifact or service writes."""
    now = time.time() if now is None else now
    validate_failure_resume(state, reviewed_sha256, store=store, now=now)
    if state.get("stop_reason") != "failure_reconciled_paused":
        return state  # Exact explicit retry after the durable transition.
    start, _ = day_bounds(state["daily_policy"]["policy_date"])
    require(now >= start, "daily_window_not_open")
    state.setdefault("failure_recovery_resume_audit", []).append({
        "receipt_sha256": reviewed_sha256, "policy_date": state["daily_policy"]["policy_date"],
        "resumed_at": now, "previous_status": state["status"], "previous_stop_reason": state["stop_reason"],
        "cost_total_complete": False})
    deadline = timestamp(state["config"]["deadline"])
    if now >= deadline:
        state.update(status="stopped", stop_reason="deadline_reached")
    elif now + state["config"]["job_timeout_seconds"] >= deadline:
        state.update(status="stopped", stop_reason="insufficient_time_for_new_job")
    else:
        state.update(status="running", stop_reason=None)
    return state


def preflight_plan(plan, *, store, read_result, now=None, rag_store=None, rag_read_result=None):
    """Read-only evidence preflight; callers hold both locks, no activation."""
    now = time.time() if now is None else now
    start, _ = day_bounds(plan["daily_policy"]["policy_date"])
    require(start <= now < timestamp(plan["config"]["deadline"]) - 1500, "daily_window_not_open")
    state, _, _ = _checked_state(plan, store=store, read_result=read_result, now=now,
        rag_store=rag_store, rag_read_result=rag_read_result)
    require(state["status"] != "stopped", "daily_checkpoint_stopped_requires_reconciliation")
    return {"mode": "preflight", "verified": True, "plan_sha256": digest(plan),
        "historical_jobs_verified": sum(bool(r.get("counts_slot")) for r in state["records"]),
        "existing_child": Path(plan["checkpoint"]).exists(), "generation_enabled": False,
        "llm_calls": 0, "queue_writes": 0, "service_db_writes": 0, "checkpoint_writes": 0}


def verify_empty_generation_broker(app, *, client=None):
    """Read every Redis priority list and in-flight receipt before enabling a Worker."""
    url = app.conf.broker_url
    parsed = urlsplit(url)
    options = app.conf.broker_transport_options or {}
    require(parsed.scheme == "redis" and parsed.hostname == "generation-redis" and parsed.path in {"/0", ""}
            and not options.get("global_keyprefix") and options.get("priority_steps", [0, 3, 6, 9]) == [0, 3, 6, 9]
            and options.get("sep", "\x06\x16") == "\x06\x16"
            and options.get("unacked_key", "unacked") == "unacked"
            and options.get("unacked_index_key", "unacked_index") == "unacked_index", "unexpected_generation_broker_layout")
    owned = client is None
    try:
        if owned:
            from redis import Redis
            client = Redis.from_url(url, socket_timeout=3, socket_connect_timeout=3)
        counts = {str(priority): client.llen("anime-generation-live" + ("\x06\x16" + str(priority) if priority else ""))
                  for priority in (0, 3, 6, 9)}
        report = {"priority_counts": counts, "ready": sum(counts.values()),
                  "unacked": client.hlen("unacked"), "unacked_index": client.zcard("unacked_index")}
    except Exception:
        raise StopRun("generation_broker_unavailable") from None
    finally:
        if owned and client is not None:
            client.close()
    require(not any(report[key] for key in ("ready", "unacked", "unacked_index")), "generation_broker_not_empty")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-checkpoint", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--policy-date", required=True, help="Explicit approved KST date YYYY-MM-DD")
    parser.add_argument("--daily-budget-usd", help="Legacy plan replay only: use together with --deadline")
    parser.add_argument("--deadline", help="Legacy plan replay only: use together with --daily-budget-usd; new plans use the KST day boundary")
    parser.add_argument("--delivery-root", type=Path, required=True, help="Existing dedicated envelope directory; no service DB writes")
    parser.add_argument("--registered-routes", action="store_true",
                        help="Plan balanced sequential generation across eligible registered source/work/type routes; does not activate collection")
    parser.add_argument("--rag-scope", type=Path, help="Explicit reviewed native RAG route scope; omitted means period-only")
    parser.add_argument("--expected-rag-scope-sha256")
    parser.add_argument("--rag-delivery-root", type=Path, help="Separate native inbox, never the period consumer inbox")
    parser.add_argument("--rag-runtime-config", type=Path, help="Reviewed isolated native runtime configuration; used only for preflight/apply")
    parser.add_argument("--expected-rag-runtime-sha256")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--preflight", action="store_true", help="Verify historical DB/sealed results under locks without publishing or creating a checkpoint")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--resume-failure-recovery", metavar="RECEIPT_SHA256",
                        help="Requires --apply: explicitly resume the latest reconciled failure without regenerating it")
    args = parser.parse_args(argv)
    try:
        require(not args.resume_failure_recovery or args.apply, "failure_resume_requires_apply")
        require(args.rag_scope is not None or args.rag_runtime_config is None, "rag_options_require_reviewed_scope")
        route_report = None
        if args.registered_routes or args.daily_budget_usd is None and args.deadline is None:
            from menu_worker.daily_routes import catalog_routes
            route_report = catalog_routes()
        plan = build_plan(source=args.source_checkpoint, source_sha256=args.source_sha256, checkpoint=args.checkpoint,
            policy_date=args.policy_date, daily_budget_usd=args.daily_budget_usd, deadline=args.deadline, delivery_root=args.delivery_root,
            routes=route_report["routes"] if route_report is not None else None,
            rag_scope=read_json(args.rag_scope) if args.rag_scope is not None else None,
            rag_scope_sha256=args.expected_rag_scope_sha256, rag_delivery_root=args.rag_delivery_root,
            rag_runtime_sha256=args.expected_rag_runtime_sha256)
        if not args.apply and not args.preflight:
            print(json.dumps({"mode": "plan", "plan": plan, "plan_sha256": digest(plan), "llm_calls": 0, "queue_writes": 0,
                              "service_db_writes": 0, **({"route_catalog": route_report} if route_report is not None else {})}))
            return 0
        require(args.expected_plan_sha256 == digest(plan), "explicit_reviewed_plan_digest_required")
        from menu_worker.discovery_agent import discover_articles, discover_daily_articles
        from menu_worker.job_contract import validate_job_request
        from menu_worker.job_store import JobStore
        from menu_worker.jobs import job_route
        from menu_worker.real_generation import read_sealed_staging_result
        from menu_worker.staging_export import export_staging_bundle
        store = JobStore()
        app, task, queue = job_route("anime_generate")
        root, bundles = Path(plan["config"]["artifact_root"]), Path(plan["config"]["bundle_root"])
        require(root.is_dir() and bundles.is_dir(), "existing_artifact_directories_required")
        def read_result(job):
            return read_sealed_staging_result(root, job)
        def validate(fields):
            _, canonical, key = validate_job_request(fields, "anime_generate")
            return canonical, key
        runtime = None
        history_kwargs = {}
        if plan.get("rag_scope"):
            from menu_worker.rag_daily_runtime import compose_runtime, verify_empty_rag_broker
            require(args.rag_runtime_config is not None, "explicit_reviewed_rag_runtime_required")
            runtime = compose_runtime(plan, app=app, runtime_path=args.rag_runtime_config,
                runtime_sha256=plan["rag_runtime_sha256"], enable=args.apply)
            history_kwargs = {"rag_store": runtime.store, "rag_read_result": runtime.read_result}
        with local_lock(args.source_checkpoint), local_lock(args.checkpoint), database_lock(store, plan["config"]["run_id"]) as check:
            if args.preflight:
                check()
                report = preflight_plan(plan, store=store, read_result=read_result, **history_kwargs)
                report["broker"] = verify_empty_rag_broker(app) if runtime else verify_empty_generation_broker(app)
                print(json.dumps(report))
                return 0
            state, reservations, _ = _checked_state(plan, store=store, read_result=read_result, **history_kwargs)
            refs = current_failure_references(state)
            if refs:
                require(bool(args.resume_failure_recovery), "explicit_failure_resume_required")
            if args.resume_failure_recovery:
                verify_empty_rag_broker(app) if runtime else verify_empty_generation_broker(app)
                state = resume_failure_recovery(state, args.resume_failure_recovery, store=store)
                write_json(args.checkpoint, state)
            _, reservations = activate_plan(plan, store=store, read_result=read_result, **history_kwargs)
            def guard():
                check()
                parent = source_checkpoint(args.source_checkpoint, args.source_sha256)
                validate_child(controller.state, parent, plan)
                from menu_worker.failure_recovery import validate_recovery_history
                validate_recovery_history(controller.state, store=store, artifact_root=root)
                require(read_json(claim_path(args.source_checkpoint))["plan_sha256"] == digest(plan), "successor_claim_changed")
                audit_existing_jobs(store, controller.state, rag_store=runtime.store if runtime else None)
            controller_class, mixed_kwargs = DailyController, {}
            if runtime:
                from menu_worker.rag_daily import MixedDailyController
                controller_class = MixedDailyController
                mixed_kwargs = {"rag_store": runtime.store, "rag_discover": runtime.discover,
                    "rag_publish": runtime.publish, "rag_read_result": runtime.read_result, "rag_export": runtime.export,
                    "rag_scope_sha256": plan["rag_scope_sha256"]}
            controller = controller_class(config=plan["config"], checkpoint=args.checkpoint, store=store,
                discover=discover_daily_articles if plan["daily_policy"]["schema_version"] in {2, 3} else discover_articles,
                validate=validate, publish=lambda job_id: publish_with_deadline(job_id,
                    app=app, task=task, queue=queue, deadline=plan["config"]["deadline"], job_timeout_seconds=1500),
                read_result=read_result, export=lambda job_id, output: export_staging_bundle(root, store, job_id, output),
                reservations=reservations, lock_check=guard, **mixed_kwargs)
            try:
                report = controller.run()
            except Exception as error:
                reason = str(error) if isinstance(error, StopRun) else "daily_error_inspect_checkpoint"
                # A broken DB connection has already released its advisory
                # lock. Do not persist stale in-memory state in that case.
                try:
                    check()
                except Exception:
                    pass
                else:
                    controller.state.update(status="stopped", stop_reason=reason)
                    controller.save()
                raise
            print(json.dumps({**report, "plan_sha256": digest(plan), "source_checkpoint_unchanged": True}))
            return 0 if report["status"] == "complete" else 2
    except Exception as error:
        reason = str(error) if isinstance(error, StopRun) else "daily_error_inspect_checkpoint"
        print(json.dumps({"error": reason, "category": type(error).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
