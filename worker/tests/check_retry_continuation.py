"""Verify bounded in-task retries and next-job execution using real Celery/Redis/PG.

Run in a disposable internal Docker network with jobs-db, redis-tasks and
generation-redis. Set only CONTENT_JOB_TEST_DATABASE_URL to an empty dedicated
menu_content_jobs_test database. No provider keys, service DB, HTTP or Beat are
used. Expected producer failures are assertions to satisfy, not test failures.
The first unexpected state or timeout aborts the check and stops its workers.
"""
from contextlib import ExitStack
import json
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEST_DATABASE = "menu_content_jobs_test"
CASES = {
    "fake": (
        ("exhausted", "failed", 3, "fake_generation_failed", 3),
        ("transient", "succeeded", 3, "fake_generation_failed", 3),
    ),
    "anime_generate": (
        ("spawn_exhausted", "failed", 3, "agent_spawn_failed", 3),
        ("setup_transient", "succeeded", 3, "agent_attempt_setup_failed", 3),
        ("ambiguous", "needs_attention", 1, "agent_timeout", 1),
        ("invalid_secret", "failed", 1, "invalid_agent_secret", 0),
    ),
}


def isolate_transport(app, run_id):
    app.conf.broker_transport_options = {
        **app.conf.broker_transport_options, "global_keyprefix": f"retry-{run_id}:",
    }
    app.conf.result_backend_transport_options = {
        **app.conf.result_backend_transport_options, "global_keyprefix": f"retry-result-{run_id}:",
    }


def worker_process(kind, run_id, plans, root, events, ready, released, stop):
    from celery import current_task
    from celery.contrib.testing.worker import start_worker
    from celery.signals import task_postrun
    from menu_worker import job_runner, job_store
    from menu_worker.job_artifacts import FakeAgentError, generate_fake
    from menu_worker.jobs import job_route
    from menu_worker.real_generation import RealGenerationError
    from test_staging_export import seal_ready_attempt

    store = job_store.JobStore(os.environ["CONTENT_JOB_TEST_DATABASE_URL"], expected_database=TEST_DATABASE)
    app, _, queue = job_route(kind)
    isolate_transport(app, run_id)
    os.environ.update(CONTENT_JOB_KIND=kind, CONTENT_JOB_ARTIFACT_ROOT=root)
    invocations = {}

    def preflight():
        job_id = current_task.request.kwargs["job_id"]
        if plans[job_id] == "invalid_secret":
            raise RealGenerationError("invalid_agent_secret", before_launch=True)

    def producer(artifact_root, job, pulse):
        # Keep A in its first invocation until B and all later messages are queued.
        if not released.wait(20):
            raise AssertionError("Dispatcher did not release the fixture producer")
        pulse()
        job_id = job["job_id"]
        invocations[job_id] = invocations.get(job_id, 0) + 1
        count, plan = invocations[job_id], plans[job_id]
        events.put({"event": "producer", "job_id": job_id, "call": count,
                    "pid": os.getpid(), "worker": current_task.request.hostname})
        if plan == "exhausted" or (plan == "transient" and count < 3):
            raise FakeAgentError("Expected fixture generation failure")
        if plan == "spawn_exhausted":
            raise RealGenerationError("agent_spawn_failed", before_launch=True)
        if plan == "setup_transient" and count < 3:
            raise RealGenerationError("agent_attempt_setup_failed", before_launch=True)
        if plan == "ambiguous":
            raise RealGenerationError("agent_timeout")
        if kind == "fake":
            generate_fake(artifact_root, job, pulse)
        else:
            seal_ready_attempt(artifact_root, job)

    def capture(sender, retval, state, task_id, **_):
        result = retval if isinstance(retval, dict) else {"exception_type": type(retval).__name__}
        events.put({"event": "completed", "task": sender.name, "task_id": task_id,
                    "state": state, "result": result, "pid": os.getpid(),
                    "worker": sender.request.hostname})

    with ExitStack() as context:
        # Only inject the real store's test-DB factory; no DB method is mocked.
        context.enter_context(patch.object(job_store, "JobStore", new=lambda: store))
        context.enter_context(patch.object(job_runner, "preflight_real", new=preflight))
        target = "generate_fake" if kind == "fake" else "generate_real"
        context.enter_context(patch.object(job_runner, target, new=producer))
        task_postrun.connect(capture, weak=False)
        context.callback(task_postrun.disconnect, capture)
        context.enter_context(start_worker(app, pool="solo", concurrency=1, queues=(queue,),
                                          hostname=f"retry-{kind}-{run_id}@isolated",
                                          perform_ping_check=False, loglevel="ERROR", shutdown_timeout=15))
        ready.set()
        if not stop.wait(180):
            raise AssertionError("Parent did not finish the continuation check")


