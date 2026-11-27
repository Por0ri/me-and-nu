"""Explicitly wired, disabled-by-default Celery execution for one admitted RAG job.

Importing this module registers nothing and never starts providers. The runtime
composition supplies seed preflight and the native bounded producer; a broker
message contains only the durable job UUID, never execution settings or keys.
"""
import asyncio
import base64
from datetime import datetime, time as day_time, timedelta, timezone
import hashlib
import inspect
from io import BytesIO
import json
import os
from pathlib import Path
from threading import Event, Thread
import time
from uuid import UUID

from menu_worker.rag_contract import canonical, require, validate_request

TASK_NAME = "content.rag_generate"
QUEUE_NAME = "content.rag_generate"
MAX_RESULT_BYTES = 96 * 1024 * 1024
KST = timezone(timedelta(hours=9))
ADMISSION_ERRORS = frozenset({"rag_admission_date_inactive", "rag_admission_window_closed"})


def _identity(job):
    return {key: job[key] for key in ("job_id", "generation_attempt_id", "input_sha256", "admission_sha256")}


def _check_admission_window(job, now, job_timeout_seconds):
    moment = datetime.fromtimestamp(now(), KST)
    require(moment.date().isoformat() == job["admission_receipt"]["policy_date"], "rag_admission_date_inactive")
    midnight = datetime.combine(moment.date() + timedelta(days=1), day_time.min, tzinfo=KST)
    require((midnight - moment).total_seconds() >= job_timeout_seconds, "rag_admission_window_closed")


def _directory(root, job):
    root = Path(root)
    require(root.is_absolute() and root.resolve() == root, "unsafe_rag_artifact_root")
    job_id, attempt = (str(UUID(job[key])) for key in ("job_id", "generation_attempt_id"))
    directory = root / "rag/jobs" / job_id / "attempts" / attempt
    require(directory.resolve() == directory, "unsafe_rag_artifact_path")
    return directory


def _write(path, value):
    data = canonical(value)
    require(len(data) <= MAX_RESULT_BYTES, "rag_result_too_large")
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(data).hexdigest()


def _validate_result(result, job):
    require(isinstance(result, dict), "invalid_native_rag_result")
    request = validate_request(job["request"])
    native = result.get("request", {})
    require(result.get("producer") == "ani_rag" and result.get("schema_version") == 1
            and result.get("job_id") == job["job_id"]
            and result.get("generation_attempt_id") == job["generation_attempt_id"]
            and result.get("namespace") == request.namespace and result.get("work_id") == request.work_id,
            "unbound_native_rag_result")
    require(isinstance(native, dict) and native.get("work_id") == request.work_id
            and native.get("menu_work_ids") == [request.menu_work_id]
            and native.get("content_type") == request.content_type and native.get("query") == request.query
            and sorted(native.get("seed_evidence_ids", [])) == [row[0] for row in request.seed_evidence]
            and result.get("source_ids") == list(request.allowed_source_ids), "native_rag_scope_changed")
    require(result.get("outcome") in {"ready_for_staging", "held", "skipped", "rejected", "failed"}
            and isinstance(result.get("usage_records"), list)
            and isinstance(result.get("cost_accounting"), dict)
            and type(result["cost_accounting"].get("complete")) is bool, "native_rag_accounting_missing")
    if result["outcome"] == "ready_for_staging":
        review = result.get("review")
        require(result.get("simulated") is False and result.get("review_mode") == "llm"
                and result.get("producer_status") == "ready_for_downstream"
                and isinstance(review, dict) and review.get("decision") == "pass"
                and isinstance(review.get("issues"), list)
                and all(isinstance(row, dict) and row.get("blocking") is False for row in review["issues"])
                and any(row.get("stage") == "review" and row.get("simulated") is False
                        for row in result["usage_records"] if isinstance(row, dict)), "native_rag_review_incomplete")
        cards = result.get("rendered_cards")
        require(isinstance(cards, list) and 2 <= len(cards) <= 10, "native_rag_cards_missing")
        from PIL import Image
        for index, card in enumerate(cards, 1):
            require(isinstance(card, dict) and type(card.get("page")) is int and card["page"] == index,
                    "native_rag_card_order")
            try:
                data = base64.b64decode(card["png_base64"], validate=True)
                require(len(data) <= 20 * 1024 * 1024 and hashlib.sha256(data).hexdigest() == card.get("sha256"),
                        "native_rag_card_hash")
                with Image.open(BytesIO(data)) as image:
                    require(image.format == "PNG" and image.size == (1308, 1632)
                            and (card.get("width"), card.get("height")) == (1308, 1632)
                            and getattr(image, "n_frames", 1) == 1, "native_rag_card_dimensions")
                    image.verify()
                with Image.open(BytesIO(data)) as image:
                    image.load()
            except (ValueError, KeyError, TypeError, OSError):
                require(False, "invalid_native_rag_card")
    return result


