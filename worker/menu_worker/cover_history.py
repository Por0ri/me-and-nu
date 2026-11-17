"""Build immutable, attempt-bound cover input from verified completed results."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
from tempfile import TemporaryDirectory
from uuid import UUID

from menu_worker.job_artifacts import sha256
from menu_worker.real_generation import (
    AGENT_PYTHON, AGENT_ROOT, MAX_JSON, RealGenerationError, _dump, _environment,
    read_sealed_staging_result,
)

MAX_ENTRIES = 10000


def _invalid():
    return RealGenerationError("cover_history_invalid", before_launch=True)


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError
    return value


def validate_snapshot(data: bytes, job: dict) -> dict:
    """The producer separately validates ImageAsset and its canonical visual keys."""
    def unique(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError
        return value

    try:
        if not isinstance(data, bytes) or len(data) > MAX_JSON:
            raise ValueError
        document = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not isinstance(document, dict) or set(document) != {"schema_version", "consumer", "entries"}
                or type(document["schema_version"]) is not int or document["schema_version"] != 1
                or document["consumer"] != {"job_id": _uuid(str(job["job_id"])),
                    "generation_attempt_id": _uuid(str(job["generation_attempt_id"]))}
                or not isinstance(document["entries"], list) or len(document["entries"]) > MAX_ENTRIES):
            raise ValueError
        for entry in document["entries"]:
            if (not isinstance(entry, dict) or set(entry) != {"request_id", "image", "origin"}
                    or not isinstance(entry["request_id"], str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,300}", entry["request_id"])
                    or not isinstance(entry["image"], dict) or not entry["image"]
                    or not isinstance(entry["origin"], dict)
                    or set(entry["origin"]) != {"job_id", "generation_attempt_id", "result_sha256"}):
                raise ValueError
            origin = entry["origin"]
            _uuid(origin["job_id"])
            _uuid(origin["generation_attempt_id"])
            if not isinstance(origin["result_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", origin["result_sha256"]):
                raise ValueError
        return document
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise _invalid() from None


def cover_entry(package: dict, origin: dict) -> dict | None:
    """A result without a selected source-image cover has no image identity to add."""
    try:
        cards = package.get("cards", [])
        if not isinstance(cards, list):
            raise ValueError
        if not cards:
            return None
        if any(not isinstance(card, dict) or type(card.get("page")) is not int or card["page"] < 1 for card in cards):
            raise ValueError
        first = min(cards, key=lambda card: card["page"])
        image_id = first.get("image_id")
        if not image_id and first.get("visual_mode") == "editorial_graphic":
            return None
        images = package.get("images", [])
        if not isinstance(image_id, str) or not image_id or not isinstance(images, list):
            raise ValueError
        matching = [image for image in images if isinstance(image, dict) and image.get("id") == image_id]
        if len(matching) != 1:
            raise ValueError
        return {"request_id": package["request_id"], "image": matching[0], "origin": origin}
    except (ValueError, TypeError, KeyError):
        raise _invalid() from None


def history_environment(path: Path, data: bytes, job: dict) -> dict[str, str]:
    return {"ANI_MENU_COVER_HISTORY_FILE": str(path), "ANI_MENU_COVER_HISTORY_SHA256": sha256(data),
            "ANI_MENU_COVER_HISTORY_JOB_ID": str(job["job_id"]),
            "ANI_MENU_COVER_HISTORY_ATTEMPT_ID": str(job["generation_attempt_id"])}


def _bootstrap(job: dict) -> list[dict]:
    filename = os.environ.get("ANIME_COVER_HISTORY_BOOTSTRAP_FILE", "")
    digest = os.environ.get("ANIME_COVER_HISTORY_BOOTSTRAP_SHA256", "")
    if not filename and not digest:
        return []
    try:
        path = Path(filename)
        if (not filename or not re.fullmatch(r"[0-9a-f]{64}", digest) or not path.is_absolute()
                or path.resolve() != path or not stat.S_ISREG(path.stat().st_mode)):
            raise ValueError
        with path.open("rb") as stream:
            data = stream.read(MAX_JSON + 1)
        if len(data) > MAX_JSON or sha256(data) != digest:
            raise ValueError
        # A bootstrap is itself an attempt-bound, approved snapshot; its consumer
        # is only a provenance identifier. Rebind a copy for this attempt.
        raw = json.loads(data)
        if not isinstance(raw, dict) or not isinstance(raw.get("consumer"), dict):
            raise ValueError
        validated = validate_snapshot(data, raw["consumer"])
        return validated["entries"]
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise _invalid() from None


def build_snapshot(root: Path, job: dict, history: list[dict]) -> bytes:
    try:
        if len(history) > MAX_ENTRIES:
            raise _invalid()
        entries = _bootstrap(job)
        for previous in history:
            result = read_sealed_staging_result(root, previous)
            origin = {key: previous[key] for key in ("job_id", "generation_attempt_id")}
            origin["result_sha256"] = previous["result_sha256"]
            entry = cover_entry(result.get("package", {}), origin)
            if entry is not None:
                entries.append(entry)
        # A bootstrap and ledger can legitimately refer to the same original.
        # They must agree on the exact metadata rather than silently overwrite.
        unique = {}
        for entry in entries:
            key = (entry["origin"]["job_id"], entry["origin"]["generation_attempt_id"])
            if key in unique and unique[key] != entry:
                raise _invalid()
            unique[key] = entry
        data = _dump({"schema_version": 1,
            "consumer": {key: str(job[key]) for key in ("job_id", "generation_attempt_id")},
            "entries": [unique[key] for key in sorted(unique)]})
        validate_snapshot(data, job)
        return data
    except RealGenerationError:
        raise _invalid() from None
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise _invalid() from None


def preflight_snapshot(data: bytes, job: dict) -> None:
    """Run the installed Agent's exact loader without a key, before start fencing."""
    validate_snapshot(data, job)
    try:
        with TemporaryDirectory(prefix="anime-cover-input-") as temporary:
            path = Path(temporary) / "cover-history.json"
            path.write_bytes(data)
            path.chmod(0o400)
            env = {**_environment(), **history_environment(path, data, job)}
            checked = subprocess.run([str(AGENT_PYTHON), "-B", "-s", "-c",
                "from ani_menu_agent.cover_history import validate_cover_history_environment; "
                "validate_cover_history_environment()"], cwd=AGENT_ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30, check=False)
            if checked.returncode:
                raise _invalid()
    except (OSError, subprocess.TimeoutExpired):
        raise RealGenerationError("cover_history_unavailable", before_launch=True) from None
