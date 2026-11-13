"""The manual generation task runs only in the dedicated generation profile."""
from menu_worker.generation_app import app
from menu_worker.job_runner import execute_delivery


@app.task(name="content.anime_generate", bind=True, shared=False, acks_late=True,
          reject_on_worker_lost=True, max_retries=0)
def anime_generate(self, job_id: str) -> dict:
    return execute_delivery(job_id, self.request.id, self.request.hostname, expected_kind="anime_generate")
