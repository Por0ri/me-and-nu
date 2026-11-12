"""DB claims fence delivery; interrupted generation is never restarted here."""
import os
from pathlib import Path
from threading import Event, Thread

from menu_worker.job_artifacts import ArtifactError, FakeAgentError, generate_anime_probe, generate_fake, validate_artifacts
from menu_worker.anime_catalog import AnimeInputError
from menu_worker.job_contract import INPUT_FIELDS, JobInputError, validate_job_request
from menu_worker.real_generation import RealGenerationError, generate_real, preflight_real, validate_real_artifacts


class LostClaim(RuntimeError):
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


def run_job(store, root: Path, job_id: str, task_id: str | None = None, *, recover=False, expected_kind="fake") -> dict:
    claim = store.claim_recovery if recover else store.claim
    job = claim(job_id, task_id=task_id, lease_seconds=30, expected_kind=expected_kind)
    if job is None:
        return {"job_id": str(job_id), "processed": False}
    token = job["claim_token"]
    live = expected_kind == "anime_generate"
    generation_started = recover
    with Lease(store, job) as lease:
        try:
            if not recover:
                if expected_kind in {"anime", "anime_generate"}:
                    fields = {key: job[key] for key in INPUT_FIELDS}
                    fields["menu_work_id"] = job["menu_work_id"]
                    _, canonical, digest = validate_job_request(fields, expected_kind)
                    if (canonical, digest) != (job["canonical_url"], job["input_sha256"]):
                        raise JobInputError("Stored input identity mismatch", "input_identity_mismatch")
                if live:
                    preflight_real()
                if not store.mark_generation_started(job_id, token, expected_kind=expected_kind):
                    raise LostClaim("Job claim was lost")
                generation_started = True
                generate = {"anime_generate": generate_real, "anime": generate_anime_probe, "fake": generate_fake}[expected_kind]
                generate(root, job, pulse=lease.ensure)
            lease.ensure()
            validate = validate_real_artifacts if live else validate_artifacts
            evidence = validate(root, job)
            completed = store.complete(job_id, token, execution_status="succeeded", expected_kind=expected_kind, **evidence)
        except (JobInputError, AnimeInputError) as error:
            completed = store.complete(job_id, token, execution_status="failed",
                                       expected_kind=expected_kind, error_code=error.code)
        except FakeAgentError:
            completed = store.complete(job_id, token, execution_status="failed",
                                       expected_kind=expected_kind, error_code="fake_generation_failed")
        except LostClaim:
            return {"job_id": str(job_id), "processed": False, "reason": "claim_lost"}
        except RealGenerationError as error:
            agent_not_launched = live and not recover and error.before_launch
            status = "needs_attention" if generation_started and not agent_not_launched else "failed"
            completed = store.complete(job_id, token, execution_status=status,
                                       expected_kind=expected_kind, error_code=error.code,
                                       agent_not_launched=agent_not_launched)
        except (ArtifactError, OSError):
            completed = store.complete(job_id, token, execution_status="needs_attention",
                                       expected_kind=expected_kind, error_code="artifact_verification_required")
    return {"job_id": str(job_id), "processed": completed, "execution_status": store.get(job_id)["execution_status"]}


def execute_delivery(job_id, task_id, worker_name, *, expected_kind):
    from menu_worker.job_store import JobStore
    try:
        if os.environ.get("CONTENT_JOB_KIND") != expected_kind:
            raise RuntimeError("Unexpected worker role")
        root = Path(os.environ["CONTENT_JOB_ARTIFACT_ROOT"])
        result = run_job(JobStore(), root, job_id, task_id, expected_kind=expected_kind)
        return {**result, "worker": worker_name, "worker_kind": expected_kind}
    except Exception:
        # DB errors can contain credentials. The dispatcher reconciles expired claims.
        raise RuntimeError("Content job stopped; inspect its durable job status") from None
