"""Shared transport settings for the two local Celery applications."""
from celery import Celery
from kombu import Queue

from menu_worker.config import Settings


def create_app(name: str, settings: Settings, *, modules: tuple[str, ...], default_queue: str,
               result_prefix: str, other_queues: tuple[str, ...] = (), visibility_timeout: int = 90,
               soft_time_limit: int = 15, time_limit: int = 20) -> Celery:
    app = Celery(name, include=modules)
    app.conf.update(
        broker_url=settings.broker_url,
        result_backend=settings.result_backend,
        task_queues=tuple(Queue(queue) for queue in (default_queue, *other_queues)),
        task_default_queue=default_queue,
        task_create_missing_queues=False,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_accept_content=["json"],
        enable_utc=True,
        timezone="Asia/Seoul",
        task_track_started=True,
        task_send_sent_event=True,
        worker_send_task_events=True,
        worker_prefetch_multiplier=1,
        worker_cancel_long_running_tasks_on_connection_loss=True,
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=3,
        broker_transport_options={"visibility_timeout": visibility_timeout, "socket_timeout": 3, "socket_connect_timeout": 3},
        result_backend_transport_options={"visibility_timeout": visibility_timeout, "global_keyprefix": result_prefix},
        visibility_timeout=visibility_timeout,
        redis_socket_timeout=3,
        redis_socket_connect_timeout=3,
        result_expires=3600,
        task_publish_retry_policy={"max_retries": 3, "interval_start": 0, "interval_step": 0.2, "interval_max": 1},
        task_soft_time_limit=soft_time_limit,
        task_time_limit=time_limit,
    )
    return app
