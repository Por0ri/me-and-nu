from celery import Celery
from kombu import Queue

from menu_worker.config import load_settings

settings = load_settings()
app = Celery("menu_runtime", include=["menu_worker.tasks", "menu_worker.job_tasks"])
app.conf.update(
    broker_url=settings.broker_url,
    result_backend=settings.result_backend,
    task_queues=(Queue("runtime"), Queue("content-generation")),
    task_default_queue="runtime",
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
    # Longer than the fake task hard limit (50s). DB claims still fence delivery.
    # Real paid agents need a separate execution/timeout review before wiring in.
    broker_transport_options={"visibility_timeout": 90, "socket_timeout": 3, "socket_connect_timeout": 3},
    result_backend_transport_options={"visibility_timeout": 90, "global_keyprefix": "menu-runtime:"},
    visibility_timeout=90,
    redis_socket_timeout=3,
    redis_socket_connect_timeout=3,
    result_expires=3600,
    task_publish_retry_policy={"max_retries": 3, "interval_start": 0, "interval_step": 0.2, "interval_max": 1},
    task_soft_time_limit=15,
    task_time_limit=20,
    beat_max_loop_interval=5,
    beat_schedule={
        "runtime-heartbeat": {
            "task": "runtime.heartbeat",
            "schedule": settings.beat_interval_seconds,
            "options": {"expires": settings.beat_interval_seconds},
        },
    },
)
