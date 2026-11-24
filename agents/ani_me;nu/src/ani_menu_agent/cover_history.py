"""Validate a Worker-sealed, read-only history of other requests' cover images.

This boundary reads only the named JSON file. Image URLs and local asset paths
are identity data, never fetched or opened. No shared Agent database is needed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
from uuid import UUID

from ani_menu_agent.config import Settings, get_settings
from ani_menu_agent.image_selection import image_visual_keys
from ani_menu_agent.models import ImageAsset


MAX_HISTORY_BYTES = 16 * 1024 * 1024
MAX_HISTORY_ENTRIES = 10_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class CoverHistoryError(ValueError):
    """A configured history cannot be trusted; stop before collection or LLM."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Invalid cover history: {code}")


@dataclass(frozen=True)
class CoverHistoryEntry:
    request_id: str
    visual_keys: frozenset[str]


def _object(value: object, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise CoverHistoryError("invalid_schema")
    return value


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise CoverHistoryError("invalid_uuid")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise CoverHistoryError("invalid_uuid") from None
    return value


def _sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CoverHistoryError("invalid_sha256")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CoverHistoryError("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(_value: str) -> None:
    raise CoverHistoryError("nonfinite_json_number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise CoverHistoryError("nonfinite_json_number")
    return parsed


def _check_path(path: Path) -> os.stat_result:
    # Check every component: a regular file through a symlinked directory is
    # still not an immutable path supplied by the Worker.
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            hasattr(component, "is_junction") and component.is_junction()
        ):
            raise CoverHistoryError("symlink_not_allowed")
        expected = stat.S_ISREG if component == path else stat.S_ISDIR
        if not expected(info.st_mode):
            raise CoverHistoryError("not_regular_file")
    return info


def _identity(info: os.stat_result) -> tuple:
    # Windows lstat/fstat expose different ctime semantics. The stable file ID,
    # byte count and mtime fence replacement; the pinned SHA verifies content.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _read_history(filename: str) -> bytes:
    path = Path(filename)
    if not path.is_absolute() or ".." in path.parts:
        raise CoverHistoryError("absolute_path_required")
    try:
        before = _check_path(path)
        if before.st_size > MAX_HISTORY_BYTES:
            raise CoverHistoryError("file_too_large")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or _identity(before) != _identity(opened):
                raise CoverHistoryError("file_changed")
            raw = stream.read(MAX_HISTORY_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(raw) > MAX_HISTORY_BYTES:
            raise CoverHistoryError("file_too_large")
        if _identity(opened) != _identity(after) or _identity(after) != _identity(_check_path(path)):
            raise CoverHistoryError("file_changed")
        return raw
    except OSError:
        raise CoverHistoryError("file_unavailable") from None


def load_cover_history(*, settings: Settings | None = None) -> tuple[CoverHistoryEntry, ...]:
    """Re-read and verify the complete snapshot on every call; never cache bytes.

    With all four settings absent, existing DB-only behavior is retained. Any
    partial or empty setting fails closed, including an explicitly empty file.
    """
    settings = settings or get_settings()
    values = (settings.cover_history_file, settings.cover_history_sha256,
              settings.cover_history_job_id, settings.cover_history_attempt_id)
    if all(value is None for value in values):
        return ()
    if any(not isinstance(value, str) or not value for value in values):
        raise CoverHistoryError("incomplete_configuration")
    filename, digest, job_id, attempt_id = values
    digest = _sha256(digest)
    consumer = {"job_id": _uuid(job_id), "generation_attempt_id": _uuid(attempt_id)}
    raw = _read_history(filename)
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest):
        raise CoverHistoryError("hash_mismatch")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_nonfinite, parse_float=_finite_float)
    except CoverHistoryError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise CoverHistoryError("invalid_json") from None
    document = _object(document, {"schema_version", "consumer", "entries"})
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise CoverHistoryError("unsupported_schema_version")
    bound = _object(document["consumer"], {"job_id", "generation_attempt_id"})
    _uuid(bound["job_id"])
    _uuid(bound["generation_attempt_id"])
    if bound != consumer:
        raise CoverHistoryError("consumer_mismatch")
    entries = document["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_HISTORY_ENTRIES:
        raise CoverHistoryError("invalid_entry_count")
    validated = []
    for entry in entries:
        entry = _object(entry, {"request_id", "image", "origin"})
        request_id = entry["request_id"]
        if not isinstance(request_id, str) or not request_id.strip():
            raise CoverHistoryError("invalid_request_id")
        origin = _object(entry["origin"], {"job_id", "generation_attempt_id", "result_sha256"})
        _uuid(origin["job_id"])
        _uuid(origin["generation_attempt_id"])
        _sha256(origin["result_sha256"])
        payload = entry["image"]
        if not isinstance(payload, dict) or set(payload) - ImageAsset.model_fields.keys() or "content_bytes" in payload:
            raise CoverHistoryError("invalid_image")
        try:
            image = ImageAsset.model_validate(payload)
            keys = frozenset(image_visual_keys(image))
        except (TypeError, ValueError):
            raise CoverHistoryError("invalid_image") from None
        if not image.id.strip() or not keys or any(not key for key in keys):
            raise CoverHistoryError("invalid_image")
        validated.append(CoverHistoryEntry(request_id=request_id, visual_keys=keys))
    return tuple(validated)


def validate_cover_history_environment() -> tuple[CoverHistoryEntry, ...]:
    """No-key subprocess preflight shared by the Worker and crawl-generate."""
    return load_cover_history()
