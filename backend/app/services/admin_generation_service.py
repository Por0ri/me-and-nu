"""Read an atomic, sanitized file only. Never connect to the generation system."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import stat
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.schemas.admin_generation import (
    AdminGenerationResponse, GenerationSnapshot, MAX_SNAPSHOT_BYTES, STALE_AFTER_SECONDS,
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate snapshot key")
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError("non-finite snapshot number")


def _load(path: str) -> GenerationSnapshot:
    target = Path(path)
    if not target.is_absolute() or any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError("snapshot requires an absolute regular file")
    descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot is not a bounded regular file")
        payload = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot too large")
    # Reject duplicate keys/NaN before Pydantic's JSON date parser; neither raw
    # content nor validation errors are exposed in a response or a log.
    data = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_invalid_number)
    return GenerationSnapshot.model_validate_json(json.dumps(data), strict=True)


def get_generation_status(*, snapshot_path: str | None, now: datetime | None = None) -> AdminGenerationResponse:
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    response = AdminGenerationResponse(observed_at=observed)
    if not snapshot_path:
        return response.model_copy(update={"reason": "not_configured"})
    try:
        snapshot = _load(snapshot_path)
    except FileNotFoundError:
        return response.model_copy(update={"reason": "snapshot_missing"})
    except OSError:
        return response.model_copy(update={"reason": "snapshot_unreadable"})
    except (ValueError, ValidationError, TypeError, RecursionError):
        return response.model_copy(update={"reason": "snapshot_invalid"})
    age = (observed - snapshot.generated_at).total_seconds()
    if age < -30:
        return response.model_copy(update={"reason": "snapshot_future"})
    response = AdminGenerationResponse(
        observed_at=observed, generated_at=snapshot.generated_at,
        age_seconds=min(1_000_000_000, max(0, int(age))), policy_date=snapshot.policy_date,
        availability=snapshot.availability, reason=snapshot.reason,
        daily=snapshot.daily, history=snapshot.history, jobs=snapshot.jobs,
        broker=snapshot.broker, runtime=snapshot.runtime,
    )
    if snapshot.availability != "unavailable" and age >= STALE_AFTER_SECONDS:
        response.availability = "stale"
        response.reason = "snapshot_stale"
    if snapshot.policy_date != observed.astimezone(ZoneInfo("Asia/Seoul")).date():
        response.daily = None
        response.jobs = None
        if response.availability == "available":
            response.availability = "partial"
        if snapshot.availability != "unavailable":
            response.reason = "policy_day_mismatch"
    return response
