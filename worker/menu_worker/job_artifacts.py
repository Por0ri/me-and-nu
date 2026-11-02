"""Small, explicitly simulated outputs; no agent imports or network requests."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4


class ArtifactError(ValueError):
    pass


class FakeAgentError(RuntimeError):
    pass


# A fixture pixel, deliberately not a service carousel or an importable bundle.
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4DwQACfsD/fteaysAAAAASUVORK5CYII="
)


def _json(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_directory(root: Path, job: dict, *, create: bool = False) -> Path:
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise ArtifactError("Invalid artifact root")
    try:
        parts = ("jobs", str(UUID(str(job["job_id"]))), "attempts",
                 str(UUID(str(job["generation_attempt_id"]))))
    except (ValueError, KeyError, TypeError):
        raise ArtifactError("Invalid artifact identity") from None
    directory = root
    for part in parts:
        directory = directory / part
        if directory.is_symlink():
            raise ArtifactError("Linked artifact path is forbidden")
        if create:
            directory.mkdir(exist_ok=True)
        if not directory.is_dir():
            raise ArtifactError("Artifact directory is missing")
    return directory


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        # Persist the directory entry as well as contents on Linux containers.
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def generate_fake(root: Path, job: dict, pulse=lambda: None) -> None:
    """At most one invocation per job. Recovery only reads the final manifest."""
    scenario = urlsplit(job["source_url"]).path.strip("/").split("/")[0]
    if scenario == "fail":
        raise FakeAgentError("Simulated generation failure")
    if scenario == "slow":
        for _ in range(20):
            pulse()
            time.sleep(1)
    outcome = {"held": "held", "skipped": "skipped"}.get(scenario, "ready_for_staging")
    producer_status = {"held": "held_for_quality_review", "skipped": "duplicate_skipped"}.get(
        scenario, "ready_for_feed"
    )
    _write_simulation(root, job, pulse, producer="fake_ani_menu_agent", outcome=outcome,
                      producer_status=producer_status, include_card=outcome != "skipped")


def generate_anime_probe(root: Path, job: dict, pulse) -> None:
    """Produce an execution receipt without loading a model or generating cards."""
    _write_simulation(root, job, pulse, producer="anime_queue_probe", outcome="routing_verified",
                      producer_status="anime_probe_completed", include_card=False)


def _write_simulation(root, job, pulse, *, producer, outcome, producer_status, include_card):
    directory = artifact_directory(root, job, create=True)
    if (directory / "manifest.json").exists():
        raise ArtifactError("Generation output already exists")
    result = {
        "artifact_version": 1, "producer": producer, "simulated": True,
        "job_id": str(job["job_id"]), "generation_attempt_id": str(job["generation_attempt_id"]),
        "input_sha256": job["input_sha256"], "outcome": outcome, "producer_status": producer_status,
        "skip_reason": "duplicate" if outcome == "skipped" else None,
        "llm_calls": 0, "service_database_changed": False,
    }
    log = f"{producer}: simulated execution, zero LLM calls.\n".encode()
    files = {"result.json": _json(result), "agent.log": log}
    if include_card:
        files["card-1.png"] = PIXEL
    for name, data in files.items():
        pulse()
        _atomic_write(directory / name, data)
    pulse()
    # Manifest is the completion marker, written after all referenced files.
    _atomic_write(directory / "manifest.json", _json({
        "artifact_version": 1,
        "files": {name: {"sha256": sha256(data), "bytes": len(data)} for name, data in files.items()},
    }))


def validate_artifacts(root: Path, job: dict) -> dict:
    directory = artifact_directory(root, job)

    def read(name: str) -> bytes:
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
            raise ArtifactError("Missing or invalid artifact")
        return path.read_bytes()

    try:
        manifest = json.loads(read("manifest.json"))
        files = manifest["files"]
        if manifest["artifact_version"] != 1 or set(files) not in (
            {"result.json", "agent.log"}, {"result.json", "agent.log", "card-1.png"}
        ):
            raise ArtifactError("Invalid manifest")
        content = {}
        for name, expected in files.items():
            data = read(name)
            if expected != {"sha256": sha256(data), "bytes": len(data)}:
                raise ArtifactError("Artifact digest mismatch")
            content[name] = data
        result = json.loads(content["result.json"])
        if not isinstance(result, dict):
            raise ArtifactError("Result must be an object")
        # Pre-v2 fake receipts use the original producer identity and file layout.
        anime_probe = job.get("job_kind", "fake") == "anime"
        expected_identity = {
            "artifact_version": 1, "producer": "anime_queue_probe" if anime_probe else "fake_ani_menu_agent", "simulated": True,
            "job_id": str(job["job_id"]), "generation_attempt_id": str(job["generation_attempt_id"]),
            "input_sha256": job["input_sha256"], "llm_calls": 0, "service_database_changed": False,
        }
        if any(result.get(key) != value for key, value in expected_identity.items()):
            raise ArtifactError("Artifact identity mismatch")
        dispositions = {"ready_for_staging": "ready_for_feed", "held": "held_for_quality_review",
                        "skipped": "duplicate_skipped"}
        if anime_probe:
            dispositions = {"routing_verified": "anime_probe_completed"}
        if result.get("outcome") not in dispositions or result.get("producer_status") != dispositions[result["outcome"]]:
            raise ArtifactError("Invalid generation outcome")
        if ("card-1.png" in files) != (not anime_probe and result["outcome"] != "skipped"):
            raise ArtifactError("Unexpected fixture card layout")
        if "card-1.png" in content and content["card-1.png"] != PIXEL:
            raise ArtifactError("Invalid fixture card")
        return {"outcome": result["outcome"], "producer_status": result["producer_status"],
                "artifact_path": str((directory / "manifest.json").relative_to(root)).replace("\\", "/"),
                "result_sha256": sha256(content["result.json"])}
    except (KeyError, TypeError, ValueError, OSError):
        raise ArtifactError("Artifact validation failed") from None
