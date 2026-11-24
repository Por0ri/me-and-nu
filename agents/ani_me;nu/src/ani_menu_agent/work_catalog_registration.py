"""Resolve work identities and store source-grounded temporary IDs."""

from __future__ import annotations

import csv
import os
import re
import tempfile
import unicodedata
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from ani_menu_agent import work_catalog
from ani_menu_agent.models import StructuredWorkReference, WorkMatchEvidence
from ani_menu_agent.sources.http_policy import pace_host
from ani_menu_agent.sources.registry import load_source_registry

MAX_CATALOG_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_KITSU_PAGE_LIMIT = 20
_CATALOG_FIELDS = (
    "menu_work_id",
    "title_ko",
    "title_ko_source",
    "title_ko_check",
    "title_romaji",
    "title_english",
    "title_native",
    "first_year",
    "last_year",
    "n_entries",
    "popularity",
    "anilist_ids",
)
_EXTERNAL_FIELDS = (
    "menu_work_id",
    "source_id",
    "external_id",
    "source_url",
    "canonical_title",
    "format",
    "year",
    "registered_at",
)
_DISCOVERED_FIELDS = (
    "kitsu_id",
    "canonical_title",
    "title_romaji",
    "title_english",
    "title_native",
    "format",
    "first_year",
    "source_title",
    "source_url",
    "status",
    "first_seen_at",
    "last_seen_at",
)


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _title_values(attributes: dict[str, Any]) -> list[str]:
    values = [attributes.get("canonicalTitle")]
    titles = attributes.get("titles")
    if isinstance(titles, dict):
        values.extend(titles.values())
    elif isinstance(titles, list):
        values.extend(titles)
    return list(
        dict.fromkeys(
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip()
        )
    )


def _title_match(
    headlines: list[str], rows: list[dict[str, Any]]
) -> list[tuple[str, str, dict[str, Any]]]:
    matches: list[tuple[str, str, dict[str, Any]]] = []
    normalized_headlines = [(_normalize_title(value), value) for value in headlines]
    located: list[tuple[str, str, dict[str, Any], int, int, int]] = []
    for row in rows:
        attrs = row.get("attributes")
        if not isinstance(attrs, dict) or not str(row.get("id", "")).isdigit():
            continue
        for catalog_title in _title_values(attrs):
            normalized_catalog_title = _normalize_title(catalog_title)
            if len(normalized_catalog_title) < 4:
                continue
            for headline_index, (normalized_headline, _headline) in enumerate(
                normalized_headlines
            ):
                start = normalized_headline.find(normalized_catalog_title)
                if start >= 0:
                    located.append(
                        (
                            str(row["id"]),
                            catalog_title,
                            attrs,
                            headline_index,
                            start,
                            start + len(normalized_catalog_title),
                        )
                    )
    # Prefer a complete, more specific catalog title over its overlapping parent
    # title (e.g. a named spin-off should not also match the main series).
    for candidate in located:
        _id, _title, _attrs, headline_index, start, end = candidate
        if any(
            other[3] == headline_index
            and other[5] - other[4] > end - start
            and other[4] < end
            and start < other[5]
            for other in located
        ):
            continue
        matches.append(candidate[:3])
    unique: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for match in matches:
        unique.setdefault(match[0], match)
    return list(unique.values())


def _catalog_search_queries(headline: str) -> list[str]:
    """Break long editorial headlines into likely title phrases for catalog search.

    Search variants only improve Kitsu retrieval. A result is still accepted only
    when its complete catalog title occurs in the original headline, so fragments
    cannot create a weaker work match by themselves.
    """
    separators = re.compile(r"\s*(?:[:：|｜—–]|\s/\s|\s·\s|\s-\s)\s*")
    parts = [part.strip(" \t\r\n[]【】「」『』()（）") for part in separators.split(headline)]
    queries = [headline.strip(), *parts]
    unique: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = _normalize_title(query)
        if len(normalized) < 4 or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(query)
    return unique[:4]


@contextmanager
def _catalog_lock(catalog_dir: Path) -> Iterator[None]:
    """Serialize ID assignment across concurrent local crawler processes."""
    catalog_dir.mkdir(parents=True, exist_ok=True)
    lock_path = catalog_dir / ".menu_work_catalog.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+b") as stream:
        if os.name == "nt":
            import msvcrt

            if lock_path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return [
            {field: str(row.get(field) or "") for field in fields}
            for row in csv.DictReader(stream)
        ]


