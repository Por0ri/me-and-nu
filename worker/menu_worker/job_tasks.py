"""Fake-only task; service credentials and real Agent modules are not imported."""
import os
from pathlib import Path

from menu_worker.app import app


@app.task(name="content.fake_generate", bind=True, acks_late=True,
          reject_on_worker_lost=True, max_retries=0, soft_time_limit=45, time_limit=50)
def fake_generate(self, job_id: str) -> dict:
    from menu_worker.job_store import JobStore
    from menu_worker.job_runner import run_job
    try:
        root = Path(os.environ["CONTENT_JOB_ARTIFACT_ROOT"])
        return run_job(JobStore(), root, job_id, str(self.request.id))
    except Exception:
        # DB reconnect diagnostics can expose credentials; retain safe category only.
        # The dispatcher reconciles expired claims. Never autoretry generation.
        raise RuntimeError("Content job stopped; inspect its durable job status") from None
