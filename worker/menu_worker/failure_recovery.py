"""Reconcile an exhausted producer attempt without retrying it or resuming a run.

The default plan reads only. Apply needs its exact reviewed digest, takes the
controller/history locks, and writes immutable evidence backups plus a paused
checkpoint. No job mutation, broker publication, model or service DB is used.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from menu_worker.bounded_generation import StopRun, database_lock, local_lock, money, read_json, result_cost, write_json
from menu_worker.bounded_resume import digest, require

PAUSED_REASON = "failure_reconciled_paused"
FAILED_OPERATION = "reconcile_failed_generation_unknown_cost"
HELD_OPERATION = "reconcile_held_generation_unknown_cost"
receipt_digest = digest
TABLES = ("menu_worker_schema", "content_generation_job", "content_generation_attempt",
          "content_generation_review", "content_staging_selection")


def _raw_sha(path):
    require(path.is_absolute() and path.resolve() == path and path.is_file(), "unsafe_recovery_file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(connection):
    from menu_worker.job_store import _plain
    names = {row["tablename"] for row in connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()}
    require(names == set(TABLES), "unexpected_recovery_database_schema")
    return {table: sorted(_plain(connection.execute("SELECT * FROM public." + table).fetchall()), key=digest)
            for table in TABLES}


@contextmanager
def _database_view(store, *, lock=False):
    with store._connect() as connection:
        connection.commit()
        if lock:
            # No row is written. Block all concurrent ledger mutations until the
            # filesystem CAS commits; advisory locks alone do not fence submit().
            connection.execute("LOCK TABLE " + ",".join("public." + name for name in TABLES)
                               + " IN SHARE MODE NOWAIT")
        else:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield _snapshot(connection)


def _job(snapshot, job_id):
    jobs = [row for row in snapshot["content_generation_job"] if row["job_id"] == job_id]
    require(len(jobs) == 1, "recovery_job_missing")
    return {**jobs[0], "attempts": sorted(
        [row for row in snapshot["content_generation_attempt"] if row["job_id"] == job_id],
        key=lambda row: row["attempt_no"])}


def _idle(snapshot):
    require(not any(row["execution_status"] in {"queued", "running"}
                    for name in ("content_generation_job", "content_generation_attempt")
                    for row in snapshot[name]), "recovery_database_not_idle")


def _inventory(root, job):
    from menu_worker.job_artifacts import artifact_directory
    directory = artifact_directory(root, job).parents[1]
    files, total = {}, 0
    for path in sorted(directory.rglob("*")):
        require(len(files) < 4096 and not path.is_symlink() and path.resolve() == path,
                "unsafe_recovery_artifact")
        before = path.stat()
        name = path.relative_to(directory).as_posix()
        if stat.S_ISDIR(before.st_mode):
            files[name] = {"type": "directory"}
            continue
        require(stat.S_ISREG(before.st_mode) and before.st_size <= 256 * 1024 * 1024,
                "unsafe_recovery_artifact")
        total += before.st_size
        require(total <= 1024 * 1024 * 1024, "recovery_artifacts_too_large")
        fingerprint = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                fingerprint.update(block)
        after = path.stat()
        require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                "recovery_artifacts_changed")
        files[name] = {"type": "file", "bytes": before.st_size, "sha256": fingerprint.hexdigest()}
    return files


def _held_unknown_cost(result):
    """Validate partial evidence before allowing an incomplete flag to short-circuit it."""
    records = result.get("usage_records", [])
    summary, accounting = result.get("usage_summary", {}), result.get("cost_accounting", {})
    require(isinstance(records, list) and isinstance(summary, dict) and isinstance(accounting, dict),
            "invalid_cost_evidence")
    token_fields = ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "total_tokens")
    amounts = []
    for row in records:
        require(isinstance(row, dict), "invalid_cost_evidence")
        for field in token_fields:
            if field in row:
                require(type(row[field]) is int and row[field] >= 0, "invalid_cost_evidence")
        if "usage_complete" in row:
            require(type(row["usage_complete"]) is bool, "invalid_cost_evidence")
        if "estimated_cost_usd" in row:
            amount = money(row["estimated_cost_usd"])
            require(amount != 0 or not any(row.get(field, 0) > 0 for field in token_fields),
                    "zero_cost_with_paid_tokens")
            amounts.append(amount)
    known = sum(amounts, Decimal(0))
    for source in (summary, accounting):
        for field in ("complete", "cost_complete"):
            if field in source:
                require(type(source[field]) is bool, "invalid_cost_evidence")
        for field in ("estimated_cost_usd", "total_estimated_cost_usd", "known_estimated_cost_usd"):
            if field in source:
                require(abs(money(source[field]) - known) <= Decimal("0.00000002"),
                        "inconsistent_cost_evidence")
    require(not any(source.get(field) is True for source in (summary, accounting)
                    for field in ("complete", "cost_complete")), "recovery_held_cost_not_unknown")
    for source in (result, summary):
        if "llm_calls" in source:
            require(type(source["llm_calls"]) is int and source["llm_calls"] >= 0, "invalid_cost_evidence")
            if "usage_records" in result:
                require(source["llm_calls"] == len(records), "inconsistent_cost_evidence")
    for field in token_fields:
        if field in summary:
            require(type(summary[field]) is int and summary[field] >= 0, "invalid_cost_evidence")
            if records and all(field in row for row in records):
                require(summary[field] == sum(row[field] for row in records), "inconsistent_cost_evidence")
    if len(amounts) == len(records) and known == 0:
        require(not any(summary.get(field, 0) > 0 for field in token_fields), "zero_cost_with_paid_tokens")
    try:
        result_cost(result)
    except StopRun as error:
        require(str(error) in {"missing_cost_evidence", "incomplete_cost_evidence"},
                "recovery_held_cost_not_unknown")
    else:
        raise StopRun("recovery_held_cost_not_unknown")
    return str(known) if amounts else None


def _held_evidence(root, job):
    from menu_worker.job_artifacts import artifact_directory
    from menu_worker.real_generation import RealGenerationError, read_sealed_staging_result
    inventory = _inventory(root, job)
    exit_record = read_json(artifact_directory(root, job) / "exit.json")
    require(isinstance(exit_record, dict) and type(exit_record.get("returncode")) is int
            and exit_record["returncode"] == 0 and exit_record.get("supervisor_stop") is None,
            "recovery_exit_not_confirmed")
    try:
        # seal_missing=False: never repair, seal, export or otherwise modify the producer output.
        result = read_sealed_staging_result(root, job)
    except RealGenerationError:
        raise StopRun("recovery_held_artifact_invalid") from None
    require(result.get("status") == "expiry_unresolved_held", "recovery_held_result_not_eligible")
    known = _held_unknown_cost(result)
    require(inventory == _inventory(root, job), "recovery_artifacts_changed")
    return inventory, known


def _operation(job):
    return HELD_OPERATION if job.get("execution_status") == "succeeded" else FAILED_OPERATION


def _evidence(root, job):
    if _operation(job) == HELD_OPERATION:
        return _held_evidence(root, job)
    from menu_worker.job_artifacts import artifact_directory
    from menu_worker.real_generation import _identity, _request
    directory = artifact_directory(root, job)
    inventory = _inventory(root, job)
    launch = read_json(directory / "launch.json")
    identity = _identity(job)
    if "cover_history_sha256" in launch:
        history_sha = launch["cover_history_sha256"]
        require(re.fullmatch(r"[0-9a-f]{64}", str(history_sha)) is not None
                and _raw_sha(directory / "cover-history.json") == history_sha, "recovery_cover_history_changed")
        identity["cover_history_sha256"] = history_sha
    require(launch == identity, "recovery_launch_mismatch")
    exit_record = read_json(directory / "exit.json")
    require(isinstance(exit_record, dict) and type(exit_record.get("returncode")) is int
            and exit_record["returncode"] != 0 and -255 <= exit_record["returncode"] <= 255,
            "recovery_exit_not_confirmed")
    require(not (directory / "manifest.json").exists(), "recovery_sealed_result_requires_review")
    known = None
    result_path = directory / "result.json"
    if result_path.exists():
        result = read_json(result_path)
        require(isinstance(result, dict) and result.get("status") == "generation_failed"
                and result.get("expected_work_request") == _request(job)
                and isinstance(result.get("request_id"), str) and result["request_id"]
                and isinstance(result.get("failure"), dict)
                and result["failure"].get("stage") == "content_generation"
                and isinstance(result.get("cost_accounting"), dict)
                and result["cost_accounting"].get("complete") is False
                and isinstance(result.get("usage_records"), list), "recovery_result_not_eligible")
        amounts = []
        for row in result["usage_records"]:
            require(isinstance(row, dict), "recovery_partial_usage_invalid")
            if "estimated_cost_usd" in row:
                amounts.append(money(row["estimated_cost_usd"]))
        if amounts:
            known = str(sum(amounts, Decimal(0)))
    require(inventory == _inventory(root, job), "recovery_artifacts_changed")
    return inventory, known


def _held_unexported(snapshot, job):
    job_id, attempt_id = job["job_id"], job["generation_attempt_id"]
    require(not any(row.get("job_id") == job_id or row.get("generation_attempt_id") == attempt_id
                    for row in snapshot["content_generation_review"])
            and not any(row.get("selected_job_id") == job_id or row.get("article_job_id") == job_id
                        or row.get("generation_attempt_id") == attempt_id
                        for row in snapshot["content_staging_selection"]), "recovery_held_result_already_reviewed")


def _validate_target(state, job_id, snapshot):
    from menu_worker.job_contract import validate_job_request
    job = _job(snapshot, job_id)
    held = _operation(job) == HELD_OPERATION
    stop_reasons = ({"missing_cost_evidence", "incomplete_cost_evidence"} if held else
                    {"job_needs_attention", "failed_generation_cost_unknown"})
    require(state.get("schema_version") == 1 and state.get("status") == "stopped"
            and state.get("stop_reason") in stop_reasons,
            "recovery_checkpoint_not_stopped")
    require(state.get("daily_policy", {}).get("schema_version") == 3
            and state.get("daily_policy", {}).get("limit_mode") == "daily_counts"
            and state.get("config", {}).get("limit_mode") == "daily_counts"
            and state["config"].get("total_budget_usd") is None, "recovery_requires_count_policy")
    rows = state.get("records", [])
    pending = [index for index, row in enumerate(rows) if row.get("state") not in {"complete", "duplicate"}]
    require(len(pending) == 1 and rows[pending[0]].get("job_id") == job_id,
            "recovery_pending_identity_mismatch")
    index, row = pending[0], rows[pending[0]]
    require(row.get("counts_slot") is True and row.get("canonical_url") in state.get("seen", [])
            and not row.get("accounting_method") and not row.get("bundle_path")
            and not row.get("delivery_envelope_path") and row.get("estimated_cost_usd") is None
            and row.get("outcome") == ("held" if held else None), "recovery_record_not_eligible")
    require(job.get("job_kind") == "anime_generate"
            and job.get("execution_status") == ("succeeded" if held else "needs_attention")
            and job.get("outcome") == ("held" if held else None) and job.get("generation_started_at")
            and job.get("current_attempt_id") == job.get("generation_attempt_id")
            and job.get("finished_at") and job.get("claim_token") is None
            and job.get("lease_expires_at") is None, "recovery_job_not_eligible")
    if held:
        require(job.get("producer_status") == "expiry_unresolved_held" and job.get("artifact_path")
                and re.fullmatch(r"[0-9a-f]{64}", str(job.get("result_sha256"))) is not None,
                "recovery_held_job_not_eligible")
        require(row.get("execution_status") == "succeeded" and row.get("producer_status") == job["producer_status"]
                and row.get("generation_attempt_id", job["generation_attempt_id"]) == job["generation_attempt_id"]
                and row.get("result_sha256", job["result_sha256"]) == job["result_sha256"]
                and not row.get("selected_job_id") and not row.get("review_id"), "recovery_held_record_not_eligible")
        _held_unexported(snapshot, job)
    else:
        require(job.get("artifact_path") is None and job.get("result_sha256") is None,
                "recovery_job_not_eligible")
    attempts = [a for a in job["attempts"] if a["attempt_id"] == job["generation_attempt_id"]]
    require(len(attempts) == 1 and attempts[0].get("mode") == "generation"
            and attempts[0].get("execution_status") == ("succeeded" if held else "needs_attention")
            and attempts[0].get("generation_started_at") and attempts[0].get("finished_at")
            and attempts[0].get("outcome") == ("held" if held else None), "recovery_attempt_not_eligible")
    if held:
        require(all(attempts[0].get(key) == job.get(key)
                    for key in ("artifact_path", "result_sha256", "producer_status")),
                "recovery_held_attempt_not_eligible")
    request, canonical, input_sha = validate_job_request(row["request"], "anime_generate")
    require(canonical == row["canonical_url"] == job["canonical_url"] and input_sha == job["input_sha256"]
            and request.idempotency_key == job["idempotency_key"]
            and all(getattr(request, key) == job[key] for key in ("source_id", "content_type", "menu_work_id")),
            "recovery_input_mismatch")
    return index, row, job


def _make_plan(checkpoint, expected_sha256, snapshot, artifact_root, job_id, reason):
    require(isinstance(reason, str) and 10 <= len(reason.strip()) <= 1000,
            "recovery_operator_reason_required")
    state = read_json(checkpoint)
    require(_raw_sha(checkpoint) == expected_sha256, "recovery_checkpoint_changed")
    _idle(snapshot)
    index, row, job = _validate_target(state, job_id, snapshot)
    require(str(artifact_root) == state["config"].get("artifact_root"), "recovery_artifact_root_changed")
    inventory, known = _evidence(artifact_root, job)
    require(_raw_sha(checkpoint) == expected_sha256, "recovery_checkpoint_changed")
    return {"schema_version": 1, "operation": _operation(job),
        "checkpoint": str(checkpoint), "checkpoint_sha256": expected_sha256,
        "run_id": state["config"]["run_id"], "record_index": index, "original_record": row,
        "job": job, "database_snapshot_sha256": digest(snapshot), "artifact_root": str(artifact_root),
        "artifacts": inventory, "known_partial_estimated_usd": known, "unknown_total": True,
        "operator_reason": reason.strip(), "generation_enabled": False, "resume_authorized": False}


def plan_recovery(*, checkpoint, expected_sha256, store, artifact_root, job_id, reason):
    """Read-only planning: no lock file, checkpoint, job, queue or artifact write."""
    with _database_view(store) as snapshot:
        return _make_plan(Path(checkpoint), expected_sha256, snapshot, Path(artifact_root), job_id, reason)


def _paths(plan, plan_sha):
    checkpoint = Path(plan["checkpoint"])
    root = checkpoint.with_name(checkpoint.name + ".failure-recovery")
    require(checkpoint.is_absolute() and checkpoint.parent.resolve() == checkpoint.parent
            and root.resolve() == root and not root.is_symlink(), "unsafe_recovery_directory")
    return root, root / (plan_sha + ".checkpoint.json"), root / (plan_sha + ".database.json"), root / (plan_sha + ".receipt.json")


def _bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False).encode()


def _immutable(path, data):
    require(path.resolve() == path and not path.is_symlink(), "unsafe_recovery_file")
    if path.exists():
        require(path.read_bytes() == data, "recovery_immutable_file_changed")
        return
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            require(path.read_bytes() == data, "recovery_immutable_file_changed")
    finally:
        temporary.unlink(missing_ok=True)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _reference(receipt, path):
    return {"receipt_path": str(path), "receipt_sha256": digest(receipt)}


def _recovered_row(receipt, reference):
    held = receipt["operation"] == HELD_OPERATION
    result = {**receipt["original_record"], "state": "complete",
        "execution_status": "succeeded" if held else "needs_attention",
        "outcome": "held" if held else None, "generation_attempt_id": receipt["job"]["generation_attempt_id"],
        "accounting_method": "unknown", "estimated_cost_usd": None, "measured_cost": None,
        "cost_unknown": True, "failure_recovery_receipt": reference}
    if held:
        result["result_sha256"] = receipt["job"]["result_sha256"]
        result["producer_status"] = receipt["job"]["producer_status"]
    return result


def _after(before, receipt, reference):
    state = copy.deepcopy(before)
    state["records"][receipt["record_index"]] = _recovered_row(receipt, reference)
    state.setdefault("failure_recovery_audit", []).append(reference)
    state.update(status="stopped", stop_reason=PAUSED_REASON)
    return state


def apply_recovery(plan, *, expected_plan_sha256, store):
    """Explicit plan CAS; only the checkpoint/immutable recovery evidence is written."""
    require(digest(plan) == expected_plan_sha256, "explicit_reviewed_recovery_digest_required")
    checkpoint = Path(plan["checkpoint"])
    root, backup, database_backup, receipt_path = _paths(plan, expected_plan_sha256)
    with local_lock(checkpoint), database_lock(store, plan["run_id"]) as check, store.generation_history_guard() as history_check:
        with _database_view(store, lock=True) as snapshot:
            check()
            history_check()
            _idle(snapshot)
            require(digest(snapshot) == plan["database_snapshot_sha256"], "recovery_database_changed")
            if _raw_sha(checkpoint) != plan["checkpoint_sha256"]:
                receipt = read_json(receipt_path)
                reference = _reference(receipt, receipt_path)
                require(receipt.get("plan_sha256") == expected_plan_sha256,
                        "recovery_receipt_plan_changed")
                before = read_json(backup)
                expected_after = _after(before, receipt, reference)
                require(checkpoint.read_bytes() == _bytes(expected_after), "recovery_checkpoint_changed")
                validate_recovery_record(read_json(checkpoint)["records"][plan["record_index"]], store=store)
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
            require(_raw_sha(checkpoint) == plan["checkpoint_sha256"]
                    and _inventory(Path(plan["artifact_root"]), plan["job"]) == plan["artifacts"],
                    "recovery_evidence_changed_before_apply")
            write_json(checkpoint, after)
            return receipt


def validate_recovery_record(record, *, store=None, artifact_root=None):
    reference = record.get("failure_recovery_receipt")
    require(isinstance(reference, dict) and set(reference) == {"receipt_path", "receipt_sha256"},
            "missing_failure_recovery_receipt")
    path = Path(reference["receipt_path"])
    receipt = read_json(path)
    require(digest(receipt) == reference["receipt_sha256"] and receipt.get("schema_version") == 1
            and receipt.get("operation") in {FAILED_OPERATION, HELD_OPERATION}
            and receipt.get("unknown_total") is True and receipt.get("generation_enabled") is False
            and receipt.get("resume_authorized") is False, "failure_recovery_receipt_changed")
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
    index, original, job = _validate_target(before, receipt["job"]["job_id"], snapshot)
    require(receipt["operation"] == _operation(job)
            and index == receipt["record_index"] and original == receipt["original_record"] and job == receipt["job"]
            and record == _recovered_row(receipt, reference), "failure_recovery_record_changed")
    root = Path(receipt["artifact_root"])
    require(artifact_root is None or Path(artifact_root) == root, "failure_recovery_artifact_root_changed")
    inventory, known = _evidence(root, job)
    require(inventory == receipt["artifacts"] and known == receipt["known_partial_estimated_usd"],
            "failure_recovery_artifacts_changed")
    if store is not None:
        require(store.get(job["job_id"]) == job, "failure_recovery_job_history_changed")
        if receipt["operation"] == HELD_OPERATION:
            with _database_view(store) as current:
                require(_job(current, job["job_id"]) == job, "failure_recovery_job_history_changed")
                _held_unexported(current, job)
    return receipt


def validate_recovery_history(state, *, store=None, artifact_root=None):
    rows = [row for row in state["records"] if row.get("accounting_method") == "unknown"
            or row.get("failure_recovery_receipt") or row.get("cost_unknown")]
    references = [row.get("failure_recovery_receipt") for row in rows]
    require(state.get("failure_recovery_audit", []) == references, "failure_recovery_audit_changed")
    if rows:
        require(state.get("daily_policy", {}).get("schema_version") == 3
                and state.get("daily_policy", {}).get("limit_mode") == "daily_counts"
                and state.get("config", {}).get("limit_mode") == "daily_counts"
                and state["config"].get("total_budget_usd") is None, "recovery_requires_count_policy")
    receipts = [validate_recovery_record(row, store=store, artifact_root=artifact_root) for row in rows]
    for row, receipt in zip(rows, receipts):
        require(type(receipt["record_index"]) is int
                and 0 <= receipt["record_index"] < len(state["records"])
                and state["records"][receipt["record_index"]] == row,
                "failure_recovery_record_position_changed")
    return receipts


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
                              "unknown_total": True, "generation_enabled": False}))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error) if isinstance(error, StopRun) else "failure_recovery_failed",
                          "generation_enabled": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
