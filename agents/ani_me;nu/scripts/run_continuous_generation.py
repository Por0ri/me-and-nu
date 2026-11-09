"""Supervise crawler, content generation, and review as separate processes.

This script only starts workers when explicitly launched. A shared STOP file is
observed by all child processes; it is never removed automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

CONTENT_TYPES = ("news", "event", "goods")
WORKER_STAGES = ("crawl", "generate", "review")


def _timestamp() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _log(path: Path, message: str) -> None:
    line = f"{_timestamp()} {message}"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
        stream.flush()
    print(line, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path, help="worker logs and STOP file")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--batch-limit", type=int, default=200)
    parser.add_argument("--source-refresh-minutes", type=int, default=5)
    args = parser.parse_args()

    run_root = args.root.resolve()
    project_root = Path(__file__).resolve().parents[1]
    database = args.database.resolve()
    baseline = args.baseline.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    logs = run_root / "workers"
    logs.mkdir(parents=True, exist_ok=True)
    stop_file = run_root / "STOP"
    supervisor_log = run_root / "worker.log"
    if stop_file.exists():
        _log(supervisor_log, f"STOP marker exists; refusing to start workers: {stop_file}")
        return 3

    uv = shutil.which("uv") or r"C:\Users\yunju\.local\bin\uv.exe"
    if not Path(uv).exists():
        _log(supervisor_log, "FATAL uv executable not found")
        return 2
    if not baseline.is_file():
        _log(supervisor_log, f"FATAL duplicate/image baseline missing: {baseline}")
        return 2

    environment = os.environ.copy()
    environment["ANI_MENU_DATABASE_BACKEND"] = "sqlite"
    environment["ANI_MENU_DATABASE_PATH"] = str(database)
    environment["ANI_MENU_DUPLICATE_BASELINE_PATH"] = str(baseline)
    environment["ANI_MENU_IMAGE_REFERENCE_PATH"] = str(baseline)

    commands = {
        "crawl": [
            uv, "run", "ani-menu", "pipeline-worker", "--stage", "crawl",
            "--content-types", ",".join(CONTENT_TYPES),
            "--scan-interval-seconds", str(max(300, args.source_refresh_minutes * 60)),
            "--batch-limit", str(args.batch_limit), "--stop-file", str(stop_file),
        ],
        "generate": [
            uv, "run", "ani-menu", "pipeline-worker", "--stage", "generate",
            "--stop-file", str(stop_file),
        ],
        "review": [
            uv, "run", "ani-menu", "pipeline-worker", "--stage", "review",
            "--stop-file", str(stop_file),
        ],
    }
    processes: dict[str, subprocess.Popen] = {}
    streams = {}

    def start_worker(stage: str) -> None:
        log_file = logs / f"{stage}.log"
        stream = log_file.open("ab")
        process = subprocess.Popen(
            commands[stage],
            cwd=project_root,
            env=environment.copy(),
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
        streams[stage] = stream
        processes[stage] = process
        _log(supervisor_log, f"WORKER_STARTED stage={stage} pid={process.pid} log={log_file}")

    for stage in WORKER_STAGES:
        start_worker(stage)

    try:
        while not stop_file.exists():
            time.sleep(2)
            for stage, process in list(processes.items()):
                exit_code = process.poll()
                if exit_code is None:
                    continue
                _log(
                    supervisor_log,
                    f"WORKER_EXITED stage={stage} code={exit_code}; restarting while STOP is absent",
                )
                streams.pop(stage).close()
                del processes[stage]
                if not stop_file.exists():
                    start_worker(stage)
    except KeyboardInterrupt:
        _log(supervisor_log, "INTERRUPTED; create STOP marker for graceful worker shutdown")
        return 130

    _log(supervisor_log, "STOP marker observed; waiting for workers to finish current items")
    while processes:
        for stage, process in list(processes.items()):
            exit_code = process.poll()
            if exit_code is None:
                continue
            _log(supervisor_log, f"WORKER_STOPPED stage={stage} code={exit_code}")
            streams.pop(stage).close()
            del processes[stage]
        if processes:
            time.sleep(1)
    _log(supervisor_log, "ALL_WORKERS_STOPPED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