def _atomic_write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _year(value: Any) -> str:
    match = re.match(r"^(\d{4})", str(value or ""))
    return match.group(1) if match else ""


def _external_row(
    menu_work_id: int, kitsu_id: str, attributes: dict[str, Any]
) -> dict[str, str]:
    canonical = str(attributes.get("canonicalTitle") or "").strip()
    return {
        "menu_work_id": str(menu_work_id),
        "source_id": "kitsu_anime_catalog",
        "external_id": kitsu_id,
        "source_url": f"https://kitsu.io/anime/{kitsu_id}",
        "canonical_title": canonical,
        "format": str(attributes.get("subtype") or ""),
        "year": _year(attributes.get("startDate")),
        "registered_at": datetime.now(UTC).isoformat(),
    }


def _record_discovered_work(
    kitsu_id: str,
    attributes: dict[str, Any],
    source_titles: list[str],
    source_url: str | None,
) -> None:
    """Store an unconfirmed or not-yet-registerable title for later review."""
    catalog_dir = work_catalog.work_registry_directory()
    discovered_path = catalog_dir / "discovered_works.csv"
    titles = attributes.get("titles") or {}
    if not isinstance(titles, dict):
        titles = {}
    now = datetime.now(UTC).isoformat()
    canonical = str(attributes.get("canonicalTitle") or "").strip()
    source_title = " | ".join(
        dict.fromkeys(value.strip() for value in source_titles if value.strip())
    )
    candidate = {
        "kitsu_id": kitsu_id,
        "canonical_title": canonical,
        "title_romaji": str(titles.get("en_jp") or canonical).strip(),
        "title_english": str(titles.get("en") or "").strip(),
        "title_native": str(titles.get("ja_jp") or titles.get("ja") or "").strip(),
        "format": str(attributes.get("subtype") or ""),
        "first_year": _year(attributes.get("startDate")),
        "source_title": source_title,
        "source_url": str(source_url or ""),
        "status": "pending_team_review",
        "first_seen_at": now,
        "last_seen_at": now,
    }
    with _catalog_lock(catalog_dir):
        rows = _read_csv(discovered_path, _DISCOVERED_FIELDS)
        for row in rows:
            if row["kitsu_id"] != kitsu_id:
                continue
            row["last_seen_at"] = now
            for key in ("source_title", "source_url"):
                values = [*row[key].split(" | "), *candidate[key].split(" | ")]
                row[key] = " | ".join(dict.fromkeys(value for value in values if value))
            break
        else:
            rows.append(candidate)
        _atomic_write_csv(discovered_path, _DISCOVERED_FIELDS, rows)


