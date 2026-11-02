"""Local fake content queue CLI and durable DB-to-broker dispatcher."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

from menu_worker.job_contract import validate_input
from menu_worker.job_store import JobConflict, JobStore


def dispatch_once(store, publish=None) -> dict:
    if publish is None:
        from menu_worker.app import app

        def publish(job_id):
            return app.send_task("content.fake_generate", kwargs={"job_id": str(job_id)},
                                 queue="content-generation").id

    recovered = store.recover_expired()
    report = {"published": 0, "failed": 0, "recovered": recovered}
    for job in store.list_pending():
        try:
            task_id = publish(job["job_id"])
            store.record_dispatched(job["job_id"], task_id)
            report["published"] += 1
        except Exception:
            # The committed queued row is the retry source; publication is not completion.
            report["failed"] += 1
    return report


def public_job(job):
    # Claim tokens are internal fencing data, not needed by CLI clients.
    if isinstance(job, dict):
        return {key: public_job(value) for key, value in job.items() if key != "claim_token"}
    if isinstance(job, list):
        return [public_job(value) for value in job]
    return job


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    commands.add_parser("smoke")
    submit = commands.add_parser("submit")
    submit.add_argument("--idempotency-key", required=True)
    submit.add_argument("--source", default="fixture")
    submit.add_argument("--url", required=True)
    submit.add_argument("--content-type", default="news")
    for name in ("show", "cancel", "recover"):
        commands.add_parser(name).add_argument("--job-id", required=True)
    dispatch = commands.add_parser("dispatch")
    dispatch.add_argument("--loop", action="store_true")
    args = parser.parse_args(argv)
    try:
        store = JobStore()
        if args.command == "init-db":
            store.initialize()
            result = {"schema": "content-jobs-v1", "initialized": True}
        elif args.command == "smoke":
            from menu_worker.job_checks import smoke
            result = smoke(store)
        elif args.command == "submit":
            request = validate_input({"idempotency_key": args.idempotency_key, "source_id": args.source,
                                      "source_url": args.url, "content_type": args.content_type})
            result = store.submit(request)
        elif args.command == "show":
            result = store.get(args.job_id)
            if result is None:
                print(json.dumps({"error": "job_not_found"}), file=sys.stderr)
                return 1
        elif args.command == "cancel":
            result = {"job_id": args.job_id, "cancelled": store.cancel(args.job_id)}
        elif args.command == "recover":
            from menu_worker.job_runner import run_job
            store.recover_expired()
            result = run_job(store, Path(os.environ["CONTENT_JOB_ARTIFACT_ROOT"]), args.job_id, recover=True)
        else:
            while True:
                result = dispatch_once(store)
                if not args.loop:
                    break
                Path("/tmp/job-dispatcher.health").write_text(datetime.now(timezone.utc).isoformat())
                if result["published"] or result["failed"]:
                    print(json.dumps(result, default=str), flush=True)
                time.sleep(2)
        print(json.dumps(public_job(result), ensure_ascii=False, default=str))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        # Never emit raw database/provider errors, DSNs, or environment values.
        result = {"error": "content_job_command_failed", "category": type(error).__name__}
        if isinstance(error, JobConflict):
            result["error"] = error.code
            result["existing_job_id"] = str(error.existing_job_id)
        print(json.dumps(result), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
