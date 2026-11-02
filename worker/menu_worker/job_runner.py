"""DB claims fence delivery; interrupted generation is never restarted here."""
from pathlib import Path
from threading import Event, Thread

from menu_worker.job_artifacts import ArtifactError, FakeAgentError, generate_fake, validate_artifacts


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
                if not self.store.renew_lease(self.job["job_id"], self.job["claim_token"], lease_seconds=30):
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


def run_job(store, root: Path, job_id: str, task_id: str | None = None, *, recover=False) -> dict:
    claim = store.claim_recovery if recover else store.claim
    job = claim(job_id, task_id=task_id, lease_seconds=30)
    if job is None:
        return {"job_id": str(job_id), "processed": False}
    token = job["claim_token"]
    with Lease(store, job) as lease:
        try:
            if not recover:
                if not store.mark_generation_started(job_id, token):
                    raise LostClaim("Job claim was lost")
                generate_fake(root, job, pulse=lease.ensure)
            lease.ensure()
            evidence = validate_artifacts(root, job)
            completed = store.complete(job_id, token, execution_status="succeeded", **evidence)
        except FakeAgentError:
            completed = store.complete(job_id, token, execution_status="failed", error_code="fake_generation_failed")
        except LostClaim:
            return {"job_id": str(job_id), "processed": False, "reason": "claim_lost"}
        except (ArtifactError, OSError):
            completed = store.complete(job_id, token, execution_status="needs_attention",
                                       error_code="artifact_verification_required")
    return {"job_id": str(job_id), "processed": completed, "execution_status": store.get(job_id)["execution_status"]}
