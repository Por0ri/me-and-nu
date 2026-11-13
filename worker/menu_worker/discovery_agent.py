"""Read one configured listing with the installed Agent; never run its generation pipeline."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.parse import urljoin, urlsplit

from menu_worker.anime_catalog import AnimeInputError, canonical_anime_url, generation_article_key, validate_anime_source
from menu_worker.real_generation import AGENT_PYTHON, AGENT_ROOT, _environment


class DiscoveryError(RuntimeError):
    """A bounded error code suitable for Celery logs."""


class _DeadlineExceeded(Exception):
    pass


def _deadline_expired(signum, frame):
    raise _DeadlineExceeded


@contextmanager
def _until(seconds):
    # This code runs on the main thread of a dedicated Linux Agent subprocess.
    # Bound a whole sequence of HTTP requests, redirects, robots checks and pacing.
    previous = signal.signal(signal.SIGALRM, _deadline_expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _read_page(url, source):
    from ani_menu_agent.sources.page_collector import _get_html
    from bs4 import BeautifulSoup

    # Download only the article. Asset hosts, related pages, and CTA targets
    # do not establish an article's publisher or its identity.
    html, final_url = _get_html(url, source.model_copy(update={"allowed_asset_hosts": []}), 8.0)
    return BeautifulSoup(html, "html.parser"), final_url


def _original_article(candidate, outlet, sources, content_type):
    from ani_menu_agent.cli import _registered_original_source_link
    from ani_menu_agent.sources.page_collector import _article_outbound_links
    from ani_menu_agent.work_catalog import registered_service_work_ids

    if candidate.source_id != outlet.id or content_type not in candidate.content_types:
        return None
    url = str(candidate.url)
    canonical_anime_url(url)  # Reject non-public or credential-bearing candidate URLs before fetch.
    if outlet.role == "discovery":
        lead_host = urlsplit(url).hostname
        outlet_host = urlsplit(str(outlet.url)).hostname
        if lead_host == outlet_host or lead_host.endswith("." + outlet_host):
            soup, final_url = _read_page(url, outlet)
            links = _article_outbound_links(soup, final_url, outlet.id)
            original = _registered_original_source_link(links, sources, content_type)
            if original is None:
                return None
            source, url, _ = original
        else:
            # RSS sometimes links directly to the original publisher. Match its exact host.
            matching = [source for source in sources if source.status == "active"
                        and source.role == "primary" and source.collection == "html"
                        and content_type in {kind.value for kind in source.content_types}
                        and urlsplit(str(source.url)).hostname == lead_host]
            if len(matching) != 1:
                return None
            source = matching[0]
    else:
        source = outlet
    work_ids = registered_service_work_ids(source.work_title_ko, source.work_title_original)
    if len(work_ids) != 1:
        return None
    work_id = next(iter(work_ids))
    article = {"source_id": source.id, "source_url": url, "content_type": content_type, "menu_work_id": work_id}
    identity = validate_anime_source(**article)
    if source.collection == "html":
        soup, final_url = _read_page(url, source)
        # A redirect to the front page, a different property or an asset is not the article.
        final_identity = validate_anime_source(source.id, final_url, content_type, work_id)
        hints = {tag["href"].strip() for tag in soup.select('head link[rel~="canonical"][href]')
                 if tag["href"].strip()}
        try:
            hint = urljoin(final_url, next(iter(hints))) if len(hints) == 1 else None
        except ValueError:
            hint = None
        identity = validate_anime_source(source.id, final_identity, content_type, work_id, canonical_url=hint)
    article["canonical_url"] = generation_article_key(source.id, identity)
    return article


def _collect(source_id: str, content_type: str, limit: int) -> dict:
    import httpx
    from ani_menu_agent.discovery import collect_candidates, supports_discovery_adapter
    from ani_menu_agent.sources import load_source_registry

    sources = load_source_registry().sources
    source = next((item for item in sources if item.id == source_id), None)
    if (source is None or source.status != "active" or not supports_discovery_adapter(source)
            or content_type not in {kind.value for kind in source.content_types}):
        raise DiscoveryError("discovery_source_unavailable")
    deadline = time.monotonic() + 210
    try:
        with _until(75):
            items, errors = collect_candidates(source_ids={source.id}, content_type=content_type, limit=limit,
                                               timeout=8.0, exhaustive_listing=False)
    except _DeadlineExceeded:
        raise DiscoveryError("discovery_listing_timeout") from None
    if errors and not items:
        raise DiscoveryError("discovery_listing_failed")
    articles = []
    unresolved = fetch_failed = 0
    for index, item in enumerate(items):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            fetch_failed += len(items) - index
            break
        try:
            with _until(min(remaining, 45)):
                article = _original_article(item, source, sources, content_type)
        except AnimeInputError as exc:
            if exc.code in {"catalog_unavailable", "invalid_catalog"}:
                raise DiscoveryError("discovery_catalog_unavailable") from None
            article = None
        except (ValueError, httpx.HTTPError, _DeadlineExceeded):
            # The Agent also signals robots/DNS/HTML failures as ValueError.
            fetch_failed += 1
            continue
        if article is None:
            unresolved += 1
        else:
            articles.append(article)
    report = {"articles": articles, "discovered": len(items), "unresolved": unresolved, "fetch_failed": fetch_failed}
    if errors:
        report["listing_partial"] = True
    return report


def discover_articles(source_id: str, content_type: str, limit: int) -> dict:
    """Run one bounded pass with the Agent interpreter and no inherited keys or database URLs."""
    if not re.fullmatch(r"[a-z0-9_]{1,100}", source_id) or content_type not in {"news", "event", "goods"}:
        raise DiscoveryError("invalid_discovery_route")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise DiscoveryError("invalid_discovery_limit")
    env = _environment()
    env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parents[1]), env["PYTHONPATH"]])
    if (AGENT_ROOT / ".env").exists():
        raise DiscoveryError("discovery_runtime_unavailable")
    try:
        result = subprocess.run(
            [str(AGENT_PYTHON), "-B", "-s", "-m", "menu_worker.discovery_agent",
             str(os.getpid()), source_id, content_type, str(limit)],
            cwd=AGENT_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=240, check=True, encoding="utf-8",
        )
        report = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise DiscoveryError("discovery_timeout") from None
    except (ValueError, OSError, subprocess.CalledProcessError):
        raise DiscoveryError("discovery_runtime_unavailable") from None
    if "error" in report:
        raise DiscoveryError(report["error"])
    return report


if __name__ == "__main__":
    import ctypes

    # Linux parent-death signal also covers a hard Celery time limit (SIGKILL).
    # Recheck the parent after registering to close the fork/exec race.
    if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != int(sys.argv[1]):
        sys.exit(1)
    try:
        result = _collect(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    except DiscoveryError as exc:
        result = {"error": str(exc)}
    print(json.dumps(result))