def stop_worker(worker, stop, released):
    released.set()
    stop.set()
    worker.join(20)
    if worker.is_alive():
        worker.terminate()
        worker.join(10)
    if worker.is_alive():
        worker.kill()
        worker.join(5)


def next_completion(events, producer_events):
    while True:
        try:
            event = events.get(timeout=30)
        except Empty:
            raise AssertionError("Worker did not complete the next queued task within 30 seconds") from None
        if event["event"] == "producer":
            producer_events.append(event)
        else:
            return event


def candidate(kind, run_id, name):
    key = f"retry-{run_id}-{name}"
    if kind == "fake":
        return {"idempotency_key": key, "source_id": "fixture", "content_type": "news",
                "source_url": f"https://example.invalid/success/{key}"}
    return {"idempotency_key": key, "source_id": "one_piece_official", "content_type": "goods",
            "menu_work_id": 8, "source_url": f"https://one-piece.com/news/{uuid4().int}/index.html"}


def assert_ledger(store, job_id, status, attempts, error_code=None):
    job = store.get(job_id)
    assert job["execution_status"] == status, (job_id, job["execution_status"], status)
    assert job["attempt_count"] == attempts, (job_id, job["attempt_count"], attempts)
    assert job["dispatch_count"] == 1, (job_id, job["dispatch_count"])
    history = job["attempts"]
    assert len(history) == attempts and all(row["mode"] == "generation" for row in history), job_id
    assert len({row["attempt_id"] for row in history}) == attempts, job_id
    assert {row["celery_task_id"] for row in history} == {job["last_task_id"]}, job_id
    assert [row["execution_status"] for row in history] == ["failed"] * (attempts - 1) + [status], job_id
    expected_errors = [error_code] * (attempts - 1) + [None if status == "succeeded" else error_code]
    assert [row["error_code"] for row in history] == expected_errors, job_id
    assert job["error_code"] == expected_errors[-1], job_id
    assert job["outcome"] == ("ready_for_staging" if status == "succeeded" else None), job_id
    return job


