"""Keep discovering and generating type-diverse content until STOP is requested."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

CONTENT_TYPES = (
    "news",
    "event",
    "guide",
    "people",
    "goods",
    "review",
    "humor",
    "character",
    "curation",
    "music",
)
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _timestamp() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _request_awake() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            )
        )
    except (AttributeError, OSError):
        return False


def _log(path: Path, message: str) -> None:
    line = f"{_timestamp()} {message}"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
        stream.flush()
    print(line, flush=True)


def _summary_results(summary_path: Path) -> tuple[int, int, float]:
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, 0, 0.0
    passed = 0
    attempts = len(summary.get("outcomes", []))
    cost = 0.0
    for outcome in summary.get("outcomes", []):
        if outcome.get("status") == "ready_for_feed":
            passed += 1
        output = outcome.get("output")
        if not output:
            continue
        try:
            result = json.loads(Path(output).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        usage = result.get("usage_summary") or {}
        cost += float(usage.get("estimated_cost_usd", 0) or 0)
    return passed, attempts, cost


def _ready_count(database: Path) -> int:
    if not database.exists():
        return 0
    try:
        with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as con:
            return int(
                con.execute(
                    "SELECT COUNT(*) FROM content_drafts WHERE status='ready_for_feed'"
                ).fetchone()[0]
            )
    except sqlite3.Error:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--batch-limit", type=int, default=20)
    parser.add_argument("--passed-per-type", type=int, default=20)
    args = parser.parse_args()

    root = args.root.resolve()
    database = args.database.resolve()
    baseline = args.baseline.resolve()
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "worker.log"
    stop_path = root / "STOP"
    pid_path = root / "worker.pid"
    if stop_path.exists():
        stop_path.unlink()
    pid_path.write_text(str(os.getpid()), encoding="ascii")

    uv = shutil.which("uv") or r"C:\Users\yunju\.local\bin\uv.exe"
    if not Path(uv).exists():
        _log(log_path, "FATAL uv executable not found; worker cannot start")
        return 2
    if not baseline.is_file():
        _log(log_path, f"FATAL duplicate/image baseline missing: {baseline}")
        return 2

    os.environ["ANI_MENU_DATABASE_BACKEND"] = "sqlite"
    os.environ["ANI_MENU_DATABASE_PATH"] = str(database)
    os.environ["ANI_MENU_DUPLICATE_BASELINE_PATH"] = str(baseline)
    os.environ["ANI_MENU_IMAGE_REFERENCE_PATH"] = str(baseline)

    awake = _request_awake()
    _log(
        log_path,
        f"START pid={os.getpid()} db={database} baseline={baseline} "
        f"batch_limit={args.batch_limit} passed_per_type={args.passed_per_type} "
        f"prevent_sleep={'enabled' if awake else 'unavailable'}",
    )
    cycle = 0
    type_offset = 0
    while not stop_path.exists():
        cycle += 1
        ordered_types = CONTENT_TYPES[type_offset:] + CONTENT_TYPES[:type_offset]
        _log(log_path, f"CYCLE_START cycle={cycle} order={','.join(ordered_types)}")
        for content_type in ordered_types:
            if stop_path.exists():
                break
            _request_awake()
            summaries_before = set((root / "runs" / content_type).rglob("run_summary.json"))
            type_log = root / f"cycle_{cycle:04d}_{content_type}.log"
            command = [
                uv,
                "run",
                "ani-menu",
                "crawl-type",
                "--content-type",
                content_type,
                "--limit",
                str(args.batch_limit),
                "--target-passed",
                str(args.passed_per_type),
                "--region-policy",
                "kr_first",
                "--output-dir",
                str(root / "runs"),
            ]
            _log(log_path, f"TYPE_START cycle={cycle} type={content_type}")
            started = time.monotonic()
            try:
                with type_log.open("ab") as output:
                    process = subprocess.Popen(
                        command,
                        cwd=args.root.resolve().parents[1],
                        env=os.environ.copy(),
                        stdout=output,
                        stderr=subprocess.STDOUT,
                    )
                    return_code = process.wait()
                elapsed = time.monotonic() - started
                new_summaries = set((root / "runs" / content_type).rglob("run_summary.json"))
                new_summaries -= summaries_before
                passed = attempts = 0
                estimated_cost = 0.0
                for summary_path in new_summaries:
                    type_passed, type_attempts, type_cost = _summary_results(summary_path)
                    passed += type_passed
                    attempts += type_attempts
                    estimated_cost += type_cost
                _log(
                    log_path,
                    f"TYPE_END cycle={cycle} type={content_type} exit={return_code} "
                    f"elapsed_s={elapsed:.1f} candidates={attempts} passed={passed} "
                    f"ready_total={_ready_count(database)} "
                    f"estimated_llm_usd={estimated_cost:.6f} "
                    f"summaries={len(new_summaries)} log={type_log}",
                )
            except Exception as exc:  # keep other types running after a transient failure
                _log(
                    log_path,
                    f"TYPE_ERROR cycle={cycle} type={content_type} "
                    f"error={type(exc).__name__}: {exc}; retrying in later cycle",
                )
                time.sleep(15)
        type_offset = (type_offset + 3) % len(CONTENT_TYPES)
        if not stop_path.exists():
            _request_awake()
            time.sleep(5)

    _log(log_path, f"STOPPED pid={os.getpid()} ready_total={_ready_count(database)}")
    try:
        pid_path.unlink()
    except OSError:
        pass
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except (AttributeError, OSError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
