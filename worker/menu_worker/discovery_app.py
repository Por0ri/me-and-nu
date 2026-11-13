"""Consume periodic article-discovery tasks on the task runtime's broker."""
from menu_worker.celery_runtime import create_app
from menu_worker.config import load_settings
from menu_worker.discovery import run_discovery_cycle


app = create_app(
    "menu_anime_discovery", load_settings(), modules=(),
    default_queue="anime-discovery", result_prefix="menu-runtime:",
    soft_time_limit=300, time_limit=330, visibility_timeout=420,
)


@app.task(name="content.anime_discover", shared=False)
def anime_discover(source_id: str, content_type: str) -> dict:
    return run_discovery_cycle(source_id, content_type)
