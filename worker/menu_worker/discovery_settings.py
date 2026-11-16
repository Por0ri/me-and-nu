"""Routes allowed to start a periodic Agent listing scan."""
import re


def load_routes(raw: str) -> tuple[tuple[str, str], ...]:
    if not raw.strip():
        return ()
    routes = []
    for entry in raw.split(","):
        source_id, _, content_type = entry.strip().partition(":")
        if not re.fullmatch(r"[a-z0-9_]{1,100}", source_id) or content_type not in {"news", "event", "goods"}:
            raise ValueError("ANIME_DISCOVERY_ROUTES requires comma-separated source:type entries (news, event, goods)")
        if (source_id, content_type) in routes:
            raise ValueError("ANIME_DISCOVERY_ROUTES contains a duplicate route")
        routes.append((source_id, content_type))
    return tuple(routes)


def candidate_limit(raw: str) -> int:
    if not re.fullmatch(r"[0-9]{1,2}", raw) or not 1 <= int(raw) <= 20:
        raise ValueError("ANIME_DISCOVERY_LIMIT must be an integer from 1 to 20")
    return int(raw)