def read_sealed_result(root, job):
    directory = _directory(root, job)
    manifest_path = directory / "manifest.json"
    require(manifest_path.resolve() == manifest_path and manifest_path.is_file()
            and manifest_path.stat().st_size <= 16384, "rag_manifest_missing")
    manifest = json.loads(manifest_path.read_bytes())
    require(manifest.get("schema_version") == 1 and manifest.get("identity") == _identity(job)
            and set(manifest.get("files", {})) == {"launch.json", "result.json"}, "rag_manifest_identity_changed")
    for name in ("launch.json", "result.json"):
        path = directory / name
        require(path.resolve() == path and path.is_file() and path.stat().st_size <= MAX_RESULT_BYTES,
                "unsafe_rag_artifact")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == manifest.get("files", {}).get(name),
                "rag_artifact_changed")
    require(manifest["files"]["result.json"] == job["result_sha256"], "rag_result_seal_changed")
    launch = json.loads((directory / "launch.json").read_bytes())
    require(launch == {"identity": _identity(job), "request": job["request"],
                       "admission_receipt": job["admission_receipt"]}, "rag_launch_changed")
    result = _validate_result(json.loads((directory / "result.json").read_bytes()), job)
    failed = result["outcome"] == "failed"
    complete = result["cost_accounting"]["complete"] and all(row.get("estimated_cost_usd") is not None
                                                               for row in result["usage_records"])
    require(job.get("execution_status") == ("failed" if failed else "succeeded")
            and job.get("outcome") == (None if failed else result["outcome"])
            and job.get("producer_status") == result.get("producer_status")
            and job.get("usage_records") == result["usage_records"] and job.get("cost_complete") is complete
            and job.get("artifact_path") == manifest_path.relative_to(root).as_posix(), "rag_ledger_evidence_changed")
    return result


class _Lease:
    def __init__(self, store, job, guard):
        self.store, self.job, self.guard = store, job, guard
        self.stop, self.lost = Event(), Event()
        self.thread = Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.wait(5):
            try:
                self.guard()
                if not self.store.renew_lease(self.job["job_id"], self.job["claim_token"]):
                    self.lost.set()
                    return
            except Exception:
                self.lost.set()
                return

    def pulse(self):
        require(not self.lost.is_set(), "rag_claim_lost")
        self.guard()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.thread.join(timeout=6)


def execute_delivery(store, artifact_root, job_id, *, run_native, preflight, enabled=False,
                     now=time.time, job_timeout_seconds=1500):
    """Process once. Both callbacks are trusted local composition, never broker input.

    preflight(job) must verify exact seed hashes/work/source against the selected
    isolated producer snapshot, without provider calls. run_native(job, pulse)
    invokes the native bounded bridge and returns its usage-preserving result.
    """
    require(str(UUID(job_id)) == job_id, "invalid_rag_job_id")
    if enabled is not True:
        return {"job_id": job_id, "processed": False, "reason": "rag_generation_disabled"}
    require(callable(preflight) and callable(run_native), "rag_runtime_not_configured")
    require(callable(now) and type(job_timeout_seconds) is int and 1 <= job_timeout_seconds <= 3600,
            "invalid_rag_job_timeout")
    with store.generation_guard(exclude_job_id=job_id) as guard:
        if guard is None:
            return {"job_id": job_id, "processed": False, "reason": "generation_history_busy"}
        job = store.claim(job_id)
        if job is None:
            return {"job_id": job_id, "processed": False, "reason": "already_claimed_or_complete"}
        started = False
        result = None
        with _Lease(store, job, guard) as lease:
            try:
                _check_admission_window(job, now, job_timeout_seconds)
                preflight(job)
                directory = _directory(artifact_root, job)
                directory.mkdir(parents=True, exist_ok=False)
                launch_sha = _write(directory / "launch.json", {"identity": _identity(job), "request": job["request"],
                                                               "admission_receipt": job["admission_receipt"]})
                lease.pulse()
                # Preflight may itself cross midnight/the launch cutoff. The
                # delayed broker receipt must not spend yesterday's allowance.
                _check_admission_window(job, now, job_timeout_seconds)
                require(store.mark_generation_started(job_id, job["claim_token"], guard), "rag_claim_lost")
                started = True
                result = run_native(job, lease.pulse)
                if inspect.isawaitable(result):
                    result = asyncio.run(result)
                lease.pulse()
                result = _validate_result(result, job)
                result_sha = _write(directory / "result.json", result)
                _write(directory / "manifest.json", {"schema_version": 1, "identity": _identity(job),
                    "files": {"launch.json": launch_sha, "result.json": result_sha}})
                lease.pulse()
                failed = result["outcome"] == "failed"
                completed = store.complete(job_id, job["claim_token"], execution_status="failed" if failed else "succeeded",
                    outcome=None if failed else result["outcome"], producer_status=result["producer_status"],
                    result_sha256=result_sha, artifact_path=(directory / "manifest.json").relative_to(artifact_root).as_posix(),
                    usage_records=result["usage_records"], cost_complete=result["cost_accounting"]["complete"],
                    error_code="native_rag_failed" if failed else None)
            except Exception as error:
                # No provider retry, queue reset, cost fabrication, or raw exception
                # text. Preserve returned usage even if later artifact work fails.
                usage = result.get("usage_records", []) if isinstance(result, dict) else []
                code = getattr(error, "code", None)
                code = code if not started and code in ADMISSION_ERRORS else (
                    "rag_attempt_verification_required" if started else "rag_preflight_failed")
                completed = store.complete(job_id, job["claim_token"],
                    execution_status="needs_attention" if started else "failed",
                    usage_records=usage if isinstance(usage, list) else [], cost_complete=not started,
                    error_code=code)
        return {"job_id": job_id, "processed": bool(completed),
                "execution_status": store.get(job_id)["execution_status"]}


def publish(app, job_id, *, enabled=False):
    require(enabled is True and str(UUID(job_id)) == job_id, "rag_generation_disabled")
    result = app.send_task(TASK_NAME, args=[job_id], queue=QUEUE_NAME)
    return str(result.id)


def register_task(app, runner_factory):
    """Explicit runtime wiring; the factory returns a job-ID-only executor."""
    @app.task(name=TASK_NAME, bind=True, shared=False, acks_late=True, reject_on_worker_lost=True, max_retries=0)
    def task(_self, job_id):
        report = runner_factory()(job_id)
        if report.get("reason") == "generation_history_busy":
            from celery.exceptions import Reject
            time.sleep(1)
            raise Reject("Generation history temporarily unavailable", requeue=True)
        return report
    return task
