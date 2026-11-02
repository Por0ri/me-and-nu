"""Real broker/worker smoke check, with fixture requests and zero LLM calls."""
import time
from uuid import uuid4

from menu_worker.app import app
from menu_worker.job_contract import JobInput


def smoke(store, timeout=60):
    namespace = "smoke-" + uuid4().hex
    expected = {"success": ("succeeded", "ready_for_staging"), "held": ("succeeded", "held"),
                "skipped": ("succeeded", "skipped"), "fail": ("failed", None)}
    jobs = {}
    for scenario in expected:
        request = JobInput(f"{namespace}-{scenario}", "fixture",
                           f"https://example.invalid/{scenario}/{namespace}", "news")
        job = store.submit(request)
        duplicate = store.submit(request)
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
    delivery = app.send_task("content.fake_generate", kwargs={"job_id": jobs["success"]}, queue="content-generation")
    replay = delivery.get(timeout=timeout)
    if replay.get("processed") is not False:
        raise RuntimeError("Duplicate delivery ran generation")
    report = {}
    for scenario, job_id in jobs.items():
        row = store.get(job_id)
        if row["attempt_count"] != 1:
            raise RuntimeError("Unexpected generation attempt count")
        report[scenario] = {"job_id": job_id, "execution_status": row["execution_status"], "outcome": row["outcome"]}
    return {"fake_jobs": report, "duplicate_request": "deduplicated", "duplicate_delivery": "ignored", "llm_calls": 0}
