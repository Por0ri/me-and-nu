"""Real Redis/Celery/PG cover-history admission, with zero model calls.

Run sequentially in the disposable internal test project, before suites that
leave jobs behind. Requires empty menu_content_jobs_test on jobs-db, the new
generation image (installed Agent loader), and generation-redis. No key, Beat,
service DB, existing generation broker, or network access is required.
"""
from contextlib import ExitStack
import json
import multiprocessing
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_retry_continuation import isolate_transport, next_completion, stop_worker

TEST_DATABASE = "menu_content_jobs_test"
KIND = "anime_generate"


def request(run_id, name):
    return {"idempotency_key": f"cover-delivery-{run_id}-{name}", "source_id": "one_piece_official",
            "source_url": f"https://one-piece.com/news/{uuid4().int}/index.html",
            "content_type": "goods", "menu_work_id": 8}


def rejected_cover_result(root, job):
    """Produce a local sealed rejected result, not an accepted service bundle."""
    from menu_worker import real_generation as real
    from menu_worker.job_artifacts import artifact_directory
    folder = artifact_directory(root, job, create=True)
    cover = {"id": "previous-cover", "url": "https://one-piece.com/images/fixture-cover.jpg",
             "source_url": job["canonical_url"], "fingerprint": "a" * 64,
             "storage_key": "fixture-cover", "width": 1200, "height": 1200,
             "download_status": "downloaded", "official": True,
             "rights_status": "official_public_risk_accepted",
             "rights_evidence_url": job["canonical_url"], "rights_checked_at": "2026-11-16T00:00:00Z",
             "attribution_text": "Offline source fixture"}
    package = {"request_id": "crawl-cover-rejected-" + job["job_id"], "content_type": "goods",
               "menu_work_ids": [8], "sources": [{"id": "one_piece_official", "url": job["canonical_url"]}],
               "images": [cover], "cards": [{"page": 1, "role": "cover", "image_id": cover["id"],
                                              "visual_mode": "source_image"}]}
    result = {"status": "rejected_archived", "package": package, "editorial_decision": "rejected",
              "expected_work_request": real._request(job), "usage_records": [],
              "qa_report": [{"code": "OFFLINE_EXPECTED_REJECTION", "severity": "blocker"}]}
    snapshot = job["_cover_history_snapshot"]
    (folder / "cover-history.json").write_bytes(snapshot)
    (folder / "launch.json").write_bytes(real._dump({**real._identity(job), "cover_history_sha256": real.sha256(snapshot)}))
    (folder / "result.json").write_bytes(real._dump(result))
    (folder / "exit.json").write_text('{"returncode":0}', encoding="utf-8")
    real.validate_real_artifacts(root, job)


def worker_process(run_id, artifacts, events, ready, stop):
    from celery import current_task
    from celery.contrib.testing.worker import start_worker
    from celery.signals import task_postrun
    from menu_worker import job_runner, job_store
    from menu_worker.jobs import job_route
    from test_staging_export import seal_ready_attempt

    store = job_store.JobStore(os.environ["CONTENT_JOB_TEST_DATABASE_URL"], expected_database=TEST_DATABASE)
    app, _, queue = job_route(KIND)
    isolate_transport(app, run_id)
    os.environ.update(CONTENT_JOB_KIND=KIND, CONTENT_JOB_ARTIFACT_ROOT=artifacts)

    def producer(root, job, pulse):
        pulse()
        snapshot = json.loads(job["_cover_history_snapshot"])
        assert snapshot["consumer"] == {"job_id": job["job_id"], "generation_attempt_id": job["generation_attempt_id"]}
        name = job["idempotency_key"].rsplit("-", 1)[1]
        if name in {"busy", "rejected"}:
            assert snapshot["entries"] == [], snapshot
        elif name == "next":
            entry, = snapshot["entries"]
            previous = store.get(entry["origin"]["job_id"])
            assert previous["outcome"] == "rejected" and previous["producer_status"] == "rejected_archived"
            assert entry["origin"] == {"job_id": previous["job_id"],
                "generation_attempt_id": previous["generation_attempt_id"], "result_sha256": previous["result_sha256"]}
            assert entry["request_id"] == "crawl-cover-rejected-" + previous["job_id"]
            assert entry["image"]["fingerprint"] == "a" * 64
        else:
            raise AssertionError("Unexpected fixture request")
        events.put({"event": "producer", "job_id": job["job_id"], "name": name,
                    "history_entries": snapshot["entries"], "pid": os.getpid(),
                    "worker": current_task.request.hostname})
        if name == "rejected":
            rejected_cover_result(root, job)
        else:
            # Existing editorial-graphic fixture: no cover source image enters history.
            seal_ready_attempt(root, job)

    def capture(sender, retval, state, task_id, **_):
        events.put({"event": "completed", "task": sender.name, "task_id": task_id,
                    "state": state, "pid": os.getpid(), "worker": sender.request.hostname,
                    "result": retval if isinstance(retval, dict) else {"exception_type": type(retval).__name__}})

    with ExitStack() as stack:
        stack.enter_context(patch.object(job_store, "JobStore", new=lambda: store))
        # Only paid-generation preflight/producer are substituted. History query,
        # advisory locks, snapshot builder, installed Agent loader and sealing run.
        stack.enter_context(patch.object(job_runner, "preflight_real", new=lambda: None))
        stack.enter_context(patch.object(job_runner, "generate_real", new=producer))
        task_postrun.connect(capture, weak=False)
        stack.callback(task_postrun.disconnect, capture)
        stack.enter_context(start_worker(app, pool="solo", concurrency=1, queues=(queue,),
            hostname=f"cover-delivery-{run_id}@isolated", perform_ping_check=False,
            loglevel="ERROR", shutdown_timeout=15))
        ready.set()
        assert stop.wait(180), "Parent did not finish the delivery check"


