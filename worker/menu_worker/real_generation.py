"""Run one opt-in, work-bound anime CLI and verify its durable local output."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

from menu_worker.job_artifacts import ArtifactError, _atomic_write, artifact_directory, sha256

AGENT_ROOT = Path("/opt/ani-agent")
AGENT_PYTHON = Path("/opt/ani-venv/bin/python")
KEY_FILE = Path("/run/secrets/anime_llm_key")
MAX_IMAGES = 4
TIMEOUT_SECONDS = 1200
MAX_JSON = 16 * 1024 * 1024
MAX_PNG = 20 * 1024 * 1024
DISPOSITIONS = {
    "ready_for_feed": "ready_for_staging", "needs_crawler_update": "held",
    "invalid_input": "held", "rejected_archived": "rejected",
    "duplicate_skipped": "skipped", "stale_candidate_skipped": "skipped",
    "work_id_unresolved_skipped": "skipped",
    "source_redirect_skipped": "skipped",
    "expiry_unresolved_held": "held",
    "content_type_mismatch_skipped": "skipped",
    "stale_evidence_persisted": "skipped",
    "duplicate_evidence_persisted": "skipped",
}


class RealGenerationError(ArtifactError):
    def __init__(self, code: str, *, before_launch: bool = False):
        self.code = code
        self.before_launch = before_launch
        super().__init__(code)


def _key() -> str:
    try:
        if not KEY_FILE.is_file():
            raise ValueError
        with KEY_FILE.open("rb") as stream:
            raw = stream.read(1025)
        value = raw.decode("ascii").removesuffix("\n").removesuffix("\r")
        if len(raw) > 1024 or not re.fullmatch(r"sk-[A-Za-z0-9_-]{12,512}", value):
            raise ValueError
        return value
    except (ValueError, OSError):
        raise RealGenerationError("invalid_agent_secret", before_launch=True) from None


def _environment(directory: Path | None = None, key: str | None = None) -> dict:
    env = {
        "PATH": "/opt/ani-venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8",
        "PYTHONPATH": str(AGENT_ROOT / "src"), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false", "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "ANI_MENU_DATABASE_BACKEND": "sqlite", "ANI_MENU_DATABASE_URL": "", "ANI_MENU_DUPLICATE_BASELINE_PATH": "",
        "ANI_MENU_REVIEW_PROFILE": "standard", "ANI_MENU_MAX_REVISION": "3",
    }
    if directory is not None:
        env["ANI_MENU_DATABASE_PATH"] = str(directory / "agent.sqlite3")
    if key is not None:
        env["OPENAI_API_KEY"] = key
    return env


def preflight_real() -> None:
    """Check the installed executable and CLI without giving the dependency check an API key."""
    _key()
    resources = ("src/ani_menu_agent/cli.py", "config/sources.json", "config/source_type_map.json")
    if (sys.platform != "linux" or not os.access(AGENT_PYTHON, os.X_OK) or (AGENT_ROOT / ".env").exists()
            or not all((AGENT_ROOT / name).is_file() for name in resources)):
        raise RealGenerationError("agent_runtime_unavailable", before_launch=True)
    try:
        from PIL import Image  # noqa: F401 -- the worker also verifies decoded PNGs
        check = subprocess.run([str(AGENT_PYTHON), "-B", "-s", "-c",
            "import sys,inspect; from ani_menu_agent.cli import crawl_generate; "
            "from ani_menu_agent.work_catalog import load_work_entries; "
            "assert sys.version_info[:2] == (3,12) and 'expected_work_id' in inspect.signature(crawl_generate).parameters; "
            "assert load_work_entries()"],
            cwd=AGENT_ROOT, env=_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=60, check=False)
        if check.returncode:
            raise ValueError
    except (ImportError, OSError, ValueError, subprocess.TimeoutExpired):
        raise RealGenerationError("agent_runtime_unavailable", before_launch=True) from None


def _request(job: dict) -> dict:
    return {"source_id": job["source_id"], "source_url": job["canonical_url"], "content_type": job["content_type"],
            "menu_work_id": job["menu_work_id"]}


def _identity(job: dict) -> dict:
    if job.get("job_kind") != "anime_generate" or type(job.get("menu_work_id")) is not int:
        raise RealGenerationError("unexpected_generation_kind", before_launch=True)
    return {"producer": "ani_menu_agent", "simulated": False, "job_id": str(job["job_id"]),
            "generation_attempt_id": str(job["generation_attempt_id"]), "input_sha256": job["input_sha256"],
            "request": _request(job)}


def _bytes(path: Path, root: Path, limit: int) -> bytes:
    if path.resolve() != path or not path.is_relative_to(root) or not stat.S_ISREG(path.stat().st_mode):
        raise RealGenerationError("unsafe_artifact")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise RealGenerationError("artifact_too_large")
    return data


def _json(path: Path, root: Path, limit: int) -> tuple[dict, bytes]:
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate JSON field")
        return result
    data = _bytes(path, root, limit)
    value = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not isinstance(value, dict):
        raise RealGenerationError("invalid_result_json")
    return value, data


def _dump(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode()


def _source_matches(url, job) -> bool:
    if not isinstance(url, str):
        return False
    origin, actual = urlsplit(job["canonical_url"]), urlsplit(url)
    return (actual.scheme == "https" and actual.hostname == origin.hostname
            and actual.port in {None, 443} and actual.username is None and actual.password is None
            and not actual.fragment and actual.path.rstrip("/") == origin.path.rstrip("/") and actual.query == origin.query)


def _result(result: dict, job: dict) -> tuple[str, str, dict]:
    status = result.get("status")
    bound = result.get("expected_work_request")
    if (status not in DISPOSITIONS or bound != _request(job)
            or type(bound["menu_work_id"]) is not int):
        raise RealGenerationError("unknown_or_unbound_agent_result")
    package = result.get("package", {})
    if (not isinstance(package, dict) or result.get("simulated") or result.get("isTestData") or result.get("offline_cards")
            or package.get("isTestData") or result.get("review_fixture") is not None
            or package.get("fixtureNamespace") or package.get("actualAgentGeneration") is False or result.get("allow_llm") is False
            or result.get("review_mode") in {"offline_fixture", "fixture"}):
        raise RealGenerationError("invalid_producer_result")
    if package:
        work_ids = package.get("menu_work_ids")
        if (not isinstance(work_ids, list) or len(work_ids) != 1 or type(work_ids[0]) is not int
                or work_ids[0] != job["menu_work_id"] or package.get("content_type") != job["content_type"]
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,300}", package.get("request_id", ""))
                or result.get("request_id", package["request_id"]) != package["request_id"]
                or not any(isinstance(item, dict) and item.get("id") == job["source_id"] and
                    _source_matches(item.get("url"), job) for item in package.get("sources", []))):
            raise RealGenerationError("producer_source_or_work_mismatch")
    if status == "ready_for_feed":
        quality, issues, usage = result.get("quality_report"), result.get("qa_report"), result.get("usage_records")
        if (not package or result.get("review_mode") != "llm" or result.get("editorial_decision") != "approved_for_feed"
                or not isinstance(quality, dict) or quality.get("decision") != "pass_candidate" or quality.get("hard_blockers") != []
                or not isinstance(issues, list) or any(not isinstance(i, dict) or not isinstance(i.get("severity"), str)
                    or i["severity"] in {"blocker", "critical", "error"} for i in issues)
                or not isinstance(result.get("language_review"), dict) or not result["language_review"]
                or not isinstance(usage, list) or not any(isinstance(u, dict) and u.get("stage") == "review" for u in usage)
                or not isinstance(package.get("detail_body"), str) or not package["detail_body"].strip()):
            raise RealGenerationError("agent_review_incomplete")
    if status == "rejected_archived" and (not package or result.get("editorial_decision") != "rejected"):
        raise RealGenerationError("invalid_rejection")
    return DISPOSITIONS[status], status, package


def _check_cards(directory: Path, package: dict, required: bool, *, export_cards_to: Path | None = None) -> dict:
    cards = package.get("cards", [])
    # Match EditorialPolicy.minimum_cards_for in the isolated producer.
    minimum = 2 if package.get("content_type") in {"news", "event", "goods"} else 3
    if not isinstance(cards, list) or len(cards) > 10 or (required and len(cards) < minimum):
        raise RealGenerationError("invalid_card_count")
    files = {}
    confirmed_render = bool(cards) and all(isinstance(card, dict) and card.get("rendered_image_key") for card in cards)
    if required or confirmed_render:
        from PIL import Image
        for page, card in enumerate(cards, start=1):
            if not isinstance(card, dict) or type(card.get("page")) is not int or card["page"] != page:
                raise RealGenerationError("invalid_card_order")
            name = f"cards/{package['request_id']}/card_{page:02d}.png"
            data = _bytes(directory / name, directory, MAX_PNG)
            digest = sha256(data)
            if digest != card.get("rendered_image_key"):
                raise RealGenerationError("invalid_card_digest")
            if (len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR"
                    or int.from_bytes(data[16:20], "big") != 1308 or int.from_bytes(data[20:24], "big") != 1632):
                raise RealGenerationError("invalid_card_png")
            with Image.open(BytesIO(data)) as image:
                if image.format != "PNG" or image.size != (1308, 1632) or getattr(image, "n_frames", 1) != 1:
                    raise RealGenerationError("invalid_card_png")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
            if export_cards_to is not None:
                exported = export_cards_to / f"card_{page:02d}.png"
                exported.write_bytes(data)
                if sha256(exported.read_bytes()) != digest:
                    raise RealGenerationError("exported_card_digest_mismatch")
            files[name] = {"sha256": digest, "bytes": len(data)}
    if (directory / "cards").exists():
        if (directory / "cards").resolve() != directory / "cards":
            raise RealGenerationError("unsafe_artifact")
        actual = {path.relative_to(directory).as_posix() for path in (directory / "cards").rglob("*")
                  if path.is_file() or path.is_symlink()}
        if actual != set(files):
            raise RealGenerationError("unexpected_card_files")
    return files


def validate_real_artifacts(root: Path, job: dict) -> dict:
    """Validate/finalize only the original attempt; recovery never enters the producer."""
    evidence, _ = _verify_real_artifacts(root, job, seal_missing=True)
    return evidence


def read_sealed_staging_result(root: Path, job: dict, cards_output: Path) -> dict:
    """Copy the verified PNG bytes and return the result bound to the completed ledger row."""
    evidence, result = _verify_real_artifacts(root, job, seal_missing=False, export_cards_to=cards_output)
    if any(job.get(key) != value for key, value in evidence.items()):
        raise RealGenerationError("staging_ledger_mismatch")
    return result


def _verify_real_artifacts(root: Path, job: dict, *, seal_missing: bool,
                           export_cards_to: Path | None = None) -> tuple[dict, dict]:
    try:
        directory = artifact_directory(root, job)
        identity = _identity(job)
        receipt, _ = _json(directory / "launch.json", directory, 8192)
        if receipt != identity:
            raise RealGenerationError("attempt_identity_mismatch")
        result, original = _json(directory / "result.json", directory, MAX_JSON)
        outcome, status, package = _result(result, job)
        exit_record = directory / "exit.json"
        if exit_record.exists():
            finished, _ = _json(exit_record, directory, 2048)
            if finished.get("returncode") != 0 and status != "duplicate_skipped":
                raise RealGenerationError("agent_process_failed")
        files = _check_cards(directory, package, outcome == "ready_for_staging", export_cards_to=export_cards_to)
        digest = sha256(original)
        files["result.json"] = {"sha256": digest, "bytes": len(original)}
        manifest = {**identity, "artifact_version": 2, "outcome": outcome, "producer_status": status, "files": files}
        path = directory / "manifest.json"
        if path.is_symlink() or path.exists():
            previous, _ = _json(path, directory, 32768)
            if previous != manifest:
                raise RealGenerationError("manifest_mismatch")
        elif seal_missing:
            _atomic_write(path, _dump(manifest))
        else:
            raise RealGenerationError("sealed_manifest_missing")
        return {"outcome": outcome, "producer_status": status, "artifact_path": path.relative_to(root).as_posix(),
                "result_sha256": digest}, result
    except RealGenerationError:
        raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, SyntaxError):
        raise RealGenerationError("artifact_verification_failed") from None


# Linux container supervisor. A parent death signal covers a killed Celery pool child;
# terminating this process group also stops child CLI processes during a timeout or lost lease.
_SUPERVISOR = """import ctypes, os, signal, subprocess, sys
signal.signal(signal.SIGTERM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
if ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0) or os.getppid() != int(sys.argv[1]):
    sys.exit(70)
