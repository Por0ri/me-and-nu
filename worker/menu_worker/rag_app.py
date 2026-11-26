"""Dedicated RAG queue; importing the app never enables paid generation."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from menu_worker.celery_runtime import create_app
from menu_worker.config import load_settings
from menu_worker.rag_tasks import QUEUE_NAME, execute_delivery, register_task


def runner_factory():
    if os.getenv("RAG_GENERATION_ENABLED", "false") != "true":
        return lambda job_id: {"job_id": job_id, "processed": False, "reason": "rag_generation_disabled"}
    from menu_worker.rag_native import RagNativeConfig, build_native_callbacks
    from menu_worker.rag_store import RagStore
    path = Path(os.environ["RAG_RUNTIME_CONFIG_FILE"])
    if not path.is_absolute() or path.resolve(strict=True) != path or path.stat().st_size > 16384:
        raise ValueError("invalid_rag_runtime_config")
    values = json.loads(path.read_text(encoding="utf-8"))
    if values.get("enabled") is not True:
        return lambda job_id: {"job_id": job_id, "processed": False, "reason": "rag_generation_disabled"}
    config = RagNativeConfig(**values)
    preflight, run_native = build_native_callbacks(config)
    root = Path(os.environ["CONTENT_JOB_ARTIFACT_ROOT"])
    store = RagStore()
    return lambda job_id: execute_delivery(store, root, job_id, run_native=run_native,
                                           preflight=preflight, enabled=True)


settings = load_settings({
    "CELERY_BROKER_URL": os.getenv("CELERY_BROKER_URL", "redis://generation-redis:6379/0"),
    "CELERY_RESULT_BACKEND": os.getenv("CELERY_RESULT_BACKEND", "redis://generation-redis:6379/1"),
})
if any(urlsplit(value).hostname != "generation-redis" for value in (settings.broker_url, settings.result_backend)):
    raise ValueError("RAG generation requires the dedicated generation broker")
app = create_app("menu_rag_generation", settings, modules=(), default_queue=QUEUE_NAME,
                 result_prefix="menu-rag-generation:", soft_time_limit=3660,
                 time_limit=3720, visibility_timeout=3900)
register_task(app, runner_factory)
