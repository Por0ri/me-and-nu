"""Audited, paused recovery of a proven zero-cost RAG preflight failure.

Only the checkpoint and immutable backups are written. The original failed
job/attempt, used daily slot and candidate identity remain unchanged.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

from menu_worker.bounded_generation import StopRun, database_lock, local_lock, read_json, write_json
from menu_worker.bounded_resume import digest, require
from menu_worker.failure_recovery import (
    PAUSED_REASON, _bytes, _database_view, _idle, _immutable, _raw_sha,
)

OPERATION = "reconcile_rag_preflight_zero_cost"


def _job(snapshot, job_id):
    jobs = [row for row in snapshot.get("content_rag_generation_job", []) if row["job_id"] == job_id]
    require(len(jobs) == 1, "rag_recovery_job_missing")
    return {**jobs[0], "attempts": [row for row in snapshot["content_rag_generation_attempt"]
                                   if row["job_id"] == job_id]}


def _target(state, snapshot, job_id, artifact_root):
    from menu_worker.rag_preflight_failure import complete_zero_preflight_record
    require(state.get("schema_version") == 1 and state.get("status") == "stopped"
            and state.get("stop_reason") == "rag_generation_requires_attention",
            "rag_recovery_checkpoint_not_stopped")
    require(state.get("daily_policy", {}).get("schema_version") in {3, 4}
            and state["daily_policy"].get("limit_mode") == "daily_counts"
            and state.get("config", {}).get("limit_mode") == "daily_counts"
            and state["config"].get("total_budget_usd") is None,
            "rag_recovery_requires_count_policy")
    require(str(artifact_root) == state["config"].get("artifact_root"), "recovery_artifact_root_changed")
    rows = state["records"]
    pending = [i for i, row in enumerate(rows) if row.get("state") not in {"complete", "duplicate"}]
    require(len(pending) == 1, "rag_recovery_requires_one_pending_record")
    index = pending[0]
    row = rows[index]
    require(row.get("job_id") == job_id and row.get("producer") == "ani_rag"
            and not row.get("failure_recovery_receipt") and not row.get("rag_preflight_failure"),
            "rag_recovery_record_not_eligible")
    job = _job(snapshot, job_id)
    complete_zero_preflight_record(state, row, job, artifact_root=artifact_root)
    return index, row, job


def _make_plan(checkpoint, expected_sha256, snapshot, artifact_root, job_id, reason):
    require(isinstance(reason, str) and 10 <= len(reason.strip()) <= 1000,
            "recovery_operator_reason_required")
    require(_raw_sha(checkpoint) == expected_sha256, "recovery_checkpoint_changed")
    state = read_json(checkpoint)
    _idle(snapshot)
    index, row, job = _target(state, snapshot, job_id, artifact_root)
    require(_raw_sha(checkpoint) == expected_sha256, "recovery_checkpoint_changed")
    return {"schema_version": 1, "operation": OPERATION,
        "checkpoint": str(checkpoint), "checkpoint_sha256": expected_sha256,
        "run_id": state["config"]["run_id"], "record_index": index, "original_record": row,
        "job": job, "database_snapshot_sha256": digest(snapshot), "artifact_root": str(artifact_root),
        "known_partial_estimated_usd": "0.00000000", "unknown_total": False,
        "operator_reason": reason.strip(), "generation_enabled": False, "resume_authorized": False}


def plan_recovery(*, checkpoint, expected_sha256, store, artifact_root, job_id, reason):
    with _database_view(store) as snapshot:
        return _make_plan(Path(checkpoint), expected_sha256, snapshot, Path(artifact_root), job_id, reason)


def _paths(plan, plan_sha):
    checkpoint = Path(plan["checkpoint"])
    root = checkpoint.with_name(checkpoint.name + ".rag-preflight-recovery")
    require(checkpoint.is_absolute() and checkpoint.parent.resolve() == checkpoint.parent
            and root.resolve() == root and not root.is_symlink(), "unsafe_recovery_directory")
    return root, root / (plan_sha + ".checkpoint.json"), root / (plan_sha + ".database.json"), root / (plan_sha + ".receipt.json")


def _reference(receipt, path):
    return {"receipt_path": str(path), "receipt_sha256": digest(receipt)}


def _recovered_row(before, receipt, reference):
    from menu_worker.rag_preflight_failure import complete_zero_preflight_record
    return {**complete_zero_preflight_record(before, receipt["original_record"], receipt["job"],
        artifact_root=Path(receipt["artifact_root"])), "failure_recovery_receipt": reference}


def _after(before, receipt, reference):
    state = copy.deepcopy(before)
    state["records"][receipt["record_index"]] = _recovered_row(before, receipt, reference)
    state.setdefault("failure_recovery_audit", []).append(reference)
    state.update(status="stopped", stop_reason=PAUSED_REASON)
    return state


def apply_recovery(plan, *, expected_plan_sha256, store):
    require(digest(plan) == expected_plan_sha256 and plan.get("operation") == OPERATION,
            "explicit_reviewed_recovery_digest_required")
    checkpoint = Path(plan["checkpoint"])
    root, backup, database_backup, receipt_path = _paths(plan, expected_plan_sha256)
    with local_lock(checkpoint), database_lock(store, plan["run_id"]) as check, store.generation_history_guard() as history_check:
        require(callable(history_check), "recovery_generation_history_busy")
        with _database_view(store, lock=True) as snapshot:
            check()
            history_check()
            _idle(snapshot)
            require(digest(snapshot) == plan["database_snapshot_sha256"], "recovery_database_changed")
            if _raw_sha(checkpoint) != plan["checkpoint_sha256"]:
                receipt = read_json(receipt_path)
                reference = _reference(receipt, receipt_path)
                require(receipt.get("plan_sha256") == expected_plan_sha256, "recovery_receipt_plan_changed")
                expected_after = _after(read_json(backup), receipt, reference)
                require(checkpoint.read_bytes() == _bytes(expected_after), "recovery_checkpoint_changed")
                validate_recovery_record(expected_after["records"][plan["record_index"]], store=store)
                return receipt
            actual = _make_plan(checkpoint, plan["checkpoint_sha256"], snapshot, Path(plan["artifact_root"]),
                                plan["job"]["job_id"], plan["operator_reason"])
            require(actual == plan, "recovery_plan_evidence_changed")
            raw_before = checkpoint.read_bytes()
            before = read_json(checkpoint)
            receipt = {**plan, "plan_sha256": expected_plan_sha256,
                "checkpoint_backup": str(backup), "database_backup": str(database_backup),
                "database_backup_sha256": hashlib.sha256(_bytes(snapshot)).hexdigest()}
            reference = _reference(receipt, receipt_path)
            after = _after(before, receipt, reference)
            root.mkdir(mode=0o700, exist_ok=True)
            require(os.name == "nt" or root.stat().st_mode & 0o077 == 0,
                    "unsafe_recovery_directory_permissions")
            _immutable(backup, raw_before)
            _immutable(database_backup, _bytes(snapshot))
            _immutable(receipt_path, _bytes(receipt))
            check()
            history_check()
            require(_raw_sha(checkpoint) == plan["checkpoint_sha256"], "recovery_checkpoint_changed")
            _target(before, snapshot, plan["job"]["job_id"], Path(plan["artifact_root"]))
            write_json(checkpoint, after)
            return receipt


def validate_recovery_record(record, *, store=None, artifact_root=None):
    reference = record.get("failure_recovery_receipt")
    require(isinstance(reference, dict) and set(reference) == {"receipt_path", "receipt_sha256"},
            "missing_failure_recovery_receipt")
    path = Path(reference["receipt_path"])
    receipt = read_json(path)
    require(digest(receipt) == reference["receipt_sha256"] and receipt.get("schema_version") == 1
            and receipt.get("operation") == OPERATION and receipt.get("unknown_total") is False
            and receipt.get("generation_enabled") is False and receipt.get("resume_authorized") is False,
            "failure_recovery_receipt_changed")
    plan = {key: value for key, value in receipt.items()
            if key not in {"plan_sha256", "checkpoint_backup", "database_backup", "database_backup_sha256"}}
    require(digest(plan) == receipt["plan_sha256"], "failure_recovery_plan_changed")
    _, backup, database_backup, expected_path = _paths(plan, receipt["plan_sha256"])
    require(path == expected_path and receipt["checkpoint_backup"] == str(backup)
            and receipt["database_backup"] == str(database_backup)
            and _raw_sha(backup) == receipt["checkpoint_sha256"]
            and _raw_sha(database_backup) == receipt["database_backup_sha256"], "failure_recovery_backup_changed")
    snapshot = read_json(database_backup)
    require(digest(snapshot) == receipt["database_snapshot_sha256"], "failure_recovery_database_backup_changed")
    before = read_json(backup)
    root = Path(receipt["artifact_root"])
    require(artifact_root is None or Path(artifact_root) == root, "failure_recovery_artifact_root_changed")
    index, original, job = _target(before, snapshot, receipt["job"]["job_id"], root)
    require(index == receipt["record_index"] and original == receipt["original_record"] and job == receipt["job"]
            and record == _recovered_row(before, receipt, reference), "failure_recovery_record_changed")
    if store is not None:
        from menu_worker.rag_store import RagStore
        rag_store = RagStore.__new__(RagStore)
        rag_store.jobs = getattr(store, "jobs", store)
        require(rag_store.get(job["job_id"]) == job, "failure_recovery_job_history_changed")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_args = commands.add_parser("plan")
    for name in ("checkpoint", "expected-sha256", "artifact-root", "job-id", "reason"):
        plan_args.add_argument("--" + name, required=True)
    apply_args = commands.add_parser("apply")
    apply_args.add_argument("--plan-file", type=Path, required=True)
    apply_args.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        from menu_worker.job_store import JobStore
        store = JobStore()
        if args.command == "plan":
            plan = plan_recovery(checkpoint=args.checkpoint, expected_sha256=args.expected_sha256,
                store=store, artifact_root=args.artifact_root, job_id=args.job_id, reason=args.reason)
            print(json.dumps({"plan": plan, "plan_sha256": digest(plan), "generation_enabled": False}))
        else:
            receipt = apply_recovery(read_json(args.plan_file), expected_plan_sha256=args.expected_plan_sha256, store=store)
            print(json.dumps({"status": PAUSED_REASON, "receipt_sha256": digest(receipt),
                "unknown_total": False, "generation_enabled": False, "database_writes": 0, "llm_calls": 0}))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error) if isinstance(error, StopRun) else "rag_preflight_recovery_failed",
                          "generation_enabled": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
