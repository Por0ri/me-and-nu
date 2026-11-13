from menu_worker.celery_runtime import create_app
from menu_worker.config import load_settings


settings = load_settings()
app = create_app(
    "menu_runtime", settings,
    modules=("menu_worker.tasks", "menu_worker.job_tasks"),
    default_queue="runtime", other_queues=("content-generation",), result_prefix="menu-runtime:",
)
app.conf.update(
    task_routes={"content.fake_generate": {"queue": "content-generation"}},
    beat_max_loop_interval=5,
    beat_schedule={
        "runtime-heartbeat": {
            "task": "runtime.heartbeat",
            "schedule": settings.beat_interval_seconds,
            "options": {"expires": settings.beat_interval_seconds},
        },
    },
)
