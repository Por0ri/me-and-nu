"""Local content queue CLI and kind-scoped DB-to-broker dispatcher."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

from menu_worker.anime_catalog import AnimeInputError
from menu_worker.job_contract import JobInputError
from menu_worker.job_store import JobConflict, JobStore
from menu_worker.real_generation import RealGenerationError


def job_route(kind):
    if kind == "anime_generate":
        from menu_worker.generation_app import app
        task, queue = "content.anime_generate", "anime-generation-live"
    elif kind == "anime":
        from menu_worker.anime_app import app
        task, queue = "content.anime_probe", "anime-generation"
    elif kind == "fake":
        from menu_worker.app import app
        task, queue = "content.fake_generate", "content-generation"
    else:
        raise ValueError("Unknown dispatcher kind")
    return app, task, queue


def send_delivery(job_id, kind):
    app, task, queue = job_route(kind)
    return app.send_task(task, kwargs={"job_id": str(job_id)}, queue=queue)


def dispatch_once(store, publish=None, *, expected_kind="fake") -> dict:
    if publish is None:
        def publish(job_id):
            return send_delivery(job_id, expected_kind).id

    recovered = store.recover_expired(expected_kind=expected_kind)
    report = {"published": 0, "failed": 0, "recovered": recovered}
    for job in store.list_pending(expected_kind=expected_kind):
        try:
            task_id = publish(job["job_id"])
            store.record_dispatched(job["job_id"], task_id, expected_kind=expected_kind)
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
    submit.add_argument("--menu-work-id", type=int, help="Existing catalog work ID for an anime source")
    submit.add_argument("--kind", choices=("fake", "anime"), help="Default: the current dispatcher's kind")
    generate = commands.add_parser("generate", help="Request one real, billable run in the generation profile")
    generate.add_argument("--idempotency-key", required=True)
    generate.add_argument("--source", required=True)
    generate.add_argument("--url", required=True)
    generate.add_argument("--content-type", required=True)
    generate.add_argument("--menu-work-id", type=int, required=True)
    export = commands.add_parser("export-staging", help="Export a sealed ready_for_staging job for the carousel importer")
    export.add_argument("--job-id", required=True)
    export.add_argument("--output", type=Path, required=True, help="New bundle directory under an existing parent")
    for name in ("show", "cancel", "recover", "retry-preflight"):
        commands.add_parser(name).add_argument("--job-id", required=True)
    dispatch = commands.add_parser("dispatch")
    dispatch.add_argument("--loop", action="store_true")
    args = parser.parse_args(argv)
    try:
        store = JobStore()
        role = os.environ.get("CONTENT_JOB_KIND", "fake")
        if role not in {"fake", "anime", "anime_generate"}:
            raise ValueError("Invalid CONTENT_JOB_KIND")
        if args.command in {"generate", "retry-preflight", "export-staging"} and role != "anime_generate":
            raise JobInputError("Use the dedicated generation dispatcher", "generation_profile_required")
        if role == "anime_generate" and args.command in {"submit", "smoke"}:
            raise JobInputError("Live jobs require the generate command", "probe_command_not_available")
        if args.command == "init-db":
            schema = store.initialize()
            result = {"schema": f"content-jobs-v{schema['version']}", "initialized": True}
        elif args.command == "smoke":
            from menu_worker.job_checks import smoke
            result = smoke(store, kind=role)
        elif args.command in {"submit", "generate"}:
            request = {"idempotency_key": args.idempotency_key, "source_id": args.source,
                       "source_url": args.url, "content_type": args.content_type}
            if args.menu_work_id is not None:
                request["menu_work_id"] = args.menu_work_id
            kind = "anime_generate" if args.command == "generate" else args.kind or role
            result = store.submit(request, job_kind=kind)
        elif args.command == "export-staging":
            from menu_worker.staging_export import export_staging_bundle
            result = export_staging_bundle(Path(os.environ["CONTENT_JOB_ARTIFACT_ROOT"]),
                                           store.get(args.job_id), args.output)
        elif args.command == "show":
            result = store.get(args.job_id)
            if result is None:
                print(json.dumps({"error": "job_not_found"}), file=sys.stderr)
                return 1
        elif args.command == "cancel":
            result = {"job_id": args.job_id, "cancelled": store.cancel(args.job_id, expected_kind=role)}
        elif args.command == "retry-preflight":
            result = {"job_id": args.job_id, "requeued": store.retry_preflight(args.job_id, expected_kind=role)}
        elif args.command == "recover":
            from menu_worker.job_runner import run_job
            store.recover_expired(expected_kind=role)
            result = run_job(store, Path(os.environ["CONTENT_JOB_ARTIFACT_ROOT"]), args.job_id,
                             recover=True, expected_kind=role)
        else:
            while True:
                result = dispatch_once(store, expected_kind=role)
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
        if isinstance(error, (AnimeInputError, JobInputError, RealGenerationError)):
            result["error"] = error.code
        elif isinstance(error, JobConflict):
            result["error"] = error.code
            result["existing_job_id"] = str(error.existing_job_id)
        print(json.dumps(result), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