def valid_source_url(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = httpx.URL(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.host)
    except httpx.InvalidURL:
        return False


def record_unresolved_source_title(title: str, source_url: str) -> None:
    normalized = _normalize_title(title)
    if normalized and valid_source_url(source_url):
        _record_discovered_work(
            f"title:{normalized}", {"canonicalTitle": title}, [title], source_url,
        )


def _persist_kitsu_work(
    kitsu_id: str,
    attributes: dict[str, Any],
    matched_title: str,
    *,
    existing_menu_work_id: int | None = None,
    source_url: str | None = None,
    audit: dict[str, Any] | None = None,
) -> int | None:
    catalog_dir = work_catalog.work_registry_directory()
    works_path = work_catalog.official_work_directory() / "menu_works.csv"
    agent_works_path = catalog_dir / "agent_works.csv"
    external_path = work_catalog.work_external_entries_path()
    with _catalog_lock(catalog_dir):
        if not works_path.is_file():
            raise FileNotFoundError(f"Missing official work catalog: {works_path}")
        works = _read_csv(works_path, _CATALOG_FIELDS)
        agent_works = _read_csv(agent_works_path, _CATALOG_FIELDS)
        external_entries = _read_csv(external_path, _EXTERNAL_FIELDS)
        created = False
        official_ids = {int(row["menu_work_id"]) for row in works}
        valid_work_ids = official_ids | {int(row["menu_work_id"]) for row in agent_works}
        external_link = next((
            row for row in external_entries
            if row["source_id"] == "kitsu_anime_catalog" and row["external_id"] == kitsu_id
        ), None)
        linked_id = int(external_link["menu_work_id"]) if external_link else None
        if linked_id in official_ids:
            if existing_menu_work_id is not None and existing_menu_work_id != linked_id:
                return None
            if audit is not None:
                audit["status"] = "mapped_existing_work"
            return linked_id

        work_catalog.refresh_work_catalog_caches()
        existing_ids = set(work_catalog.resolve_title_work_ids(matched_title, source_url))
        # Recheck the file being written while holding the cross-process lock;
        # another process may have registered the same title since this
        # process last refreshed its in-memory catalog cache.
        catalog_title_keys = {
            _normalize_title(value)
            for value in _title_values(attributes)
            if _normalize_title(value)
        }
        catalog_rows = [*works, *agent_works, *work_catalog.load_work_entries()]
        catalog_rows.extend(
            {
                "menu_work_id": alias["menu_work_id"],
                "title_native": alias["alias"],
            }
            for alias in work_catalog.load_work_aliases()
            if work_catalog._alias_scope_matches(alias, source_url)
        )
        exact_file_ids: set[int] = set()
        for row in catalog_rows:
            try:
                row_work_id = int(row["menu_work_id"])
            except (KeyError, TypeError, ValueError):
                continue
            normalized_titles = {
                _normalize_title(row.get(field, ""))
                for field in (
                    "title_ko",
                    "title_romaji",
                    "title_english",
                    "title_native",
                )
                if row.get(field)
            }
            if normalized_titles & catalog_title_keys:
                exact_file_ids.add(work_catalog._franchise_work_id(row_work_id))
        exact_official_ids = exact_file_ids & official_ids
        if len(exact_official_ids) > 1:
            return None
        if linked_id is not None:
            if not exact_official_ids and linked_id not in valid_work_ids:
                return None
            existing_ids = exact_official_ids or {linked_id}
        else:
            existing_ids = exact_official_ids or exact_file_ids or existing_ids
            if existing_ids and not exact_file_ids and existing_menu_work_id is None:
                return None
        if existing_menu_work_id is not None:
            if existing_menu_work_id not in valid_work_ids:
                return None
            if existing_ids and existing_menu_work_id not in existing_ids:
                return None
            menu_work_id = existing_menu_work_id
        elif len(existing_ids) == 1:
            # Kitsu may return a franchise title already present under a local
            # alias. Reuse its canonical ID instead of creating a duplicate.
            menu_work_id = next(iter(existing_ids))
            if menu_work_id not in valid_work_ids:
                return None
        elif existing_ids:
            # A title that resolves to multiple works is not safe to register.
            return None
        else:
            # New IDs are allocated only after an exact title was found in the
            # Kitsu anime catalog (the caller reaches here after title evidence
            # validation). Locking above prevents concurrent duplicate IDs.
            try:
                numeric_kitsu_id = int(kitsu_id)
            except (TypeError, ValueError):
                return None
            if numeric_kitsu_id <= 0:
                return None
            menu_work_id = 1_000_000 + numeric_kitsu_id
            if menu_work_id in valid_work_ids:
                return None
            titles = attributes.get("titles") or {}
            if not isinstance(titles, dict):
                titles = {}
            canonical = str(attributes.get("canonicalTitle") or matched_title).strip()
            romaji = str(titles.get("en_jp") or canonical).strip()
            english = str(titles.get("en") or "").strip()
            native = str(titles.get("ja_jp") or titles.get("ja") or "").strip()
            agent_works.append(
                {
                    "menu_work_id": str(menu_work_id),
                    "title_ko": "",
                    "title_ko_source": "",
                    "title_ko_check": "",
                    "title_romaji": romaji,
                    "title_english": english,
                    "title_native": native,
                    "first_year": _year(attributes.get("startDate")),
                    "last_year": _year(attributes.get("endDate")),
                    "n_entries": "0",
                    "popularity": "0",
                    "anilist_ids": "",
                }
            )
            _atomic_write_csv(agent_works_path, _CATALOG_FIELDS, agent_works)
            created = True

        if external_link is None:
            external_entries.append(_external_row(menu_work_id, kitsu_id, attributes))
        elif linked_id != menu_work_id:
            external_link["menu_work_id"] = str(menu_work_id)
        if linked_id != menu_work_id:
            _atomic_write_csv(external_path, _EXTERNAL_FIELDS, external_entries)
        work_catalog.refresh_work_catalog_caches()
        if audit is not None:
            audit["status"] = (
                "registered_temporary_kitsu_work" if created else "mapped_existing_work"
            )
        return menu_work_id


def register_new_work_from_source_title(
    title: str, source_url: str, *, audit: dict[str, Any] | None = None
) -> int | None:
    """Register a source-grounded new title with a stable temporary work ID.

    The caller must persist the original capture and ERD entity before handing the
    candidate to content generation. Exact existing titles are reused, and the
    canonical catalog remains read-only.
    """
    title = " ".join(str(title or "").split()).strip()
    if not title or len(title) > 200 or len(_normalize_title(title)) < 3:
        if audit is not None:
            audit["status"] = "invalid_title"
        return None
    if not valid_source_url(source_url):
        if audit is not None:
            audit["status"] = "invalid_source_url"
        return None

    catalog_dir = work_catalog.work_registry_directory()
    works_path = work_catalog.official_work_directory() / "menu_works.csv"
    agent_works_path = catalog_dir / "agent_works.csv"
    with _catalog_lock(catalog_dir):
        work_catalog.refresh_work_catalog_caches()
        if not works_path.is_file():
            raise FileNotFoundError(f"Missing official work catalog: {works_path}")
        works = _read_csv(works_path, _CATALOG_FIELDS)
        agent_works = _read_csv(agent_works_path, _CATALOG_FIELDS)
        normalized = _normalize_title(title)
        matching_ids: set[int] = set()
        catalog_rows = [*works, *agent_works, *work_catalog.load_work_entries()]
        catalog_rows.extend(
            {"menu_work_id": row["menu_work_id"], "title_native": row["alias"]}
            for row in work_catalog.load_work_aliases()
            if work_catalog._alias_scope_matches(row, source_url)
        )
        catalog_rows.extend(
            {
                "menu_work_id": row["menu_work_id"],
                "title_native": row["canonical_title"],
            }
            for row in work_catalog.load_work_external_entries()
        )
        for row in catalog_rows:
            try:
                work_id = int(row["menu_work_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if any(
                _normalize_title(str(row.get(field) or "")) == normalized
                for field in (
                    "title_ko",
                    "title_romaji",
                    "title_english",
                    "title_native",
                    "canonical_title",
                )
            ):
                matching_ids.add(work_id)
        official_ids = {int(row["menu_work_id"]) for row in works}
        occupied_ids = official_ids | {int(row["menu_work_id"]) for row in agent_works}
        matching_ids = matching_ids & official_ids or matching_ids
        if len(matching_ids) == 1 and matching_ids <= occupied_ids:
            existing_id = next(iter(matching_ids))
            if audit is not None:
                audit.update({"status": "reused_existing_exact_title", "menu_work_id": existing_id})
            return existing_id

        menu_work_id = 2_000_000 + zlib.crc32(normalized.encode("utf-8")) % 1_000_000_000
        if matching_ids:
            held_reason = "exact_title_maps_to_multiple_ids"
        elif menu_work_id in occupied_ids:
            held_reason = "temporary_id_collision"
        else:
            held_reason = None
        if held_reason is None:
            korean = bool(re.search(r"[\uac00-\ud7a3]", title))
            japanese = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", title))
            agent_works.append(
                {
                    "menu_work_id": str(menu_work_id),
                    "title_ko": title if korean else "",
                    "title_ko_source": source_url if korean else "",
                    "title_ko_check": "source-grounded; review pending" if korean else "",
                    "title_romaji": "",
                    "title_english": title if not korean and not japanese else "",
                    "title_native": title if japanese else "",
                    "first_year": "",
                    "last_year": "",
                    "n_entries": "0",
                    "popularity": "0",
                    "anilist_ids": "",
                }
            )
            _atomic_write_csv(agent_works_path, _CATALOG_FIELDS, agent_works)
            work_catalog.refresh_work_catalog_caches()
            if audit is not None:
                audit.update(
                    {"status": "registered_temporary_source_title", "menu_work_id": menu_work_id}
                )
            return menu_work_id

    record_unresolved_source_title(title, source_url)
    if audit is not None:
        audit["status"] = held_reason
    return None


def register_unmatched_work_from_kitsu(
    titles: list[str],
    *,
    structured_references: list[StructuredWorkReference] | None = None,
    existing_menu_work_id: int | None = None,
    source_url: str | None = None,
    audit: dict[str, Any] | None = None,
    timeout: float = 12.0,
) -> WorkMatchEvidence | None:
    """Map an exact Kitsu title or register a catalog-confirmed new work.

    The page body, generated text, and character names are not searched for a new
    series name. A Kitsu search result must contain a full catalog title verbatim
    (after typography/spacing normalization) in an article headline or an explicit
    structured ``about`` reference. Existing works receive an external title
    mapping; a distinct Kitsu-confirmed anime receives a deterministic temporary ID.
    Ambiguous or unconfirmed titles remain in discovered_works.csv for review.
    """
    source = next(
        (
            item
            for item in load_source_registry().sources
            if item.id == "kitsu_anime_catalog"
            and item.status == "active"
            and item.role == "reference"
            and item.collection == "api"
        ),
        None,
    )
    if source is None:
        return None

    eligible_titles = [str(title).strip() for title in titles if str(title).strip()]
    eligible_titles.extend(
        reference.name.strip()
        for reference in structured_references or []
        if reference.relation == "about" and reference.name.strip()
    )
    eligible_titles = list(dict.fromkeys(eligible_titles))
    if not eligible_titles:
        return None

    headers = {
        "Accept": "application/vnd.api+json, application/json",
        "User-Agent": "me-nu-content-agent/0.3 (anime work catalog registration)",
    }
    exact_matches: dict[str, tuple[str, dict[str, Any]]] = {}
    lookup_failed = False
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        queried: set[str] = set()
        for headline in eligible_titles:
            for query in _catalog_search_queries(headline):
                normalized_query = _normalize_title(query)
                if normalized_query in queried:
                    continue
                queried.add(normalized_query)
                params = urlencode(
                    {"filter[text]": query, "page[limit]": MAX_KITSU_PAGE_LIMIT}
                )
                endpoint = f"{str(source.url).rstrip('/')}?{params}"
                try:
                    pace_host(endpoint, source.min_interval_seconds)
                    response = client.get(endpoint)
                    response.raise_for_status()
                    if len(response.content) > MAX_CATALOG_RESPONSE_BYTES:
                        lookup_failed = True
                        continue
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    lookup_failed = True
                    continue
                rows = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(rows, list):
                    lookup_failed = True
                    continue
                for kitsu_id, matched_title, attributes in _title_match([headline], rows):
                    exact_matches.setdefault(kitsu_id, (matched_title, attributes))

    if len(exact_matches) > 1:
        normalized_headlines = [_normalize_title(value) for value in eligible_titles]
        specific_matches = {
            kitsu_id: (matched_title, attributes)
            for kitsu_id, (matched_title, attributes) in exact_matches.items()
            if not any(
                other_id != kitsu_id
                and len(_normalize_title(other_title)) > len(_normalize_title(matched_title))
                and _normalize_title(matched_title) in _normalize_title(other_title)
                and any(
                    _normalize_title(other_title) in normalized_headline
                    for normalized_headline in normalized_headlines
                )
                for other_id, (other_title, _other_attributes) in exact_matches.items()
            )
        }
        exact_matches = specific_matches

    if len(exact_matches) != 1:
        if audit is not None:
            if exact_matches:
                audit["status"] = "kitsu_match_ambiguous"
            elif lookup_failed:
                audit["status"] = "kitsu_lookup_unavailable"
            else:
                audit["status"] = "kitsu_match_missing"
        for kitsu_id, (matched_title, attributes) in exact_matches.items():
            _record_discovered_work(
                kitsu_id, attributes, [*eligible_titles, matched_title], source_url,
            )
        return None
    kitsu_id, (matched_title, attributes) = next(iter(exact_matches.items()))
    menu_work_id = _persist_kitsu_work(
        kitsu_id,
        attributes,
        matched_title,
        existing_menu_work_id=existing_menu_work_id,
        source_url=source_url,
        audit=audit,
    )
    if menu_work_id is None:
        _record_discovered_work(
            kitsu_id,
            attributes,
            [*eligible_titles, matched_title],
            source_url,
        )
        if audit is not None:
            audit.update(
                {
                    "status": "new_work_logged_pending_review",
                    "kitsu_id": kitsu_id,
                    "matched_title": matched_title,
                }
            )
        return None
    if audit is not None:
        audit.update(
            {
                "kitsu_id": kitsu_id,
                "matched_title": matched_title,
                "menu_work_id": menu_work_id,
            }
        )
    return WorkMatchEvidence(
        menu_work_id=menu_work_id,
        basis=(
            "kitsu_catalog_entry_title"
            if existing_menu_work_id is not None
            else "kitsu_catalog_title"
        ),
        matched_value=matched_title,
        evidence_url=f"https://kitsu.io/anime/{kitsu_id}",
        confidence=0.97,
    )
