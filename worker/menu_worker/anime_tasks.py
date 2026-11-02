"""No-LLM delivery probe bound exclusively to the anime Celery application."""
from menu_worker.anime_app import app
from menu_worker.job_runner import execute_delivery


@app.task(name="content.anime_probe", bind=True, shared=False, acks_late=True,
          reject_on_worker_lost=True, max_retries=0, soft_time_limit=45, time_limit=50)
def anime_probe(self, job_id: str) -> dict:
    return execute_delivery(job_id, self.request.id, self.request.hostname, expected_kind="anime")
