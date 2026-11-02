"""Bounded smoke tasks only. Never call an LLM, agent, or application database."""
from datetime import datetime, timezone
import json
import time

from redis import Redis

from menu_worker.app import app, settings

HEARTBEAT_KEY = "menu:runtime:heartbeat"


def result_store() -> Redis:
    return Redis.from_url(settings.result_backend, socket_timeout=3, socket_connect_timeout=3)


@app.task(bind=True, name="runtime.probe", max_retries=2, acks_late=True, reject_on_worker_lost=True)
def probe(self, label: str = "manual", delay_seconds: int = 0, failures_before_success: int = 0) -> dict:
    if not isinstance(label, str) or not 1 <= len(label) <= 80 or not label.isprintable():
        raise ValueError("label must be 1 to 80 printable characters")
    if type(delay_seconds) is not int or not 0 <= delay_seconds <= 10:
        raise ValueError("delay_seconds must be an integer from 0 to 10")
    if type(failures_before_success) is not int or not 0 <= failures_before_success <= 2:
        raise ValueError("failures_before_success must be an integer from 0 to 2")
    if self.request.retries < failures_before_success:
        raise self.retry(exc=RuntimeError("Intentional runtime probe retry"), countdown=1)
    time.sleep(delay_seconds)
    return {"label": label, "task_id": self.request.id, "retries": self.request.retries}


@app.task(bind=True, name="runtime.heartbeat", ignore_result=True, acks_late=True)
def heartbeat(self) -> None:
    evidence = {"timestamp": datetime.now(timezone.utc).isoformat(), "task_id": self.request.id}
    with result_store() as client:
        client.set(HEARTBEAT_KEY, json.dumps(evidence), ex=settings.beat_interval_seconds * 3 + 30)
