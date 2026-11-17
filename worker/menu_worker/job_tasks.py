"""Fake-only task; service credentials and real Agent modules are not imported."""
from menu_worker.app import app
from menu_worker.job_runner import execute_delivery


@app.task(name="content.fake_generate", bind=True, shared=False, acks_late=True,
          reject_on_worker_lost=True, max_retries=0, soft_time_limit=45, time_limit=50)
def fake_generate(self, job_id: str) -> dict:
    return execute_delivery(job_id, self.request.id, self.request.hostname, expected_kind="fake")
