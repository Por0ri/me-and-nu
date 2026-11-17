"""DB claims fence delivery; interrupted generation is never restarted here."""
import os
from contextlib import ExitStack, nullcontext
from pathlib import Path
from threading import Event, Thread
import time

from menu_worker.job_artifacts import ArtifactError, FakeAgentError, generate_anime_probe, generate_fake, validate_artifacts
from menu_worker.anime_catalog import AnimeInputError, generation_article_key
from menu_worker.job_contract import INPUT_FIELDS, JobInputError, job_input_hash, validate_job_request
from menu_worker.real_generation import RealGenerationError, generate_real, preflight_real, validate_real_artifacts
from menu_worker.cover_history import build_snapshot, preflight_snapshot
from menu_worker.job_store import JobStoreError


class LostClaim(RuntimeError):
    pass


class HistoryAdmissionUnavailable(RuntimeError):
    pass


class Lease:
    def __init__(self, store, job):
        self.store, self.job = store, job
        self.stopped, self.lost = Event(), Event()
        self.thread = Thread(target=self._heartbeat, daemon=True)

    def _heartbeat(self):
        while not self.stopped.wait(5):
            try:
                if not self.store.renew_lease(self.job["job_id"], self.job["claim_token"], lease_seconds=30,
                                              expected_kind=self.job["job_kind"]):
                    self.lost.set()
                    return
            except Exception:
                self.lost.set()
                return

    def ensure(self):
        if self.lost.is_set():
            raise LostClaim("Job claim was lost")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stopped.set()
        self.thread.join(timeout=6)


def run_job(store, root: Path, job_id: str, task_id: str | None = None, *, recover=False,
            expected_kind="fake", retry_failures=False) -> dict:
    guard = store.generation_history_guard() if expected_kind == "anime_generate" else nullcontext(lambda: None)
    with ExitStack() as stack:
        try:
            history_check = stack.enter_context(guard)
            busy = history_check is None or (expected_kind == "anime_generate" and
                                              store.generation_history_busy(exclude_job_id=job_id))
            if busy:
                # ACK this receipt only after making it eligible for durable dispatch.
                if task_id is not None:
                    store.requeue_delivery(job_id, task_id, expected_kind=expected_kind)
                return {"job_id": str(job_id), "processed": False, "reason": "generation_history_busy"}
        except Exception:
            # Only admission is covered here; no job has been claimed yet.
            raise HistoryAdmissionUnavailable from None
        return _run_claimed(store, root, job_id, task_id, recover=recover, expected_kind=expected_kind,
                            retry_failures=retry_failures, history_check=history_check)


def _run_claimed(store, root, job_id, task_id, *, recover, expected_kind, retry_failures, history_check):
    claim = store.claim_recovery if recover else store.claim
    job = claim(job_id, task_id=task_id, lease_seconds=30, expected_kind=expected_kind)
    if job is None:
        return {"job_id": str(job_id), "processed": False}
    retry_delay = 0
    while True:
        report, next_job = _run_attempt(store, root, job, recover=recover, expected_kind=expected_kind,
                                        retry_failures=retry_failures, retry_delay=retry_delay,
                                        history_check=history_check)
        if next_job is None:
            return report
        job = next_job
        retry_delay = min(job["attempt_count"] - 1, 2)


