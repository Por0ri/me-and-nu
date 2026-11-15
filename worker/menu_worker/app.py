import os
import re

from menu_worker.celery_runtime import create_app
from menu_worker.config import load_settings
from menu_worker.discovery_settings import load_routes


settings = load_settings()
beat_schedule = {
    "runtime-heartbeat": {
        "task": "runtime.heartbeat",
        "schedule": settings.beat_interval_seconds,
        "options": {"expires": settings.beat_interval_seconds},
    },
}
queues = ("content-generation",)
discovery_routes = load_routes(os.getenv("ANIME_DISCOVERY_ROUTES", ""))
if discovery_routes:
    raw_interval = os.getenv("ANIME_DISCOVERY_INTERVAL_SECONDS", "900")
    if not re.fullmatch(r"[0-9]{1,5}", raw_interval) or not 5 <= int(raw_interval) <= 86400:
        raise ValueError("ANIME_DISCOVERY_INTERVAL_SECONDS must be an integer from 5 to 86400")
    interval = int(raw_interval)
    queues += ("anime-discovery",)
    for source_id, content_type in discovery_routes:
        beat_schedule[f"anime-article-discovery-{source_id}-{content_type}"] = {
            "task": "content.anime_discover",
            "args": (source_id, content_type),
            "schedule": interval,
            "options": {"queue": "anime-discovery", "expires": interval},
        }

app = create_app(
    "menu_runtime", settings,
    modules=("menu_worker.tasks", "menu_worker.job_tasks"),
    default_queue="runtime", other_queues=queues, result_prefix="menu-runtime:",
)
app.conf.update(
    task_routes={"content.fake_generate": {"queue": "content-generation"}},
    beat_max_loop_interval=5,
    beat_schedule=beat_schedule,
)
