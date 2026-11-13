"""Run one listing scan, then hand verified articles to the existing generation dispatcher."""
import logging
import os

from menu_worker.anime_catalog import AnimeInputError
from menu_worker.discovery_agent import discover_articles
from menu_worker.discovery_settings import candidate_limit, load_routes
from menu_worker.job_contract import JobInputError, validate_job_request
from menu_worker.job_store import JobConflict, JobStore


logger = logging.getLogger(__name__)


def run_discovery_cycle(source_id: str, content_type: str, *, store=None, discover=None) -> dict:
    if (source_id, content_type) not in load_routes(os.getenv("ANIME_DISCOVERY_ROUTES", "")):
        raise ValueError("Discovery route is not enabled on this worker")
    limit = candidate_limit(os.getenv("ANIME_DISCOVERY_LIMIT", "5"))
    if store is None:
        store = JobStore()
    if discover is None:
        discover = discover_articles
    result = dict(source_id=source_id, content_type=content_type, status="completed", discovered=0,
                  unresolved=0, fetch_failed=0, submitted=0, duplicates=0, rejected=0)
    with store.discovery_slot(source_id, content_type) as acquired:
        if not acquired:
            return {**result, "status": "already_running"}
        scan = discover(source_id, content_type, limit)
        result.update({name: scan[name] for name in ("discovered", "unresolved", "fetch_failed")})
        if scan["discovered"] > limit or len(scan["articles"]) > limit:
            raise RuntimeError("Discovery exceeded the configured candidate limit")
        for article in scan["articles"]:
            try:
                if article["content_type"] != content_type:
                    raise JobInputError("Candidate type differs from its discovery route")
                # Stable across Beat ticks and deliveries. A different work/type or a
                # manual request still hits the shared unique source/article constraint.
                fields = {**article, "idempotency_key": "discovery"}
                _, _, digest = validate_job_request(fields, "anime_generate")
                fields["idempotency_key"] = "discovery:" + digest
                job = store.submit(fields, job_kind="anime_generate")
                result["submitted" if job["created"] else "duplicates"] += 1
            except JobConflict as exc:
                if exc.code != "candidate_exists":
                    raise
                result["duplicates"] += 1
            except (AnimeInputError, JobInputError) as exc:
                if exc.code in {"invalid_catalog", "catalog_unavailable"}:
                    raise
                result["rejected"] += 1
                logger.info("Discovery admission rejected for %s/%s (%s)", source_id, content_type, exc.code)
    if result["fetch_failed"] or scan.get("listing_partial"):
        result["status"] = "partial"
        logger.warning("Incomplete listing scan for %s/%s (article fetch failures: %d)",
                       source_id, content_type, result["fetch_failed"])
    return result
