"""Celery application for operator-submitted single-article generation."""
import os
from urllib.parse import urlsplit

from menu_worker.celery_runtime import create_app
from menu_worker.config import load_settings


settings = load_settings({
    "CELERY_BROKER_URL": os.getenv("CELERY_BROKER_URL", "redis://generation-redis:6379/0"),
    "CELERY_RESULT_BACKEND": os.getenv("CELERY_RESULT_BACKEND", "redis://generation-redis:6379/1"),
})
if any(urlsplit(url).hostname != "generation-redis" for url in (settings.broker_url, settings.result_backend)):
    raise ValueError("Live generation must use its dedicated generation-redis service")

# The agent gets up to 3600s. Allow time to stop the subprocess, persist the result
# and release the claim before Celery terminates the worker or Redis redelivers.
app = create_app(
    "menu_anime_generation", settings, modules=("menu_worker.generation_tasks",),
    default_queue="anime-generation-live", result_prefix="menu-anime-generation:",
    soft_time_limit=3660, time_limit=3720, visibility_timeout=3900,
)