def main():
    import psycopg
    from menu_worker.job_store import JobStore, validate_database_url
    from menu_worker.jobs import dispatch_once, job_route, send_delivery

    forbidden = ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "CONTENT_JOB_DATABASE_URL", "DATABASE_URL",
                 "OPENAI_API_KEY", "LLM_KEY", "ANIME_LLM_KEY_FILE", "ANIME_LLM_KEY_HOST_FILE",
                 "ANIME_DISCOVERY_ROUTES", "ANIME_COVER_HISTORY_BOOTSTRAP_FILE", "ANIME_COVER_HISTORY_BOOTSTRAP_SHA256")
    if any(os.environ.get(name) for name in forbidden):
        raise RuntimeError("Use fresh isolated default brokers without provider keys, service DB or bootstrap history")
    if Path("/run/secrets/anime_llm_key").exists() or Path("/opt/ani-agent/.env").exists():
        raise RuntimeError("Do not mount provider secrets or Agent .env")
    dsn = os.environ["CONTENT_JOB_TEST_DATABASE_URL"]
    settings = validate_database_url(dsn, expected_database=TEST_DATABASE)
    if settings["host"] != "jobs-db":
        raise RuntimeError("Only the disposable jobs-db service is accepted")
    store = JobStore(dsn, expected_database=TEST_DATABASE)
    store.initialize()
    with psycopg.connect(dsn) as db:
        if db.execute("SELECT count(*) FROM content_generation_job").fetchone()[0]:
            raise RuntimeError("Use an empty test DB and run sequentially before retry-continuation")

    run_id = uuid4().hex
    app, task_name, _ = job_route(KIND)
    isolate_transport(app, run_id)
    mp = multiprocessing.get_context("spawn")
    events, ready, stop, released = mp.Queue(), mp.Event(), mp.Event(), mp.Event()
    owned, producer_events, receipts = [], [], []

    def completion(job_id):
        event = next_completion(events, producer_events)
        assert event["task"] == task_name and event["state"] == "SUCCESS", event
        assert event["result"]["job_id"] == job_id and event["result"]["worker_kind"] == KIND, event
        receipts.append(event)
        return event

    with TemporaryDirectory(prefix="cover-delivery-") as temporary, ExitStack() as stack:
        worker = mp.Process(target=worker_process, args=(run_id, temporary, events, ready, stop))
        worker.start()
        stack.callback(stop_worker, worker, stop, released)
        assert ready.wait(25), "Generation fixture worker failed to start"
        busy_id = store.submit(request(run_id, "busy"), job_kind=KIND)["job_id"]
        owned.append(busy_id)
        with store.generation_history_guard() as held:
            assert held is not None
            held()
            assert store.publish_pending(busy_id, lambda jid: send_delivery(jid, KIND).id, expected_kind=KIND)
            busy = completion(busy_id)
            assert busy["result"]["processed"] is False and busy["result"]["reason"] == "generation_history_busy"
            waiting = store.get(busy_id)
            assert waiting["execution_status"] == "queued" and waiting["attempt_count"] == 0
            assert waiting["generation_started_at"] is None and waiting["attempts"] == []
            assert waiting["last_dispatched_at"] is None and waiting["last_task_id"] == busy["task_id"]
            assert waiting["dispatch_count"] == 1 and not producer_events
        delivery = dispatch_once(store, expected_kind=KIND)
        assert delivery["published"] == 1 and delivery["failed"] == 0, delivery
        resumed = completion(busy_id)
        assert resumed["result"]["processed"] is True and resumed["result"]["execution_status"] == "succeeded"
        assert resumed["task_id"] != busy["task_id"]
        completed = store.get(busy_id)
        assert completed["attempt_count"] == 1 and completed["dispatch_count"] == 2
        assert completed["outcome"] == "ready_for_staging"
        for name, expected_outcome in (("rejected", "rejected"), ("next", "ready_for_staging")):
            job_id = store.submit(request(run_id, name), job_kind=KIND)["job_id"]
            owned.append(job_id)
            report = dispatch_once(store, expected_kind=KIND)
            assert report["published"] == 1 and report["failed"] == 0, report
            event = completion(job_id)
            assert event["result"]["processed"] is True and event["result"]["execution_status"] == "succeeded"
            row = store.get(job_id)
            assert row["outcome"] == expected_outcome and row["attempt_count"] == 1 and row["dispatch_count"] == 1
        assert [event["name"] for event in producer_events] == ["busy", "rejected", "next"]
        assert producer_events[-1]["history_entries"][0]["origin"]["job_id"] == owned[1]
        assert {event["pid"] for event in receipts + producer_events} == {worker.pid}
        assert dispatch_once(store, expected_kind=KIND)["published"] == 0
    assert worker.exitcode == 0, worker.exitcode
    events.close()
    events.join_thread()
    with psycopg.connect(dsn) as db:
        for job_id in owned:
            db.execute("DELETE FROM content_generation_job WHERE job_id=%s", (job_id,))
    print(json.dumps({"passed": True, "jobs": 3, "busy_receipt": "requeued_without_attempt",
        "busy_dispatch_count": 2, "busy_attempt_count": 1, "rejected_cover_forwarded": True,
        "same_worker": True, "owned_records_cleaned": len(owned), "llm_calls": 0,
        "service_database_changed": False}), flush=True)


if __name__ == "__main__":
    main()
