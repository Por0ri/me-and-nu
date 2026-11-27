"""Strict, read-only evidence for a failed RAG attempt that never started.

Completing a reservation does not change its failed DB job/attempt, refund its
daily slot, export a result, or grant permission to resume a stopped schedule.
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import UUID

from menu_worker.bounded_generation import StopRun, money
from menu_worker.bounded_resume import digest, require
from menu_worker.rag_contract import RagRequest, validate_admission_receipt


MARKER = "rag_preflight_failure"
ERROR = "rag_preflight_failure_not_recoverable"


def _timestamp(value):
    require(isinstance(value, str), ERROR)
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, ERROR)
    return parsed


def _evidence(state, record, job, artifact_root):
    require(isinstance(state, dict) and isinstance(record, dict) and isinstance(job, dict), ERROR)
    require(record.get("producer") == "ani_rag" and record.get("counts_slot") is True
            and record.get("state") in {"reserved", "complete"}
            and record.get("accounting_method") not in {"reserve", "unknown"}
            and not record.get("cost_unknown")
            and not record.get("rag_cost_incomplete") and not record.get("rag_result_verification_failed")
            and record.get("rag_delivery") is None and record.get("bundle_path") is None, ERROR)
    # A zero-cost DB failure must never erase conflicting checkpoint evidence.
    require(all(record.get(key) is None for key in (
        "generation_started_at", "outcome", "producer_status", "result_sha256", "artifact_path", "accounting_reservation",
        "delivery_envelope_path", "delivery_envelope_sha256"))
        and record.get("execution_status") in {None, "failed"}
        and ("rag_usage_records" not in record or record["rag_usage_records"] == []), ERROR)
    for key in ("estimated_cost_usd", "rag_known_partial_usd", "reserved_cost_usd", "measured_cost"):
        require(key not in record or record[key] is not None and money(record[key]) == 0, ERROR)
    request = RagRequest.parse(record["request"]["rag_request"])
    receipt = validate_admission_receipt(record["rag_admission"], request)
    job_id, attempt_id = job["job_id"], job["generation_attempt_id"]
    require(isinstance(job_id, str) and str(UUID(job_id)) == job_id
            and isinstance(attempt_id, str) and str(UUID(attempt_id)) == attempt_id, ERROR)
    require(record.get("job_id") == job_id
            and record.get("generation_attempt_id") in {None, attempt_id}
            and record.get("rag_input_sha256") == request.input_sha256
            and record.get("canonical_url") == "urn:menu-rag:" + request.identity_key
            and record["request"].get("canonical_url") == record["canonical_url"]
            and record["request"].get("producer") == "ani_rag"
            and record["request"].get("source_id") == "rag_" + request.namespace
            and record["request"].get("content_type") == request.content_type
            and record["request"].get("menu_work_id") == request.menu_work_id
            and record["request"].get("idempotency_key") == state["config"]["run_id"] + ":rag:" + request.identity_key,
            ERROR)
    require(job.get("request") == request.as_dict()
            and job.get("namespace") == request.namespace and job.get("work_id") == request.work_id
            and job.get("identity_key") == request.identity_key
            and job.get("input_sha256") == request.input_sha256
            and job.get("admission_receipt") == receipt
            and job.get("admission_sha256") == digest(receipt)
            and job.get("reservation_id") == receipt["reservation_id"], ERROR)
    rows = state["records"]
    indices = [i for i, row in enumerate(rows) if row.get("job_id") == job_id]
    require(len(indices) == 1 and rows[indices[0]] == record
            and record["canonical_url"] in state["seen"], ERROR)
    index = indices[0]
    policies = state["daily_policies"]
    require(isinstance(policies, list) and policies and state.get("daily_policy") == policies[-1], ERROR)
    owners = [(i, policy) for i, policy in enumerate(policies)
              if policy.get("policy_date") == record.get("policy_date")]
    require(len(owners) == 1, ERROR)
    number, policy = owners[0]
    start = policy["start_record_index"]
    end = policies[number + 1]["start_record_index"] if number + 1 < len(policies) else len(rows)
    scope = state["rag_scope"]
    require(type(start) is int and type(end) is int and 0 <= start <= index < end
            and policy.get("limit_mode") == "daily_counts"
            and policy.get("max_attempts") == 40 and policy.get("success_target") == 20
            and record.get("day_policy_sha256") == digest(policy)
            and digest(scope) == state.get("rag_scope_sha256")
            and receipt["policy_date"] == policy["policy_date"]
            and receipt["policy_sha256"] == digest({"daily": policy, "rag": scope})
            and receipt["attempt_ordinal"] == sum(row.get("counts_slot") is True for row in rows[start:index + 1]),
            ERROR)
    require(any(request.namespace == route.get("namespace")
                and request.menu_work_id == route.get("menu_work_id")
                and request.content_type == route.get("content_type")
                and set(request.allowed_source_ids) <= set(route.get("allowed_source_ids", []))
                for route in scope["routes"]), ERROR)
    attempts = job.get("attempts")
    require(isinstance(attempts, list) and len(attempts) == 1 and isinstance(attempts[0], dict), ERROR)
    attempt = attempts[0]
    require(attempt.get("job_id") == job_id and attempt.get("attempt_id") == attempt_id, ERROR)
    for row in (job, attempt):
        require(row.get("execution_status") == "failed" and row.get("error_code") == "rag_preflight_failed"
                and row.get("cost_complete") is True and row.get("usage_records") == [], ERROR)
        require(all(key in row and row[key] is None for key in (
            "generation_started_at", "outcome", "producer_status", "result_sha256", "artifact_path")), ERROR)
    require(all(key in job and job[key] is None for key in ("claim_token", "lease_expires_at")), ERROR)
    require(_timestamp(job["created_at"]) <= _timestamp(attempt["claimed_at"])
            <= _timestamp(attempt["finished_at"]) <= _timestamp(job["finished_at"]), ERROR)
    root = Path(artifact_root)
    require(root.is_absolute() and root.resolve() == root and root.is_dir(), ERROR)
    job_root = root / "rag" / "jobs" / job_id
    require(job_root.resolve() == job_root and not job_root.exists() and not job_root.is_symlink(), ERROR)
    return {"schema_version": 1, "kind": "preflight_failed_zero_cost", "job_sha256": digest(job)}


def _verified_evidence(state, record, job, artifact_root):
    try:
        return _evidence(state, record, job, artifact_root)
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        raise StopRun(ERROR) from None


def complete_zero_preflight_record(state, record, job, *, artifact_root):
    """Return a completed copy; never modifies the checkpoint, files, or ledger."""
    marker = _verified_evidence(state, record, job, artifact_root)
    require(MARKER not in record or record[MARKER] == marker, ERROR)
    completed = deepcopy(record)
    completed.update(state="complete", execution_status="failed", outcome=None, producer_status=None,
                     result_sha256=None, generation_attempt_id=job["generation_attempt_id"],
                     estimated_cost_usd="0.00000000", rag_usage_records=[], **{MARKER: marker})
    return completed


def verify_zero_preflight_completion(state, record, job, *, artifact_root):
    """Recheck the unchanged failed DB evidence before restart or day rollover."""
    marker = _verified_evidence(state, record, job, artifact_root)
    require(record.get("state") == "complete" and record.get(MARKER) == marker
            and record.get("execution_status") == "failed"
            and record.get("generation_attempt_id") == job["generation_attempt_id"]
            and all(key in record and record[key] is None for key in (
                "outcome", "producer_status", "result_sha256"))
            and record.get("rag_usage_records") == []
            and record.get("estimated_cost_usd") == "0.00000000", ERROR)
