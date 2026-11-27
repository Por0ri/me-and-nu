"""File-only live generation reads: no workers, broker, DB or paid providers."""

from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from app.schemas.admin_generation import MAX_SNAPSHOT_BYTES
from app.services import admin_generation_service as service

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


def snapshot():
    counts = {key: 0 for key in (
        "queued", "running", "succeeded", "failed", "needs_attention", "cancelled",
        "held", "skipped", "rejected", "ready_for_staging",
    )}
    return {
        "schema_version": 1, "purpose": "generation_observability", "generated_at": NOW.isoformat(),
        "timezone": "Asia/Seoul", "policy_date": "2026-11-27", "availability": "available", "reason": None,
        "daily": {"attempts": 4, "max_attempts": 40, "passes": 1, "success_target": 20,
                  "remaining_attempts": 36, "remaining_passes": 19, "pending_records": 1,
                  "duplicate_records": 0, "known_estimated_cost_usd": "0.0123456789",
                  "total_estimated_cost_usd": None, "reserved_cost_usd": "0.001",
                  "unknown_cost_jobs": 1, "inflight_cost_jobs": 1, "article_attempts": 1, "rag_attempts": 3},
        "history": {"known_estimated_cost_usd": "0.0615848589", "baseline_estimated_cost_usd": "0.01178341",
                    "total_estimated_cost_usd": None, "unknown_cost_jobs": 3},
        "jobs": {"article": dict(counts, succeeded=1, skipped=1),
                 "rag": dict(counts, running=1, succeeded=2, held=1, ready_for_staging=1)},
        "broker": {"article_queued": 0, "rag_queued": 0, "unacked": 1, "unacked_index": 1},
        "runtime": {"controller": "active", "timer": "inactive", "article_worker": "running", "rag_worker": "running"},
    }


def read(tmp_path, value=None, *, now=NOW):
    target = tmp_path / "snapshot.json"
    target.write_text(json.dumps(snapshot() if value is None else value), encoding="utf-8")
    return service.get_generation_status(snapshot_path=str(target), now=now)


def test_live_counts_both_producers_and_incomplete_cost_remain_distinct(tmp_path):
    result = read(tmp_path)
    assert result.availability == "available"
    assert result.daily.attempts == 4 and result.daily.passes == 1
    assert result.daily.total_estimated_cost_usd is None
    assert result.daily.known_estimated_cost_usd == "0.0123456789"
    assert result.history.unknown_cost_jobs == 3
    assert result.jobs.rag.ready_for_staging == 1 and result.jobs.rag.held == 1
    wire = result.model_dump(mode="json", by_alias=True)
    assert wire["daily"]["maxAttempts"] == 40
    assert wire["daily"]["successTarget"] == 20
    assert wire["jobs"]["rag"]["needsAttention"] == 0
    assert "max_attempts" not in json.dumps(wire)


def test_uncapped_attempts_above_forty_preserve_twenty_pass_target_and_costs(tmp_path):
    data = snapshot()
    data["daily"].update(attempts=61, max_attempts=None, remaining_attempts=None,
                         passes=20, remaining_passes=0, article_attempts=21, rag_attempts=40)
    result = read(tmp_path, data)
    assert result.availability == "available"
    assert result.daily.attempts == 61 and result.daily.max_attempts is None
    assert result.daily.remaining_attempts is None
    assert result.daily.passes == result.daily.success_target == 20
    assert result.daily.known_estimated_cost_usd == data["daily"]["known_estimated_cost_usd"]
    assert result.daily.total_estimated_cost_usd is None and result.history.unknown_cost_jobs == 3
    wire = result.model_dump(mode="json", by_alias=True)
    assert wire["daily"]["maxAttempts"] is None and wire["daily"]["remainingAttempts"] is None


@pytest.mark.parametrize("maximum", [40, None])
def test_unknown_attempt_count_is_independent_of_known_policy(tmp_path, maximum):
    data = snapshot()
    data["daily"].update(attempts=None, max_attempts=maximum, remaining_attempts=None,
                         article_attempts=None, rag_attempts=None)
    result = read(tmp_path, data)
    assert result.availability == "available"
    assert result.daily.attempts is None and result.daily.remaining_attempts is None
    assert result.daily.max_attempts == maximum


@pytest.mark.parametrize("field,value", [
    ("remaining_attempts", 0), ("remaining_attempts", 40), ("max_attempts", True),
    ("max_attempts", "unlimited"), ("max_attempts", 1000000), ("success_target", None),
    ("passes", 21), ("attempts", -1), ("attempts", True), ("attempts", 61.0),
])
def test_uncapped_policy_still_rejects_inconsistent_counts_and_target(tmp_path, field, value):
    data = snapshot()
    data["daily"].update(attempts=61, max_attempts=None, remaining_attempts=None,
                         article_attempts=21, rag_attempts=40)
    data["daily"][field] = value
    assert read(tmp_path, data).reason == "snapshot_invalid"


@pytest.mark.parametrize("seconds,availability", [(179, "available"), (180, "stale"), (7200, "stale")])
def test_freshness_boundary_retains_last_observed_values_with_stale_label(tmp_path, seconds, availability):
    result = read(tmp_path, now=NOW + timedelta(seconds=seconds))
    assert result.availability == availability
    assert result.daily.attempts == 4
    assert result.age_seconds == seconds
    assert result.reason == ("snapshot_stale" if availability == "stale" else None)