def check_kind(store, kind, root):
    from menu_worker.jobs import dispatch_once, job_route, send_delivery

    run_id = uuid4().hex
    plans, pairs, ordered_ids = {}, [], []
    for name, status, attempts, error, calls in CASES[kind]:
        a = store.submit(candidate(kind, run_id, name + "-a"), job_kind=kind)["job_id"]
        b = store.submit(candidate(kind, run_id, name + "-b"), job_kind=kind)["job_id"]
        plans.update({a: name, b: "success"})
        pairs.append((name, a, b, status, attempts, error, calls))
        ordered_ids.extend((a, b))
    # Committed insertion timestamps determine dispatcher order, before any worker runs.
    assert [row["job_id"] for row in store.list_pending(expected_kind=kind)] == ordered_ids
    app, task, _ = job_route(kind)
    isolate_transport(app, run_id)
    mp = multiprocessing.get_context("spawn")
    events, ready, released, stop = mp.Queue(), mp.Event(), mp.Event(), mp.Event()
    worker = mp.Process(target=worker_process, args=(kind, run_id, plans, str(root), events, ready, released, stop))
    producer_events, completed, reports = [], [], []
    with ExitStack() as context:
        worker.start()
        context.callback(stop_worker, worker, stop, released)
        assert ready.wait(25), f"{kind} worker failed to start"
        delivery = dispatch_once(store, expected_kind=kind)
        assert delivery["published"] == len(ordered_ids) and delivery["failed"] == 0, delivery
        released.set()
        for name, a, b, status, attempts, error, calls in pairs:
            for job_id, expected_status in ((a, status), (b, "succeeded")):
                event = next_completion(events, producer_events)
                result = event["result"]
                assert event["task"] == task and event["state"] == "SUCCESS", event
                assert result.get("job_id") == job_id and result.get("processed") is True, event
                assert result.get("execution_status") == expected_status, event
                assert result.get("worker_kind") == kind and result.get("worker") == event["worker"], event
                completed.append(event)
            a_job = assert_ledger(store, a, status, attempts, error)
            assert_ledger(store, b, "succeeded", 1)
            if kind == "anime_generate":
                if name in {"spawn_exhausted", "ambiguous"}:
                    assert store.retry_preflight(a, expected_kind=kind) is False, name
                if name in {"spawn_exhausted", "invalid_secret"}:
                    assert a_job["generation_started_at"] is None, name
                if name in {"spawn_exhausted", "setup_transient"}:
                    failed = [row for row in a_job["attempts"] if row["execution_status"] == "failed"]
                    assert all(row["generation_started_at"] is None for row in failed), name
                if name == "ambiguous":
                    assert a_job["generation_started_at"] is not None, name
            reports.append({"kind": kind, "scenario": name, "a_status": status,
                            "a_attempts": attempts, "b_status": "succeeded", "b_attempts": 1})
        expected_calls = [job_id for _, a, b, _, _, _, calls in pairs for job_id in [a] * calls + [b]]
        assert [event["job_id"] for event in producer_events] == expected_calls
        assert {event["pid"] for event in completed + producer_events} == {worker.pid}
        assert len({event["worker"] for event in completed + producer_events}) == 1
        assert worker.is_alive(), "Worker exited before duplicate deliveries"
        history_before = {job_id: store.get(job_id)["attempts"] for job_id in ordered_ids}
        producer_count = len(producer_events)
        # Redelivery of failed, ambiguous and successful jobs must never add a fourth attempt.
        for job_id in ordered_ids:
            replay = send_delivery(job_id, kind)
            event = next_completion(events, producer_events)
            assert event["task_id"] == replay.id and event["state"] == "SUCCESS", event
            assert event["result"].get("job_id") == job_id, event
            assert event["result"].get("processed") is False, event
            assert (event["pid"], event["worker"]) == (completed[0]["pid"], completed[0]["worker"]), event
            assert store.get(job_id)["attempts"] == history_before[job_id], job_id
            assert store.get(job_id)["dispatch_count"] == 1, job_id
        assert len(producer_events) == producer_count, "Duplicate delivery invoked a producer"
        no_more = dispatch_once(store, expected_kind=kind)
        assert no_more["published"] == 0 and no_more["failed"] == 0, no_more
        assert worker.is_alive(), "Worker exited after expected failures"
        for report in reports:
            report.update(worker=completed[0]["worker"], worker_pid=worker.pid,
                          duplicate_delivery="ignored", llm_calls=0)
            print(json.dumps(report), flush=True)
    assert worker.exitcode == 0, (kind, worker.exitcode)
    events.close()
    events.join_thread()
    return reports


def main():
    import psycopg
    from menu_worker.job_store import JobStore, validate_database_url

    forbidden = ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "CONTENT_JOB_DATABASE_URL", "DATABASE_URL",
                 "OPENAI_API_KEY", "ANIME_LLM_KEY_FILE", "ANIME_LLM_KEY_HOST_FILE", "ANIME_DISCOVERY_ROUTES")
    if any(os.environ.get(name) for name in forbidden):
        raise RuntimeError("Use isolated default brokers with no service DB, discovery route or provider key")
    if Path("/run/secrets/anime_llm_key").exists() or Path("/opt/ani-agent/.env").exists():
        raise RuntimeError("Do not mount provider keys or Agent environment files")
    dsn = os.environ["CONTENT_JOB_TEST_DATABASE_URL"]
    parameters = validate_database_url(dsn, expected_database=TEST_DATABASE)
    if parameters["host"] != "jobs-db":
        raise RuntimeError("Use the disposable internal network's jobs-db service")
    store = JobStore(dsn, expected_database=TEST_DATABASE)
    store.initialize()
    with psycopg.connect(dsn) as connection:
        if connection.execute("SELECT count(*) FROM content_generation_job").fetchone()[0]:
            raise RuntimeError("Use an empty disposable content-job test database")
    with TemporaryDirectory(prefix="retry-continuation-") as temporary:
        root = Path(temporary).resolve()
        reports = []
        for kind in CASES:
            reports.extend(check_kind(store, kind, root))
    print(json.dumps({"scenarios_passed": len(reports), "jobs": len(reports) * 2,
                      "fake_queue": "content-generation", "generation_queue": "anime-generation-live",
                      "llm_calls": 0, "service_database_changed": False}), flush=True)


if __name__ == "__main__":
    main()
