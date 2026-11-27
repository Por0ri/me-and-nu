"""Pure daily limits and append-only, same-day limit revisions.

This module uses only the standard library so a read-only observer can apply
the same validation. Revisions never replace a sealed policy or its records.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json


REVISION_FIELDS = frozenset({"schema_version", "purpose", "policy_date", "day_policy_sha256",
    "effective_record_index", "prior_records_sha256", "prior_seen_count", "prior_seen_sha256",
    "prior_daily_attempts", "prior_daily_successes", "max_attempts", "success_target", "approved_at", "reason"})
RECORD_REVISION = "daily_limit_revision_sha256"


class DailyLimitError(ValueError):
    pass


def _require(condition, code="daily_limit_revision_changed"):
    if not condition:
        raise DailyLimitError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _successes(rows):
    return {(row.get("request", {}).get("source_id"), row["canonical_url"]) for row in rows
        if row.get("counts_slot") and row.get("state") == "complete"
        and row.get("execution_status") == "succeeded" and row.get("outcome") == "ready_for_staging"}


def policy_attempt_limit(policy):
    version = policy.get("schema_version")
    _require(type(version) is int and version in {1, 2, 3, 4}, "invalid_daily_policy_schema")
    expected = None if version == 4 else 40
    _require("max_attempts" in policy and policy["max_attempts"] == expected
        and (expected is None or type(policy["max_attempts"]) is int)
        and type(policy.get("success_target")) is int and policy["success_target"] == 20,
        "daily_policy_limits_changed")
    if version == 4:
        _require(policy.get("limit_mode") == "daily_counts", "invalid_daily_count_policy")
    return expected


def validate_limit_revisions(state):
    """Validate approvals, untouched prefixes and every subsequent row binding."""
    revisions = state.get("daily_limit_revisions", [])
    _require(isinstance(revisions, list))
    records = state.get("records", [])
    _require(isinstance(records, list))
    if not revisions:
        _require(all(RECORD_REVISION not in row for row in records))
        return []
    policies, seen = state.get("daily_policies"), state.get("seen")
    _require(isinstance(policies, list) and policies and isinstance(seen, list)
        and state.get("daily_policy") == policies[-1])
    by_day, previous = {}, -1
    for revision in revisions:
        _require(isinstance(revision, dict) and set(revision) == REVISION_FIELDS
            and type(revision["schema_version"]) is int and revision["schema_version"] == 1
            and revision["purpose"] == "daily_success_only_revision"
            and revision["max_attempts"] is None and type(revision["success_target"]) is int
            and revision["success_target"] == 20)
        day = revision["policy_date"]
        matches = [(i, p) for i, p in enumerate(policies) if p.get("policy_date") == day]
        _require(len(matches) == 1 and day not in by_day)
        number, policy = matches[0]
        _require(policy.get("schema_version") == 3 and policy_attempt_limit(policy) == 40
            and policy.get("limit_mode") == "daily_counts"
            and revision["day_policy_sha256"] == digest(policy))
        index, start = revision["effective_record_index"], policy["start_record_index"]
        end = policies[number + 1]["start_record_index"] if number + 1 < len(policies) else len(records)
        count = revision["prior_seen_count"]
        _require(type(index) is int and type(start) is int and type(end) is int
            and 0 <= start <= index <= end <= len(records) and index >= previous
            and type(count) is int and 0 <= count <= len(seen))
        prefix, today = records[:index], records[start:index]
        _require(all(row.get("state") in {"complete", "duplicate"} for row in prefix)
            and revision["prior_records_sha256"] == digest(prefix)
            and revision["prior_seen_sha256"] == digest(seen[:count]))
        attempts = sum(bool(row.get("counts_slot")) for row in today)
        passes = len(_successes(today))
        _require(type(revision["prior_daily_attempts"]) is int and revision["prior_daily_attempts"] == attempts <= 40
            and type(revision["prior_daily_successes"]) is int and revision["prior_daily_successes"] == passes <= 20)
        try:
            approved = datetime.fromisoformat(revision["approved_at"])
            valid_time = approved.tzinfo is not None and approved.utcoffset() is not None
        except (ValueError, TypeError):
            valid_time = False
        _require(valid_time and isinstance(revision["reason"], str) and revision["reason"] == revision["reason"].strip()
            and 1 <= len(revision["reason"]) <= 500 and all(c.isprintable() for c in revision["reason"]))
        by_day[day] = (index, end, digest(revision))
        previous = index
    for index, row in enumerate(records):
        entry = by_day.get(row.get("policy_date"))
        if entry is not None and entry[0] <= index < entry[1]:
            _require(row.get(RECORD_REVISION) == entry[2])
        else:
            _require(RECORD_REVISION not in row)
    return deepcopy(revisions)


def build_limit_revision(state, *, reason, approved_at):
    """Propose only; caller separately backs up and CAS-appends under locks."""
    validate_limit_revisions(state)
    policy = state["daily_policy"]
    _require(policy.get("schema_version") == 3 and policy_attempt_limit(policy) == 40
        and state.get("status") == "stopped" and state.get("config", {}).get("limit_mode") == "daily_counts"
        and state["config"].get("total_budget_usd") is None, "daily_limit_revision_requires_stopped_count_day")
    _require(not any(row["policy_date"] == policy["policy_date"] for row in state.get("daily_limit_revisions", [])),
        "daily_limit_already_revised")
    rows, seen = state["records"], state["seen"]
    today = rows[policy["start_record_index"]:]
    revision = {"schema_version": 1, "purpose": "daily_success_only_revision",
        "policy_date": policy["policy_date"], "day_policy_sha256": digest(policy),
        "effective_record_index": len(rows), "prior_records_sha256": digest(rows),
        "prior_seen_count": len(seen), "prior_seen_sha256": digest(seen),
        "prior_daily_attempts": sum(bool(row.get("counts_slot")) for row in today),
        "prior_daily_successes": len(_successes(today)), "max_attempts": None, "success_target": 20,
        "approved_at": approved_at, "reason": reason}
    candidate = {**state, "daily_limit_revisions": [*state.get("daily_limit_revisions", []), revision]}
    validate_limit_revisions(candidate)
    return revision


def revision_for_record(state, record_index=None, *, policy=None):
    revisions = validate_limit_revisions(state)
    policy = state["daily_policy"] if policy is None else policy
    index = len(state["records"]) if record_index is None else record_index
    return next((row for row in revisions if row["policy_date"] == policy["policy_date"]
        and row["day_policy_sha256"] == digest(policy) and index >= row["effective_record_index"]), None)


def effective_daily_attempt_limit(state):
    limit = policy_attempt_limit(state["daily_policy"])
    return None if revision_for_record(state) is not None else limit


def effective_total_attempt_limit(state):
    if state.get("daily_policy"):
        limit = effective_daily_attempt_limit(state)
        if limit is None:
            return None
    return state["config"]["max_new_jobs"]


def record_limit_metadata(state):
    revision = revision_for_record(state)
    return {RECORD_REVISION: digest(revision)} if revision is not None else {}


def validate_rag_limit_admission(state, record, policy, record_index):
    revision = revision_for_record(state, record_index, policy=policy)
    unlimited = policy_attempt_limit(policy) is None or revision is not None
    receipt = record["rag_admission"]
    _require(receipt.get("schema_version") == (2 if unlimited else 1), "daily_rag_admission_changed")
    if unlimited:
        _require(receipt.get("limit_revision_sha256") == (digest(revision) if revision else None),
            "daily_rag_admission_changed")
