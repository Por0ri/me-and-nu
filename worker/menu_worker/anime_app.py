"""Application for the isolated, offline anime queue probe."""
import os
from urllib.parse import urlsplit

from menu_worker.celery_runtime import create_app
from menu_worker.config import load_settings


settings = load_settings({
    "CELERY_BROKER_URL": os.getenv("CELERY_BROKER_URL", "redis://anime-redis:6379/0"),
    "CELERY_RESULT_BACKEND": os.getenv("CELERY_RESULT_BACKEND", "redis://anime-redis:6379/1"),
})
if any(urlsplit(url).hostname != "anime-redis" for url in (settings.broker_url, settings.result_backend)):
    raise ValueError("The anime probe must use its dedicated anime-redis service")

app = create_app(
    "menu_anime_probe", settings, modules=("menu_worker.anime_tasks",),
    default_queue="anime-generation", result_prefix="menu-anime-probe:",
)