sys.exit(subprocess.call(sys.argv[2:]))
"""


def _stop_group(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()


def generate_real(root: Path, job: dict, pulse) -> None:
    try:
        directory = artifact_directory(root, job, create=True)
        if any(directory.iterdir()):
            raise RealGenerationError("generation_attempt_already_exists")
        identity = _identity(job)
        env = _environment(directory, _key())
        with (directory / "launch.json").open("xb") as stream:
            stream.write(_dump(identity))
            stream.flush()
            os.fsync(stream.fileno())
        _atomic_write(directory / "agent.log", b"Starting isolated anime generation.\n")
    except RealGenerationError:
        raise
    except (OSError, ArtifactError, KeyError, TypeError):
        raise RealGenerationError("agent_attempt_setup_failed", before_launch=True) from None
    command = [str(AGENT_PYTHON), "-B", "-s", "-m", "ani_menu_agent.cli", "crawl-generate",
               "--source", job["source_id"], "--url", job["canonical_url"], "--content-type", job["content_type"],
               "--expected-work-id", str(job["menu_work_id"]), "--max-images", str(MAX_IMAGES),
               "--output", str(directory / "result.json")]
    pulse()
    try:
        child = subprocess.Popen([str(AGENT_PYTHON), "-I", "-B", "-c", _SUPERVISOR, str(os.getpid()), *command],
            cwd=directory, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        raise RealGenerationError("agent_spawn_failed", before_launch=True) from None
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        while child.poll() is None:
            pulse()
            if time.monotonic() >= deadline:
                raise RealGenerationError("agent_timeout")
            time.sleep(0.25)
    finally:
        # Signal the group even if its supervisor already exited, to clean up any surviving children.
        _stop_group(child)
    try:
        _atomic_write(directory / "exit.json", _dump({"returncode": child.returncode}))
        _atomic_write(directory / "agent.log", f"Isolated anime CLI exited {child.returncode}.\n".encode())
    except OSError:
        raise RealGenerationError("agent_exit_record_failed") from None
    pulse()
    validate_real_artifacts(root, job)
