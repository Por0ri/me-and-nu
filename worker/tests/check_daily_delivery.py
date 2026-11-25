"""Offline daily handoff: real PostgreSQL/Redis/Celery, synthetic producer only.

Requires an EMPTY disposable jobs-db/menu_content_jobs_test and an internal
Docker network. No keys, service DSNs, HTTP, Agent subprocess or LLM are used.
The optional /handoff output is unmistakably marked as an offline fixture;
its protocol-valid review fields are synthetic, never actual model evidence.
The real 40/20 policy is retained: one success is followed by a simulated
deadline, not by lowering production limits.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DATABASE = "menu_content_jobs_test"
KST = timezone(timedelta(hours=9))
FIXTURE_NOW = datetime(2026, 11, 24, 10, tzinfo=KST)
FIXTURE_DEADLINE = datetime(2026, 11, 24, 11, tzinfo=KST)


def require(condition, reason):
    if not condition:
        raise AssertionError(reason)


def guard_environment():
    require(os.getenv("DAILY_GENERATION_OFFLINE_TEST") == "isolated", "offline_opt_in_required")
    forbidden = ("OPENAI_API_KEY", "LLM_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
                 "DATABASE_URL", "SQLALCHEMY_DATABASE_URI", "SERVICE_DATABASE_URL", "CONTENT_JOB_DATABASE_URL")
    require(not any(os.getenv(name) for name in forbidden), "provider_or_service_environment_forbidden")
    require(not Path("/run/secrets/anime_llm_key").exists(), "provider_secret_mount_forbidden")
    from psycopg.conninfo import conninfo_to_dict
    dsn = os.environ["CONTENT_JOB_TEST_DATABASE_URL"]
    parameters = conninfo_to_dict(dsn)
    require(parameters.get("dbname") == TEST_DATABASE and parameters.get("host") == "jobs-db"
            and str(parameters.get("port", "5432")) == "5432", "dedicated_test_database_required")
    broker = os.getenv("DAILY_TEST_REDIS_URL", "redis://generation-redis:6379/0")
    parsed = urlsplit(broker)
    require(parsed.scheme == "redis" and parsed.hostname == "generation-redis" and parsed.port == 6379
            and parsed.path == "/0" and not parsed.query and not parsed.fragment
            and parsed.username is None and parsed.password is None, "dedicated_test_broker_required")
    return dsn, broker


def candidate(number, key=None):
    url = f"https://one-piece.com/news/{number}/index.html"
    result = {"source_id": "one_piece_official", "source_url": url, "canonical_url": url,
              "content_type": "goods", "menu_work_id": 8}
    if key is not None:
        result["idempotency_key"] = key
    return result


def seal_synthetic(root, job, *, ready):
    """Write protocol fixtures directly; never run generate_real or an Agent."""
    from PIL import Image
    from menu_worker import real_generation
    from menu_worker.job_artifacts import artifact_directory, sha256
    folder = artifact_directory(root, job, create=True)
    result = {"expected_work_request": real_generation._request(job),
              "status": "ready_for_feed" if ready else "content_type_mismatch_skipped",
              "usage_records": [{"stage": "review", "model": "offline-fixture-not-a-real-model",
                                 "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                 "estimated_cost_usd": "0.01000000"}],
              "usage_summary": {"llm_calls": 1, "estimated_cost_usd": "0.01000000", "total_tokens": 2}}
    if ready:
        request_id = "offline-daily-" + sha256(job["canonical_url"].encode())[:14]
        card_root = folder / "cards" / request_id
        card_root.mkdir(parents=True)
        box = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        cards = []
        for page in (1, 2):
            stream = BytesIO()
            Image.new("RGB", (1308, 1632), color=(page, page, page)).save(stream, "PNG")
            binary = stream.getvalue()
            (card_root / f"card_{page:02d}.png").write_bytes(binary)
            cards.append({"page": page, "role": "cover" if page == 1 else "ending", "eyebrow": "격리 테스트",
                "title_lines": [f"가짜 통합 검증 카드 {page}"], "body_sentences": ["실제 콘텐츠가 아닌 무과금 테스트입니다."],
                "visual_mode": "editorial_graphic", "rendered_image_key": sha256(binary), "fact_ids": ["offline-fact"],
                "layout": {"image_box": box, "copy_box": box, "text_overlay_box": box}})
        result.update(review_mode="llm", editorial_decision="approved_for_feed",
            quality_report={"decision": "pass_candidate", "hard_blockers": []}, qa_report=[],
            language_review={"offline_protocol_fixture": "not an actual model review"},
            package={"request_id": request_id, "content_type": "goods", "menu_work_ids": [8], "cards": cards,
                "detail_body": "격리 환경의 합성 상세 본문입니다. 실제 상품 정보나 모델 생성물이 아닙니다.",
                "feed_valid_until": "2026-11-26T10:00:00+09:00", "purge_at": "2026-12-24T10:00:00+09:00",
                "sources": [{"id": job["source_id"], "title": "OFFLINE SYNTHETIC SOURCE",
                             "url": job["canonical_url"], "role": "primary"}],
                "facts": [{"id": "offline-fact", "statement": "격리 통합 테스트의 합성 사실입니다.",
                           "source_ids": [job["source_id"]], "verified": True}],
                "detail_fact_ids": ["offline-fact"], "detail_source_ids": [job["source_id"]]})
    for name, value in (("result.json", result), ("launch.json", real_generation._identity(job)), ("exit.json", {"returncode": 0})):
        (folder / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    (folder / "agent.log").write_text("OFFLINE FIXTURE: Agent/LLM invocations = 0.\n", encoding="utf-8")
    return real_generation.validate_real_artifacts(root, job)


def complete_synthetic(store, root, job_id, *, ready, task_id=None):
    claimed = store.claim(job_id, task_id, expected_kind="anime_generate")
    if claimed is None:
        return {"job_id": job_id, "processed": False, "reason": "ignored"}
    require(store.mark_generation_started(job_id, claimed["claim_token"], expected_kind="anime_generate"), "claim_start_failed")
    evidence = seal_synthetic(root, claimed, ready=ready)
    require(store.complete(job_id, claimed["claim_token"], expected_kind="anime_generate",
                           execution_status="succeeded", **evidence), "fixture_completion_failed")
    return {"job_id": job_id, "processed": True, **evidence}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, help="Existing empty /handoff mount, preserved for isolated backend tests")
    args = parser.parse_args(argv)
    dsn, broker = guard_environment()  # Before imports that can initialize any client.
    import psycopg
    from celery import Celery
    from celery.contrib.testing.worker import start_worker
    from menu_worker import real_generation
    from menu_worker.bounded_generation import StopRun, audit_existing_jobs, database_lock, local_lock, read_json, write_json
    from menu_worker.bounded_resume import claim_path, digest
    from menu_worker.daily_generation import DailyController
    from menu_worker.daily_resume import activate_plan, build_plan, source_checkpoint, validate_child, verify_empty_generation_broker
    from menu_worker.job_artifacts import sha256
    from menu_worker.job_contract import validate_job_request
    from menu_worker.job_store import JobConflict, JobStore
    from menu_worker.staging_export import export_staging_bundle

    token = uuid4().hex
    preflight_app = Celery("daily-preflight-" + token, broker=broker)
    try:
        broker_preflight = verify_empty_generation_broker(preflight_app)
    finally:
        preflight_app.close()
    with psycopg.connect(dsn) as connection:
        require(connection.execute("SELECT current_database()").fetchone()[0] == TEST_DATABASE, "wrong_actual_database")
        require(connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0] == 0,
                "empty_disposable_database_required")
    store = JobStore(dsn, expected_database=TEST_DATABASE)
    store.initialize()
    with psycopg.connect(dsn) as connection:
        connection.execute("CREATE TABLE public.daily_offline_fixture (fixture_id text PRIMARY KEY, purpose text NOT NULL)")
        connection.execute("INSERT INTO public.daily_offline_fixture VALUES (%s,%s)", (token, "isolated-offline-daily-delivery"))

    with ExitStack() as context:
        if args.handoff is None:
            output = Path(context.enter_context(TemporaryDirectory(prefix="daily-offline-"))).resolve()
        else:
            output = args.handoff
            require(output == Path("/handoff") and output.is_dir() and output.resolve() == output and not any(output.iterdir()),
                    "empty_handoff_mount_required")
        write_json(output / "OFFLINE_TEST_ONLY.json", {"fixture_id": token, "actual_llm_calls": 0,
            "actual_cost_usd": "0", "protocol_review_and_cost_are_synthetic": True,
            "test_database": TEST_DATABASE, "policy_clock": FIXTURE_NOW.isoformat()})
        root, bundles, inbox, state_dir = (output / name for name in ("artifacts", "bundles", "inbox", "state"))
        for directory in (root, bundles, inbox, state_dir):
            directory.mkdir()
        parent, child = state_dir / "parent.json", state_dir / "daily.json"
        prefix = "beta-open-20261116"
        first_number = 990000000000000 + int(token[:10], 16)
        rows = []
        for index in range(4):
            fields = candidate(first_number + index, f"{prefix}:offline-{token}-{index}")
            submitted = store.submit(fields, job_kind="anime_generate")
            complete_synthetic(store, root, submitted["job_id"], ready=False, task_id="offline-legacy-" + str(index))
            job = store.get(submitted["job_id"])
            rows.append({"job_id": job["job_id"], "request": fields, "state": "complete", "counts_slot": True,
                "canonical_url": job["canonical_url"], "execution_status": "succeeded", "outcome": "skipped",
                "producer_status": "content_type_mismatch_skipped", "generation_attempt_id": job["generation_attempt_id"],
                "result_sha256": job["result_sha256"], "estimated_cost_usd": "0.01000000"})
        state = {"schema_version": 1, "status": "complete", "stop_reason": "new_job_slots_consumed",
            "records": rows, "seen": [row["canonical_url"] for row in rows], "scans": 1,
            "config": {"run_id": prefix, "source_id": "one_piece_official", "content_type": "goods",
                "baseline_count": 1, "baseline_estimated_cost_usd": "0.01178341", "max_new_jobs": 4,
                "total_budget_usd": "5.00", "deadline": "2026-11-16T17:00:00+09:00", "next_job_reserve_usd": "0.04",
                "job_timeout_seconds": 1500, "scan_interval_seconds": 60, "excluded_urls": [],
                "artifact_root": str(root), "bundle_root": str(bundles)}}
        write_json(parent, state)
        original = parent.read_bytes()
        old_jobs = {row["job_id"]: store.get(row["job_id"]) for row in rows}
        old_files = {path: sha256(path.read_bytes()) for job_id in old_jobs for path in (root / "jobs" / job_id).rglob("*") if path.is_file()}
        clock = [FIXTURE_NOW.timestamp()]
        plan = build_plan(source=parent, source_sha256=sha256(original), checkpoint=child,
            policy_date="2026-11-24", daily_budget_usd="0.50", deadline=FIXTURE_DEADLINE.isoformat(),
            delivery_root=inbox, now=clock[0])
        require(plan["daily_policy"]["max_attempts"] == 40 and plan["daily_policy"]["success_target"] == 20, "production_policy_changed")
        app = Celery("daily-offline-" + token, broker=broker, backend=broker)
        queue = "daily-offline-" + token
        app.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"],
            task_default_queue=queue, task_acks_late=True, worker_prefetch_multiplier=1,
            broker_transport_options={"global_keyprefix": token + ":"},
            result_backend_transport_options={"global_keyprefix": token + ":result:"})
        producer_calls = []

        @app.task(bind=True, name="offline.daily.synthetic")
        def synthetic(self, job_id):
            result = complete_synthetic(store, root, job_id, ready=True, task_id=self.request.id)
            if result["processed"]:
                producer_calls.append(job_id)
            return result

        def forbidden(*_args, **_kwargs):
            raise AssertionError("real_agent_or_provider_path_forbidden")

        context.enter_context(patch.object(real_generation, "generate_real", forbidden))
        context.enter_context(patch.object(real_generation, "preflight_real", forbidden))
        context.callback(app.close)
        context.enter_context(start_worker(app, pool="solo", concurrency=1, queues=(queue,),
            hostname="daily-offline@" + token, perform_ping_check=False, loglevel="ERROR", shutdown_timeout=15))
        publications, scans = [], []
        article = candidate(first_number + 10)

        def discover(*_args):
            scans.append(True)
            return {"articles": [article, dict(article)] if len(scans) == 1 else [], "discovered": 2 if len(scans) == 1 else 0}

        def publish(job_id):
            publications.append(job_id)
            return synthetic.apply_async(kwargs={"job_id": job_id}, queue=queue).id

        wall_deadline = time.monotonic() + 90

        def sleep(seconds):
            require(time.monotonic() < wall_deadline, "offline_worker_completion_timeout")
            if seconds >= 60:
                clock[0] = FIXTURE_DEADLINE.timestamp()
            else:
                time.sleep(0.05)
                clock[0] += 0.05

        def validate(fields):
            _, canonical, request_hash = validate_job_request(fields, "anime_generate")
            return canonical, request_hash

        reader = lambda job: real_generation.read_sealed_staging_result(root, job)
        with local_lock(parent), database_lock(store, prefix) as check:
            _, reservations = activate_plan(plan, store=store, read_result=reader, now=clock[0])

            def guard():
                check()
                validate_child(controller.state, source_checkpoint(parent, sha256(original)), plan)
                require(read_json(claim_path(parent))["plan_sha256"] == digest(plan), "successor_changed")
                audit_existing_jobs(store, controller.state)

            controller = DailyController(config=plan["config"], checkpoint=child, store=store,
                discover=discover, validate=validate, publish=publish, read_result=reader,
                export=lambda job_id, destination: export_staging_bundle(root, store, job_id, destination),
                reservations=reservations, lock_check=guard, now=lambda: clock[0], sleep=sleep)
            try:
                controller.run()
            except StopRun as error:
                require(str(error) == "deadline_reached", "unexpected_controller_stop")
            report = controller.summary()
            require(report["daily_attempts"] == report["daily_successes"] == report["daily_deliveries"] == 1, "daily_handoff_count_mismatch")
            require(report["status"] == "stopped" and report["stop_reason"] == "deadline_reached", "deadline_not_preserved")
            require(publications == producer_calls and len(publications) == 1, "duplicate_generation")
            new_id = publications[0]
            new_before = store.get(new_id)
            replay = synthetic.apply_async(kwargs={"job_id": new_id}, queue=queue).get(timeout=20, disable_sync_subtasks=False)
            require(replay == {"job_id": new_id, "processed": False, "reason": "ignored"}, "duplicate_delivery_processed")
            require(store.get(new_id) == new_before and len(producer_calls) == 1, "duplicate_delivery_changed_history")
            try:
                store.submit({**article, "idempotency_key": prefix + ":duplicate-" + token}, job_kind="anime_generate")
            except JobConflict as error:
                require(error.code == "candidate_exists" and str(error.existing_job_id) == new_id, "wrong_duplicate_identity")
            else:
                raise AssertionError("duplicate_candidate_was_not_rejected")
            envelope_before = {path: path.read_bytes() for path in inbox.glob("*.json")}
            controller.run()  # Same stopped day: reverify/reuse the envelope, never publish again.
            require(envelope_before == {path: path.read_bytes() for path in inbox.glob("*.json")}, "envelope_replay_changed")
            require(len(publications) == len(producer_calls) == 1, "stopped_day_republished")
        require(parent.read_bytes() == original, "parent_checkpoint_changed")
        require({job_id: store.get(job_id) for job_id in old_jobs} == old_jobs, "legacy_rows_changed")
        require(all(sha256(path.read_bytes()) == value for path, value in old_files.items()), "legacy_artifacts_changed")
        with store._connect() as connection:
            counts = {table: connection.execute(f"SELECT count(*) AS total FROM public.{table}").fetchone()["total"]
                      for table in ("content_generation_job", "content_generation_attempt", "content_staging_selection")}
        require(counts == {"content_generation_job": 5, "content_generation_attempt": 5, "content_staging_selection": 1},
                "unexpected_job_attempt_or_selection_count")
        require(len(list(inbox.glob("*.json"))) == 1, "unexpected_envelope_count")
        summary = {"status": "passed", "fixture_id": token, "actual_llm_calls": 0, "actual_cost_usd": "0",
            "protocol_usage_is_synthetic": True, "service_db_access": False, "legacy_jobs_preserved": 4,
            "new_jobs": 1, "producer_calls": 1, "duplicate_candidate": "deduplicated", "duplicate_delivery": "ignored",
            "envelopes": 1, "cards": 2, "controller": report, "plan_sha256": digest(plan),
            "fixture_now": FIXTURE_NOW.isoformat(), "bundle_root": str(bundles), "inbox": str(inbox),
            "broker_preflight": broker_preflight, "database_counts": counts}
        write_json(output / "verification-summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Connection and driver exception messages may contain credentials.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, AssertionError) else "inspect_offline_test"}), file=sys.stderr)
        raise SystemExit(1)