def _run_attempt(store, root, job, *, recover, expected_kind, retry_failures, retry_delay, history_check):
    job_id = job["job_id"]
    token = job["claim_token"]
    live = expected_kind == "anime_generate"
    generation_started = recover
    next_job = None
    with Lease(store, job) as lease:
        def pulse():
            lease.ensure()
            try:
                history_check()
            except Exception:
                raise LostClaim("Generation history lock was lost") from None

        try:
            if retry_delay:
                time.sleep(retry_delay)
                pulse()
            if not recover:
                if expected_kind in {"anime", "anime_generate"}:
                    fields = {key: job[key] for key in INPUT_FIELDS}
                    fields["menu_work_id"] = job["menu_work_id"]
                    fields["canonical_url"] = job["canonical_url"]
                    request, candidate, _ = validate_job_request(fields, expected_kind)
                    # v4 manifests bind the original path, including any trailing slash.
                    digest = job_input_hash(request, expected_kind, job["canonical_url"])
                    stored_identity = job["candidate_url"]
                    if live:
                        stored_identity = generation_article_key(job["source_id"], stored_identity)
                    if candidate != stored_identity or digest != job["input_sha256"]:
                        raise JobInputError("Stored input identity mismatch", "input_identity_mismatch")
                if live:
                    preflight_real()
                    try:
                        history = store.cover_history_sources(exclude_job_id=job_id)
                    except JobStoreError as error:
                        code = str(error) if str(error) in {"cover_history_invalid", "cover_history_unavailable"} else "cover_history_unavailable"
                        raise RealGenerationError(code, before_launch=True) from None
                    snapshot = build_snapshot(root, job, history)
                    preflight_snapshot(snapshot, job)
                    job = {**job, "_cover_history_snapshot": snapshot}
                pulse()
                if not store.mark_generation_started(job_id, token, expected_kind=expected_kind):
                    raise LostClaim("Job claim was lost")
                generation_started = True
                generate = {"anime_generate": generate_real, "anime": generate_anime_probe, "fake": generate_fake}[expected_kind]
                generate(root, job, pulse=pulse)
            pulse()
            validate = validate_real_artifacts if live else validate_artifacts
            evidence = validate(root, job)
            pulse()
            completed = store.complete(job_id, token, execution_status="succeeded", expected_kind=expected_kind, **evidence)
        except (JobInputError, AnimeInputError) as error:
            completed = store.complete(job_id, token, execution_status="failed",
                                       expected_kind=expected_kind, error_code=error.code)
        except FakeAgentError:
            if retry_failures and not recover:
                next_job = store.retry_failure(job_id, token, expected_kind=expected_kind,
                                               error_code="fake_generation_failed")
            completed = bool(next_job) or store.complete(job_id, token, execution_status="failed",
                                                         expected_kind=expected_kind, error_code="fake_generation_failed")
        except LostClaim:
            return {"job_id": str(job_id), "processed": False, "reason": "claim_lost"}, None
        except RealGenerationError as error:
            agent_not_launched = live and not recover and error.before_launch
            status = "needs_attention" if generation_started and not agent_not_launched else "failed"
            if retry_failures and agent_not_launched:
                next_job = store.retry_failure(job_id, token, expected_kind=expected_kind,
                                               error_code=error.code, agent_not_launched=True)
            completed = bool(next_job) or store.complete(job_id, token, execution_status=status,
                                                         expected_kind=expected_kind, error_code=error.code,
                                                         agent_not_launched=agent_not_launched)
        except (ArtifactError, OSError):
            completed = store.complete(job_id, token, execution_status="needs_attention",
                                       expected_kind=expected_kind, error_code="artifact_verification_required")
    if next_job is not None:
        return None, next_job
    return {"job_id": str(job_id), "processed": completed,
            "execution_status": store.get(job_id)["execution_status"]}, None


def execute_delivery(job_id, task_id, worker_name, *, expected_kind):
    from menu_worker.job_store import JobStore
    try:
        if os.environ.get("CONTENT_JOB_KIND") != expected_kind:
            raise RuntimeError("Unexpected worker role")
        root = Path(os.environ["CONTENT_JOB_ARTIFACT_ROOT"])
        result = run_job(JobStore(), root, job_id, task_id, expected_kind=expected_kind, retry_failures=True)
        return {**result, "worker": worker_name, "worker_kind": expected_kind}
    except HistoryAdmissionUnavailable:
        # Admission failed before claim; an ordinary failed task would ACK the
        # only receipt while last_dispatched_at still keeps it out of dispatch.
        from celery.exceptions import Reject
        time.sleep(1)
        raise Reject("Generation history temporarily unavailable", requeue=True) from None
    except Exception:
        # DB errors can contain credentials. The dispatcher reconciles expired claims.
        raise RuntimeError("Content job stopped; inspect its durable job status") from None
