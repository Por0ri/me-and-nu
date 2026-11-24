"""Keep newly collected reusable reference facts embedded in the vector sidecar."""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPDATE_SCRIPT = PROJECT_ROOT / "scripts" / "update_vector_evidence_index.py"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--vector-db", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--stop-file", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.interval_seconds < 60:
        raise SystemExit("--interval-seconds must be at least 60")
    source_db = args.source_db.resolve()
    vector_db = args.vector_db.resolve()
    if not source_db.is_file() or not vector_db.is_file():
        raise SystemExit("Existing source DB and completed vector sidecar are required")

    stopping = False

    def request_stop(*_args) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    command = [
        sys.executable, str(UPDATE_SCRIPT),
        "--source-db", str(source_db),
        "--index-db", str(vector_db),
    ]
    print("Starting incremental RAG vector indexer; stop with Ctrl+C.", flush=True)
    while not stopping:
        if args.stop_file is not None and args.stop_file.resolve().exists():
            break
        result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        if result.returncode:
            print(
                f"RAG vector index update failed with exit code {result.returncode}; retrying later.",
                file=sys.stderr, flush=True,
            )
        deadline = time.monotonic() + args.interval_seconds
        while not stopping and time.monotonic() < deadline:
            if args.stop_file is not None and args.stop_file.resolve().exists():
                stopping = True
                break
            time.sleep(min(1.0, deadline - time.monotonic()))
    print("RAG vector indexer stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
