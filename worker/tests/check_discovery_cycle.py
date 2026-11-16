"""Exercise three real Beat ticks with dedicated Redis brokers and the PostgreSQL test DB.

Run inside a disposable internal network with jobs-db, redis-tasks and generation-redis.
Requires CONTENT_JOB_TEST_DATABASE_URL pointing at a disposable menu_content_jobs_test.
Only the HTTP responses and external generation producer are substituted.
"""
from contextlib import ExitStack
import json
import multiprocessing
import os
from pathlib import Path
import runpy
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run_agent_with_fixture_pages(url, cli_args):
    """Start the normal Agent bridge entrypoint with local HTTP responses installed."""
    from check_discovery_agent import OUTLET, RSS
    import httpx

    def read_page(address, source, timeout):
        if address == OUTLET:
            return f'<article><a href="{url}">公式発表</a></article>', address
        if address == url:
            return '<html><head></head></html>', address
        raise AssertionError("Unexpected fixture URL")

    with (patch("ani_menu_agent.discovery._get_with_backoff", return_value=httpx.Response(200, text=RSS)),
          patch("ani_menu_agent.sources.page_collector._get_html", side_effect=read_page)):
        sys.argv = ["menu_worker.discovery_agent", *cli_args]
        runpy.run_module("menu_worker.discovery_agent", run_name="__main__", alter_sys=True)


def isolate_transport(app, run_id):
    app.conf.broker_transport_options = {
        **app.conf.broker_transport_options, "global_keyprefix": f"test-{run_id}:",
    }


def worker_process(role, run_id, urls, root, events, ready, stop):
    from celery.contrib.testing.worker import start_worker
    from celery.signals import task_postrun
    from menu_worker import discovery, discovery_agent
    from menu_worker.discovery_app import app as discovery_app
    from menu_worker.generation_app import app as generation_app
    from menu_worker.job_store import JobStore

    store = JobStore(os.environ["CONTENT_JOB_TEST_DATABASE_URL"], expected_database="menu_content_jobs_test")
    tick = 0
    run_subprocess = subprocess.run

    def launch_fixture_agent(command, **kwargs):
        nonlocal tick
        assert command[3:5] == ["-m", "menu_worker.discovery_agent"]
        assert "OPENAI_API_KEY" not in kwargs["env"] and "CONTENT_JOB_DATABASE_URL" not in kwargs["env"]
        url = urls[0 if tick < 2 else 1] + ("?ref=homepage" if tick == 1 else "")
        tick += 1
        return run_subprocess([*command[:3], str(Path(__file__).resolve()), "--fixture-http", url, *command[5:]],
                              **kwargs)

    def offline_producer(artifact_root, job, pulse):
        from test_staging_export import seal_ready_attempt
        pulse()
        seal_ready_attempt(artifact_root, job)

    def capture(sender, retval, state, **_):
        events.put((sender.name, state, retval))

    with ExitStack() as context:
        if role == "discovery":
            app, queue = discovery_app, "anime-discovery"
            context.enter_context(patch.object(discovery, "JobStore", return_value=store))
            context.enter_context(patch.object(discovery_agent.subprocess, "run", side_effect=launch_fixture_agent))
        else:
            app, queue = generation_app, "anime-generation-live"
            os.environ.update(CONTENT_JOB_KIND="anime_generate", CONTENT_JOB_ARTIFACT_ROOT=root)
            context.enter_context(patch("menu_worker.job_store.JobStore", return_value=store))
            context.enter_context(patch("menu_worker.job_runner.preflight_real"))
            context.enter_context(patch("menu_worker.job_runner.generate_real", side_effect=offline_producer))
        isolate_transport(app, run_id)
        task_postrun.connect(capture, weak=False)
        context.enter_context(start_worker(app, pool="solo", concurrency=1, queues=(queue,),
                                          perform_ping_check=False, loglevel="ERROR", shutdown_timeout=15))
        ready.set()
        stop.wait(90)


def main():
    import psycopg
    from celery.beat import Scheduler, Service
    from menu_worker.job_store import JobStore, validate_database_url
    dsn = os.environ["CONTENT_JOB_TEST_DATABASE_URL"]
    validate_database_url(dsn, expected_database="menu_content_jobs_test")
    store = JobStore(dsn, expected_database="menu_content_jobs_test")
    store.initialize()
    with psycopg.connect(dsn) as conn:
        if conn.execute("SELECT count(*) FROM content_generation_job WHERE execution_status IN ('queued','running')").fetchone()[0]:
            raise RuntimeError("Use a disposable job test DB with no pending deliveries")
    if any(os.environ.get(key) for key in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "OPENAI_API_KEY")):
        raise RuntimeError("Run on the isolated test network with no broker overrides or provider key")

    source = "prtimes_rss"
    urls = [f"https://one-piece.com/news/{uuid4().int}/index.html" for _ in range(2)]
    run_id = uuid4().hex
    from menu_worker.jobs import dispatch_once

    with ExitStack() as context:
        root = context.enter_context(TemporaryDirectory(prefix="discovery-cycle-"))
        context.enter_context(patch.dict(os.environ, {
            "ANIME_DISCOVERY_ROUTES": f"{source}:news", "ANIME_DISCOVERY_INTERVAL_SECONDS": "5",
        }))
        from menu_worker.app import app as beat_app
        from menu_worker.generation_app import app as generation_app

        isolate_transport(beat_app, run_id)
        isolate_transport(generation_app, run_id)
        mp = multiprocessing.get_context("spawn")
        stop = mp.Event()
        queues = {}
        for role in ("discovery", "generation"):
            ready, queues[role] = mp.Event(), mp.Queue()
            worker = mp.Process(target=worker_process, args=(role, run_id, urls, root, queues[role], ready, stop))
            worker.start()
            context.callback(worker.join, 20)
            context.callback(stop.set)
            assert ready.wait(20), f"{role} worker did not start"

        beat = Service(app=beat_app, scheduler_cls=Scheduler, max_interval=0.25)
        thread = Thread(target=beat.start, daemon=True)
        thread.start()
        try:
            for expected_new in (1, 0, 1):
                task, state, result = queues["discovery"].get(timeout=25)
                assert task == "content.anime_discover" and state == "SUCCESS", (task, state, result)
                assert result["submitted"] == expected_new and result["duplicates"] == 1 - expected_new, result
                delivery = dispatch_once(store, expected_kind="anime_generate")
                assert delivery["published"] == expected_new and delivery["failed"] == 0, delivery
                if expected_new:
                    task, state, result = queues["generation"].get(timeout=15)
                    assert task == "content.anime_generate" and state == "SUCCESS", (task, state, result)
                    assert result["processed"] and result["execution_status"] == "succeeded", result
        finally:
            beat.stop(wait=False)
            thread.join(timeout=5)

        with psycopg.connect(dsn) as conn:
            jobs = conn.execute("SELECT candidate_url, job_kind, outcome, attempt_count FROM content_generation_job"
                                " WHERE candidate_url=ANY(%s) ORDER BY candidate_url", (urls,)).fetchall()
            assert jobs == sorted((url, "anime_generate", "ready_for_staging", 1) for url in urls), jobs
        assert len(list(Path(root).glob("jobs/*/attempts/*/result.json"))) == 2
        print(json.dumps({"beat_ticks": 3, "new_jobs_per_tick": [1, 0, 1], "jobs": len(jobs),
                          "ready_for_staging": 2, "llm_calls": 0}))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--fixture-http"]:
        run_agent_with_fixture_pages(sys.argv[2], sys.argv[3:])
    else:
        main()
