"""Actual Redis/Celery delayed-delivery check; fake task only, no Agent or DB.

Run in a disposable internal network with the generation-redis hostname.
"""
from datetime import datetime, timezone
import json
import os
import time
from uuid import uuid4

from celery import Celery
from celery.contrib.testing.worker import start_worker
from celery.exceptions import TaskRevokedError

from menu_worker.bounded_resume import publish_with_deadline


def main():
    assert os.environ.get("BOUNDED_EXPIRATION_TEST") == "isolated"
    assert not any(os.environ.get(key) for key in ("LLM_KEY", "OPENAI_API_KEY", "ANIME_LLM_KEY_FILE",
        "CONTENT_JOB_DATABASE_URL", "DATABASE_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"))
    queue = "bounded-expiry-test-" + uuid4().hex
    app = Celery(queue, broker="redis://generation-redis:6379/0", backend="redis://generation-redis:6379/1")
    app.conf.update(task_default_queue=queue, task_serializer="json", result_serializer="json",
        accept_content=["json"], broker_connection_retry_on_startup=False, worker_prefetch_multiplier=1)
    processed = []

    @app.task(name=queue + ".fake")
    def fake_task(job_id):
        processed.append(job_id)
        return {"job_id": job_id, "llm_calls": 0}

    def send(job_id, latest_start):
        # Controller reserves the full processing window before the end time.
        deadline = datetime.fromtimestamp(latest_start + 1500, tz=timezone.utc).isoformat()
        return app.AsyncResult(publish_with_deadline(job_id, app=app, task=fake_task.name,
            queue=queue, deadline=deadline, job_timeout_seconds=1500))

    expired = send("too-late", time.time() + 1)
    time.sleep(1.2)  # Simulates the Worker being offline while the message waits.
    valid = send("within-window", time.time() + 60)
    with start_worker(app, pool="solo", concurrency=1, queues=(queue,), perform_ping_check=False,
                      loglevel="ERROR", shutdown_timeout=15):
        assert valid.get(timeout=15) == {"job_id": "within-window", "llm_calls": 0}
        try:
            expired.get(timeout=10)
        except TaskRevokedError:
            pass
        else:
            raise AssertionError("Expired delivery was not revoked")
    assert processed == ["within-window"]
    expired.forget()
    valid.forget()
    app.close()
    print(json.dumps({"expired_delivery": "revoked", "valid_delivery": "processed_once",
        "expired_task_body_calls": 0, "llm_calls": 0, "service_db_writes": 0}))


if __name__ == "__main__":
    main()
