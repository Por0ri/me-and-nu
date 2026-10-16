"""Shared low-rate host pacing for read-only source collection."""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

_LOCK = threading.Lock()
_NEXT_ALLOWED_AT: dict[str, float] = {}


def pace_host(url: str, minimum_interval_seconds: float = 2.0) -> None:
    """Reserve a request slot so concurrent collectors do not burst one host."""
    host = (urlparse(url).hostname or "").lower()
    if not host or minimum_interval_seconds <= 0:
        return
    with _LOCK:
        now = time.monotonic()
        scheduled = max(now, _NEXT_ALLOWED_AT.get(host, now))
        _NEXT_ALLOWED_AT[host] = scheduled + minimum_interval_seconds
    delay = scheduled - now
    if delay > 0:
        time.sleep(delay)
