"""Read-only scope for explicit daily work-bound discovery; never enables Beat routes."""
import csv
import json
import re

from menu_worker import anime_catalog as catalog


DAILY_CONTENT_TYPES = frozenset({"news", "event", "goods"})
MAX_DAILY_ROUTES = 200


def _route_key(route):
    return route["menu_work_id"], route["content_type"], route["source_id"]


def catalog_routes() -> dict:
    """Return registered official news routes with one exact existing service work.

    Source status, endpoint capability and work identity all remain mandatory.
    The daily caller explicitly opts in to work-scoped listing discovery, so the
    general Agent discovery flag is neither changed nor used as an activation flag.
    """
    try:
        root = catalog.CATALOG_ROOT
        sources = json.loads((root / "sources.json").read_text(encoding="utf-8-sig"))["sources"]
        mapping = json.loads((root / "source_type_map.json").read_text(encoding="utf-8-sig"))
        if not isinstance(sources, list) or not isinstance(mapping["types"], dict) or not isinstance(mapping["sources"], dict):
            raise ValueError
        ids = [source["id"] for source in sources]
        if (any(not isinstance(value, str) or not re.fullmatch(r"[a-z0-9_]{1,100}", value) for value in ids)
                or len(set(ids)) != len(ids) or set(mapping["sources"]) - set(ids)):
            raise ValueError
        works = []
        known_ids = set()
        with (root / "menu_works.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not {"menu_work_id", *catalog._TITLE_COLUMNS} <= set(reader.fieldnames or ()):
                raise ValueError
            for row in reader:
                work_id = int(row["menu_work_id"])
                if not 1 <= work_id <= 2**63 - 1 or work_id in known_ids:
                    raise ValueError
                known_ids.add(work_id)
                works.append((work_id, {catalog._normalized_title(row[field]) for field in catalog._TITLE_COLUMNS} - {""}))
        routes, excluded = [], []
        for source in sources:
            kinds = mapping["sources"].get(source["id"], [])
            if (not isinstance(kinds, list) or any(not isinstance(kind, str) or kind not in mapping["types"] for kind in kinds)
                    or len(kinds) != len(set(kinds))):
                raise ValueError
            reason = None
            if source["status"] != "active":
                reason = "source_inactive"
            elif source["role"] != "primary" or source["collection"] != "html" or source.get("category") != "official_news":
                reason = "unsupported_daily_source"
            allowed = set(DAILY_CONTENT_TYPES).intersection(kinds)
            # A dedicated adapter's narrower route capability remains binding.
            # In particular One Piece's numbered-article parser only accepts the
            # publisher's goods category; it cannot discover news/event yet.
            targeted = source.get("targeted_discovery_types", [])
            if not isinstance(targeted, list) or any(not isinstance(kind, str) for kind in targeted):
                raise ValueError
            if targeted:
                allowed.intersection_update(targeted)
            if reason is None and not allowed:
                reason = "unsupported_daily_type"
            titles = {catalog._normalized_title(source.get(field) or "")
                      for field in ("work_title_ko", "work_title_original")} - {""}
            matches = {work_id for work_id, work_titles in works if titles.intersection(work_titles)}
            if reason is None and len(matches) != 1:
                reason = "source_work_unresolved"
            if reason is not None:
                excluded.append({"source_id": source["id"], "reason": reason})
                continue
            catalog.canonical_anime_url(source["url"])
            work_id = next(iter(matches))
            routes.extend({"source_id": source["id"], "content_type": kind, "menu_work_id": work_id}
                          for kind in allowed)
        if len(routes) > MAX_DAILY_ROUTES:
            raise ValueError
        return {"routes": sorted(routes, key=_route_key), "excluded": sorted(excluded, key=lambda row: row["source_id"])}
    except catalog.AnimeInputError:
        raise
    except OSError:
        raise catalog.AnimeInputError("catalog_unavailable") from None
    except (ValueError, TypeError, KeyError, AttributeError, csv.Error):
        raise catalog.AnimeInputError("invalid_catalog") from None


def validate_routes(routes, *, current_catalog=True) -> list[dict]:
    """Validate new scope against current catalog, or historical scope structurally."""
    if not isinstance(routes, list) or not 1 <= len(routes) <= MAX_DAILY_ROUTES:
        raise ValueError("invalid_daily_routes")
    normalized, seen = [], set()
    for route in routes:
        if (not isinstance(route, dict) or set(route) != {"source_id", "content_type", "menu_work_id"}
                or not isinstance(route["source_id"], str) or not re.fullmatch(r"[a-z0-9_]{1,100}", route["source_id"])
                or not isinstance(route["content_type"], str) or route["content_type"] not in DAILY_CONTENT_TYPES
                or type(route["menu_work_id"]) is not int or not 1 <= route["menu_work_id"] <= 2**63 - 1):
            raise ValueError("invalid_daily_routes")
        # A publisher/type can never be assigned to two different works.
        identity = route["source_id"], route["content_type"]
        if identity in seen:
            raise ValueError("invalid_daily_routes")
        seen.add(identity)
        normalized.append(dict(route))
    normalized.sort(key=_route_key)
    if current_catalog:
        allowed = {_route_key(route) for route in catalog_routes()["routes"]}
        if any(_route_key(route) not in allowed for route in normalized):
            raise ValueError("daily_route_unavailable")
    return normalized
