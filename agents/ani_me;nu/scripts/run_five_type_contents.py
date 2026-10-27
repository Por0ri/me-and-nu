"""Run one bounded type-crawler batch until five new contents pass review."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "generation_200" / "ani_menu_generation_200.sqlite3"
BASELINE = ROOT / "data" / "ani_menu_local.sqlite3"
RUN_ROOT = ROOT / "output" / "generation_200" / "type_runs"
LOG = RUN_ROOT / "worker.log"
TARGET = 5
PER_TYPE_LIMIT = 5
TYPE_ORDER = (
    "event",
    "goods",
    "news",
    "review",
    "music",
    "humor",
    "character",
    "curation",
    "guide",
    "people",
    "behind",
)


def log(message: str) -> None:
    line = f"{datetime.now(UTC).isoformat()} {message}"
    print(line, flush=True)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def main() -> int:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    if not DB.exists() or not BASELINE.exists():
        log(f"STOP missing database: generation={DB.exists()} baseline={BASELINE.exists()}")
        return 2

    env = os.environ.copy()
    env.update(
        {
            "ANI_MENU_DATABASE_BACKEND": "sqlite",
            "ANI_MENU_DATABASE_PATH": str(DB),
            "ANI_MENU_DUPLICATE_BASELINE_PATH": str(BASELINE),
        }
    )
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = RUN_ROOT / batch_id
    passed = 0
    log(f"START target={TARGET} types={','.join(TYPE_ORDER)} db={DB}")

    for content_type in TYPE_ORDER:
        if passed >= TARGET:
            break
        remaining = TARGET - passed
        command = [
            "uv",
            "run",
            "ani-menu",
            "crawl-type",
            "--content-type",
            content_type,
            "--limit",
            str(PER_TYPE_LIMIT),
            "--target-passed",
            str(remaining),
            "--region-policy",
            "mixed",
            "--output-dir",
            str(output_dir),
        ]
        log(f"TYPE_START type={content_type} remaining={remaining}")
        try:
            with LOG.open("a", encoding="utf-8") as stream:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                return_code = process.wait(timeout=45 * 60)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            log(f"TYPE_TIMEOUT type={content_type}")
            continue

        log(f"TYPE_EXIT type={content_type} code={return_code}")

        for summary_path in sorted(output_dir.glob(f"{content_type}/**/run_summary.json")):
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            outcomes = summary.get("outcomes", [])
            successes = sum(item.get("status") == "ready_for_feed" for item in outcomes)
            log(
                f"TYPE_RESULT type={content_type} passed={successes} "
                f"attempted={len(outcomes)} summary={summary_path}"
            )
            passed += successes

        if return_code != 0:
            try:
                tail = LOG.read_text(encoding="utf-8")[-2500:]
            except OSError:
                tail = ""
            log(f"TYPE_ERROR type={content_type} tail={tail!r}")
        time.sleep(1)

    status = "COMPLETE" if passed >= TARGET else "INCOMPLETE"
    log(f"{status} new_passed={passed} target={TARGET} output={output_dir}")
    return 0 if passed >= TARGET else 1


if __name__ == "__main__":
    raise SystemExit(main())
