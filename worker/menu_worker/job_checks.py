"""Real broker/worker smoke check, with fixture requests and zero LLM calls."""
import os
from pathlib import Path
import time
from uuid import uuid4

from menu_worker.job_artifacts import validate_artifacts
from menu_worker.job_contract import JobInput
from menu_worker.jobs import job_route, send_delivery


def smoke_input(kind, scenario, key):
    if kind not in {"fake", "anime"}:
        raise ValueError("Probe source requires a probe queue")
    if kind == "anime":
        # Synthetic article path on the catalog's One Piece source, never fetched.
        article = uuid4().int % 10**16
        return JobInput(key, "one_piece_official", f"https://one-piece.com/news/{article}/index.html", "goods", 8)
    return JobInput(key, "fixture", f"https://example.invalid/{scenario}/{key}", "news")


def smoke(store, timeout=60, *, kind="fake"):
    if kind not in {"fake", "anime"}:
        raise ValueError("Smoke checks require a probe queue")
    namespace = "smoke-" + uuid4().hex
    expected = {"success": ("succeeded", "ready_for_staging"), "held": ("succeeded", "held"),
                "skipped": ("succeeded", "skipped"), "fail": ("failed", None)}
    if kind == "anime":
        expected = {"success": ("succeeded", "routing_verified")}
    app, _, _ = job_route(kind)
    node_prefix = "anime@" if kind == "anime" else "content@"
    root = Path(os.environ["CONTENT_JOB_ARTIFACT_ROOT"])
    jobs = {}
    for scenario in expected:
        request = smoke_input(kind, scenario, f"{namespace}-{scenario}")
        job = store.submit(request, job_kind=kind)
        duplicate = store.submit(request, job_kind=kind)
        if duplicate["job_id"] != job["job_id"] or duplicate["created"]:
            raise RuntimeError("Duplicate request was not deduplicated")
        jobs[scenario] = job["job_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = {scenario: store.get(job_id) for scenario, job_id in jobs.items()}
        if all((row["execution_status"], row["outcome"]) == expected[scenario]
               for scenario, row in rows.items()):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("Fake jobs did not reach their expected outcomes")
    # Publish a second delivery after completion; this must not start generation.
    delivery = send_delivery(jobs["success"], kind)
    replay = delivery.get(timeout=timeout)
    if replay.get("processed") is not False:
        raise RuntimeError("Duplicate delivery ran generation")
    report = {}
    for scenario, job_id in jobs.items():
        row = store.get(job_id)
        if row["attempt_count"] != 1:
            raise RuntimeError("Unexpected generation attempt count")
        receipt = app.AsyncResult(row["attempts"][0]["celery_task_id"]).get(timeout=timeout)
        if (row["job_kind"] != kind or not receipt["processed"] or receipt["worker_kind"] != kind
                or not receipt["worker"].startswith(node_prefix)):
            raise RuntimeError("Unexpected worker for job")
        if row["execution_status"] == "succeeded":
            evidence = validate_artifacts(root, row)
            if any(evidence[key] != row[key] for key in evidence):
                raise RuntimeError("Stored artifact and job result differ")
        report[scenario] = {"job_id": job_id, "execution_status": row["execution_status"],
                            "outcome": row["outcome"], "worker": receipt["worker"]}
        if kind == "anime":
            report[scenario].update(source_id=row["source_id"], menu_work_id=row["menu_work_id"])
    return {f"{kind}_jobs": report, "duplicate_request": "deduplicated", "duplicate_delivery": "ignored", "llm_calls": 0}
