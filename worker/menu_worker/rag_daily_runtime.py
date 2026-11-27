"""Opt-in composition for a shared daily ledger and a separate native RAG inbox.

No backend imports, provider calls, worker starts or service DB writes occur here.
Planning is local-only; publication additionally requires the reviewed runtime
configuration and the existing explicit --apply gate.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path

from menu_worker.bounded_generation import StopRun, money, result_cost
from menu_worker.bounded_resume import digest, publish_with_deadline, require
from menu_worker.rag_contract import RagRequest, canonical, validate_admission_receipt


def record_request(record):
    request = RagRequest.parse(record["request"]["rag_request"])
    require(record.get("canonical_url") == "urn:menu-rag:" + request.identity_key
            and record.get("rag_input_sha256") == request.input_sha256,
            "rag_record_identity_changed")
    validate_admission_receipt(record["rag_admission"], request)
    return request


def audit_rag_history(store, state, *, period_job_count):
    require(store is not None, "rag_history_verifier_required")
    with store.jobs._connect() as connection:
        rows = connection.execute("SELECT job_id,identity_key,input_sha256,admission_sha256 "
                                  "FROM public.content_rag_generation_job").fetchall()
    owned = {row["job_id"]: row for row in state["records"]
             if row.get("producer") == "ani_rag" and row.get("job_id")}
    require(len(rows) + period_job_count <= state["config"]["max_new_jobs"],
            "database_job_cap_already_exceeded")
    require(set(owned) == {str(row["job_id"]) for row in rows},
            "rag_database_history_not_owned_by_checkpoint")
    for row in rows:
        record = owned[str(row["job_id"])]
        request = record_request(record)
        require(row["identity_key"] == request.identity_key and row["input_sha256"] == request.input_sha256
                and row["admission_sha256"] == digest(record["rag_admission"]),
                "rag_database_history_changed")


def verify_rag_completion(record, store, read_result, *, state=None, artifact_root=None):
    require(store is not None and callable(read_result), "rag_history_verifier_required")
    request = record_request(record)
    job = store.get(record["job_id"])
    require(job is not None and job.get("request") == request.as_dict()
            and job.get("input_sha256") == request.input_sha256
            and job.get("admission_receipt") == record["rag_admission"], "rag_completed_job_changed")
    if record.get("execution_status") == "failed":
        from menu_worker.rag_preflight_failure import verify_zero_preflight_completion
        verify_zero_preflight_completion(state, record, job, artifact_root=artifact_root)
        if record.get("failure_recovery_receipt"):
            from menu_worker.rag_preflight_recovery import validate_recovery_record
            validate_recovery_record(record, store=store, artifact_root=artifact_root)
        return
    require(job.get("execution_status") == record.get("execution_status") == "succeeded"
            and not record.get("rag_cost_incomplete")
            and record.get("accounting_method") not in {"reserve", "unknown"},
            "rag_generation_requires_reconciliation")
    require(all(job.get(key) == record.get(key) for key in (
        "outcome", "producer_status", "result_sha256", "generation_attempt_id")),
        "rag_completed_evidence_changed")
    result = read_result(job)  # Separate native seals; never the period adapter.
    require(result.get("producer") == "ani_rag" and job.get("cost_complete") is True
            and result_cost(result) == money(record["estimated_cost_usd"]), "rag_completed_cost_changed")
    require("rag_usage_records" not in record or record["rag_usage_records"] == job.get("usage_records"),
            "rag_completed_usage_changed")
    if job["outcome"] == "ready_for_staging":
        require(record.get("rag_delivery") is not None, "rag_completed_delivery_missing")
        verify_delivery(record["rag_delivery"], job)


def _file(path, *, maximum):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path and path.is_file()
            and path.stat().st_size <= maximum, "unsafe_rag_delivery_file")
    return path, path.read_bytes()


def _envelope(artifact_root, job, daily_policy, rag_scope):
    from menu_worker.rag_tasks import _directory, read_sealed_result
    result = read_sealed_result(artifact_root, job)
    require(job.get("execution_status") == "succeeded" and result["outcome"] == "ready_for_staging"
            and job.get("cost_complete") is True
            and result.get("cost_accounting", {}).get("complete") is True, "rag_delivery_not_ready")
    result_cost(result)
    require(digest({"daily": daily_policy, "rag": rag_scope}) == job["admission_receipt"]["policy_sha256"]
            and daily_policy["policy_date"] == job["admission_receipt"]["policy_date"],
            "rag_delivery_policy_changed")
    directory = _directory(artifact_root, job)
    manifest, manifest_bytes = _file(directory / "manifest.json", maximum=16384)
    launch, launch_bytes = _file(directory / "launch.json", maximum=96 * 1024 * 1024)
    return {"schema_version": 1, "producer": "ani_rag", "job_id": job["job_id"],
        "generation_attempt_id": job["generation_attempt_id"], "result_sha256": job["result_sha256"],
        "input_sha256": job["input_sha256"], "admission_sha256": job["admission_sha256"],
        "native_result_path": str(directory / "result.json"), "native_result_sha256": job["result_sha256"],
        "manifest_path": str(manifest), "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "launch_path": str(launch), "launch_sha256": hashlib.sha256(launch_bytes).hexdigest(),
        "policy_date": job["admission_receipt"]["policy_date"],
        "policy_sha256": job["admission_receipt"]["policy_sha256"],
        "day_policy_sha256": digest(daily_policy), "daily_policy": daily_policy, "rag_scope": rag_scope}


def export_native_delivery(artifact_root, inbox, job, *, daily_policy, rag_scope):
    """Idempotent raw sealed delivery, deliberately outside the period inbox."""
    inbox = Path(inbox)
    require(inbox.is_absolute() and inbox.resolve() == inbox and inbox.is_dir(), "unsafe_rag_delivery_root")
    envelope = _envelope(artifact_root, job, daily_policy, rag_scope)
    destination = inbox / (job["job_id"] + ".json")
    data = canonical(envelope)
    if destination.exists():
        _, existing = _file(destination, maximum=128 * 1024)
        require(existing == data, "rag_delivery_changed")
    else:
        with destination.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    return {key: job[key] for key in ("job_id", "generation_attempt_id", "result_sha256")} | {
        "producer": "ani_rag", "envelope_path": str(destination),
        "envelope_sha256": hashlib.sha256(data).hexdigest()}


def verify_delivery(metadata, job):
    require(isinstance(metadata, dict) and metadata.get("producer") == "ani_rag"
            and all(metadata.get(key) == job.get(key) for key in (
                "job_id", "generation_attempt_id", "result_sha256")), "rag_delivery_binding_changed")
    _, data = _file(metadata["envelope_path"], maximum=128 * 1024)
    require(hashlib.sha256(data).hexdigest() == metadata.get("envelope_sha256"), "rag_delivery_changed")
    envelope = json.loads(data)
    require(all(envelope.get(key) == job.get(key) for key in (
        "job_id", "generation_attempt_id", "result_sha256", "input_sha256", "admission_sha256"))
        and envelope.get("producer") == "ani_rag" and envelope.get("schema_version") == 1
        and envelope.get("policy_date") == job["admission_receipt"]["policy_date"]
        and envelope.get("policy_sha256") == job["admission_receipt"]["policy_sha256"],
        "rag_delivery_binding_changed")
    require(digest(envelope.get("daily_policy")) == envelope.get("day_policy_sha256")
            and digest({"daily": envelope.get("daily_policy"), "rag": envelope.get("rag_scope")})
            == job["admission_receipt"]["policy_sha256"], "rag_delivery_policy_changed")
    for prefix, maximum in (("native_result", 96 * 1024 * 1024), ("manifest", 16384), ("launch", 96 * 1024 * 1024)):
        _, body = _file(envelope[prefix + "_path"], maximum=maximum)
        require(hashlib.sha256(body).hexdigest() == envelope.get(prefix + "_sha256"), "rag_delivery_source_changed")


def load_runtime(path, expected_sha256):
    path, data = _file(path, maximum=16384)
    require(hashlib.sha256(data).hexdigest() == expected_sha256, "rag_runtime_not_reviewed")
    from menu_worker.rag_native import RagNativeConfig, validate_pricing
    try:
        config = RagNativeConfig(**json.loads(data))
    except (TypeError, ValueError):
        raise StopRun("invalid_rag_runtime_config") from None
    require(config.confirm_isolated_copy is True and config.offline is False, "rag_isolated_runtime_required")
    validate_pricing(config)
    root, database = Path(config.isolated_root), Path(config.database_path)
    require(root.is_absolute() and root.resolve() == root and root.is_dir()
            and database.is_absolute() and database.resolve() == database
            and database.is_file() and database.is_relative_to(root), "unsafe_rag_runtime_path")
    return config


@dataclass
class DailyRagRuntime:
    store: object
    discover: object
    read_result: object
    publish: object
    export: object
    app: object


def compose_runtime(plan, *, app, runtime_path, runtime_sha256, enable=False):
    """No provider setup. Redis publication is the only activation, opt-in below."""
    require(runtime_sha256 == plan.get("rag_runtime_sha256"), "rag_runtime_plan_changed")
    config = load_runtime(runtime_path, runtime_sha256)
    require(all(route["menu_work_id"] in config.catalog_work_ids for route in plan["rag_scope"]["routes"]),
            "rag_runtime_scope_mismatch")
    if enable:
        require(config.enabled is True and os.getenv("RAG_GENERATION_ENABLED", "false") == "true",
                "rag_generation_disabled")
        configured = Path(os.environ.get("RAG_RUNTIME_CONFIG_FILE", ""))
        require(configured == Path(runtime_path), "rag_worker_runtime_mismatch")
    from menu_worker.rag_candidates import DictionaryCandidates
    from menu_worker.rag_store import RagStore
    from menu_worker.rag_tasks import QUEUE_NAME, TASK_NAME, read_sealed_result
    store = RagStore()
    root, inbox = Path(plan["config"]["artifact_root"]), Path(plan["rag_delivery_root"])
    def publish(job_id):
        require(enable is True, "rag_generation_disabled")
        # A reviewed runtime file must remain unchanged throughout the run.
        load_runtime(runtime_path, runtime_sha256)
        # The article app deliberately registers only its own queue and refuses
        # implicit queue creation. Use the RAG app's existing queue declaration
        # without expanding what the article Worker can consume.
        from menu_worker.rag_app import app as publisher
        require(publisher.conf.broker_url == app.conf.broker_url, "rag_publisher_broker_mismatch")
        return publish_with_deadline(job_id, app=publisher, task=TASK_NAME, queue=QUEUE_NAME,
            deadline=plan["config"]["deadline"], job_timeout_seconds=plan["config"]["job_timeout_seconds"])
    return DailyRagRuntime(store=store,
        discover=DictionaryCandidates(config.database_path, catalog_work_ids=config.catalog_work_ids),
        read_result=lambda job: read_sealed_result(root, job), publish=publish,
        export=lambda job, _directory: export_native_delivery(root, inbox, job,
            daily_policy=plan["daily_policy"], rag_scope=plan["rag_scope"]), app=app)


def verify_empty_rag_broker(app, *, client=None):
    """Same pinned generation broker, separate queue/priority lists."""
    from menu_worker.daily_resume import verify_empty_generation_broker
    # This validates the broker layout and its global in-flight receipts first.
    report = verify_empty_generation_broker(app, client=client)
    owned = client is None
    try:
        if owned:
            from redis import Redis
            client = Redis.from_url(app.conf.broker_url, socket_timeout=3, socket_connect_timeout=3)
        counts = {str(priority): client.llen("content.rag_generate" + ("\x06\x16" + str(priority) if priority else ""))
                  for priority in (0, 3, 6, 9)}
    except Exception:
        raise StopRun("rag_generation_broker_unavailable") from None
    finally:
        if owned and client is not None:
            client.close()
    require(sum(counts.values()) == 0, "rag_generation_broker_not_empty")
    return {**report, "rag_priority_counts": counts}