def test_kst_midnight_does_not_label_yesterday_as_today_or_reset_history(tmp_path):
    value = snapshot()
    value["generated_at"] = "2026-11-27T14:59:59+00:00"
    result = read(tmp_path, value, now=datetime(2026, 11, 27, 15, 0, tzinfo=timezone.utc))
    assert result.age_seconds == 1
    assert result.availability == "partial" and result.reason == "policy_day_mismatch"
    assert result.daily is None and result.jobs is None
    assert result.history.unknown_cost_jobs == 3
    assert result.policy_date.isoformat() == "2026-11-27"


def test_source_partial_preserves_nullable_values_without_zero_fill(tmp_path):
    value = snapshot()
    value.update(availability="partial", reason="source_partial", broker=None, runtime=None)
    value["jobs"]["rag"] = dict.fromkeys(value["jobs"]["rag"])
    result = read(tmp_path, value)
    assert result.availability == "partial" and result.broker is None
    assert result.jobs.rag.running is None and result.daily.attempts == 4


@pytest.mark.parametrize("path,reason", [(None, "not_configured"), ("missing", "snapshot_missing")])
def test_no_snapshot_is_unknown_not_empty_day(tmp_path, path, reason):
    result = service.get_generation_status(snapshot_path=None if path is None else str(tmp_path / path), now=NOW)
    assert result.reason == reason and result.availability == "unavailable"
    assert all(getattr(result, key) is None for key in ("daily", "history", "jobs", "broker", "runtime", "generated_at"))


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 1.0), ("purpose", "secret-provider-key"), ("unexpected", "secret-provider-key"),
    ("timezone", "UTC"), ("generated_at", "2026-11-27T12:00:00"),
    ("generated_at", "2026-11-27T21:00:00+09:00"), ("reason", "secret runtime traceback"),
])
def test_strict_top_level_contract_and_no_raw_error_leak(tmp_path, field, value):
    data = snapshot()
    data[field] = value
    result = read(tmp_path, data)
    assert result.reason == "snapshot_invalid"
    assert "secret" not in result.model_dump_json()
    assert result.daily is None


@pytest.mark.parametrize("field,value", [
    ("attempts", True), ("attempts", "4"), ("attempts", -1), ("attempts", 41),
    ("max_attempts", 41), ("max_attempts", 40.0), ("success_target", 21), ("passes", 21),
    ("remaining_attempts", 40), ("rag_attempts", 4),
    ("known_estimated_cost_usd", "NaN"), ("known_estimated_cost_usd", "-1"),
    ("known_estimated_cost_usd", "1e-3"), ("known_estimated_cost_usd", 0.01),
    ("total_estimated_cost_usd", "0.0123456789"), ("credential", "secret-provider-key"),
])
def test_invalid_or_misleading_daily_counts_and_costs_fail_closed(tmp_path, field, value):
    data = snapshot()
    data["daily"][field] = value
    assert read(tmp_path, data).reason == "snapshot_invalid"


def test_future_timestamp_cannot_appear_fresh(tmp_path):
    data = snapshot()
    data["generated_at"] = (NOW + timedelta(seconds=31)).isoformat()
    result = read(tmp_path, data)
    assert result.reason == "snapshot_future" and result.daily is None


@pytest.mark.parametrize("payload", [b'{"a":1,"a":2}', b'{"a":NaN}', b"\xff", b"[]", b"{" + b" " * MAX_SNAPSHOT_BYTES],
                         ids=["duplicate-key", "non-finite", "bad-encoding", "wrong-type", "oversized"])
def test_invalid_duplicate_nonfinite_oversized_json_is_unavailable(tmp_path, payload):
    target = tmp_path / "snapshot.json"
    target.write_bytes(payload)
    result = service.get_generation_status(snapshot_path=str(target), now=NOW)
    assert result.reason == "snapshot_invalid" and result.daily is None


def test_permission_error_has_no_private_path_or_exception_details(monkeypatch):
    def denied(_):
        raise PermissionError("secret credentials at private path")
    monkeypatch.setattr(service, "_load", denied)
    result = service.get_generation_status(snapshot_path="/private/path", now=NOW)
    assert result.reason == "snapshot_unreadable"
    assert "private" not in result.model_dump_json()


def test_atomic_replace_is_read_fresh_without_process_cache(tmp_path):
    first = read(tmp_path)
    data = snapshot()
    data["daily"].update(attempts=5, remaining_attempts=35, rag_attempts=4)
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps(data), encoding="utf-8")
    replacement.replace(tmp_path / "snapshot.json")
    next_result = service.get_generation_status(snapshot_path=str(tmp_path / "snapshot.json"), now=NOW)
    assert first.daily.attempts == 4 and next_result.daily.attempts == 5


def test_duplicate_key_in_otherwise_valid_snapshot_is_rejected(tmp_path):
    payload = json.dumps(snapshot())
    target = tmp_path / "snapshot.json"
    target.write_text(payload[:-1] + ',"schema_version":1}', encoding="utf-8")
    assert service.get_generation_status(snapshot_path=str(target), now=NOW).reason == "snapshot_invalid"


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation requires host privileges")
def test_symlinks_and_nonregular_files_are_rejected_without_following_or_blocking(tmp_path):
    read(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(tmp_path / "snapshot.json")
    assert service.get_generation_status(snapshot_path=str(link), now=NOW).reason == "snapshot_invalid"
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    assert service.get_generation_status(snapshot_path=str(fifo), now=NOW).reason == "snapshot_invalid"


def test_relative_path_is_never_resolved_against_working_directory():
    assert service.get_generation_status(snapshot_path="snapshot.json", now=NOW).reason == "snapshot_invalid"
