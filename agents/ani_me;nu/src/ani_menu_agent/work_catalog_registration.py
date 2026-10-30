"""Register an unlisted anime only after an exact Kitsu catalog title match."""

from __future__ import annotations

import csv
import os
import re
import tempfile
import unicodedata
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
    for row in rows:
        attrs = row.get("attributes")
        if not isinstance(attrs, dict) or not str(row.get("id", "")).isdigit():
            continue
        for catalog_title in _title_values(attrs):
            normalized_catalog_title = _normalize_title(catalog_title)
            if len(normalized_catalog_title) < 4:
                continue
            for normalized_headline, _headline in normalized_headlines:
                if normalized_catalog_title in normalized_headline:
                    matches.append((str(row["id"]), catalog_title, attrs))
                    break
    unique: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for match in matches:
        unique.setdefault(match[0], match)
    return list(unique.values())


@contextmanager
def _catalog_lock(catalog_dir: Path) -> Iterator[None]:
    """Serialize ID assignment across concurrent local crawler processes."""
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


def _work_row(menu_work_id: int, attributes: dict[str, Any]) -> dict[str, str]:
    titles = attributes.get("titles") or {}
    if not isinstance(titles, dict):
        titles = {}
    canonical = str(attributes.get("canonicalTitle") or "").strip()
    start_year = _year(attributes.get("startDate"))
    end_year = _year(attributes.get("endDate")) or start_year
    return {
        "menu_work_id": str(menu_work_id),
        "title_ko": "",
        "title_ko_source": "",
        "title_ko_check": "pending_translation",
        "title_romaji": str(titles.get("en_jp") or canonical).strip(),
        "title_english": str(titles.get("en") or "").strip(),
        "title_native": str(titles.get("ja_jp") or titles.get("ja") or "").strip(),
        "first_year": start_year,
        "last_year": end_year,
        "n_entries": "1",
        "popularity": "0",
        "anilist_ids": "",
    }


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


def _clear_catalog_caches() -> None:
    for function_name in (
        "load_work_catalog",
        "load_work_entries",
        "_work_parent_overrides",
        "load_work_franchises",
        "_aliases",
        "_registered_work_titles",
    ):
        function = getattr(work_catalog, function_name, None)
        if function is not None and hasattr(function, "cache_clear"):
            function.cache_clear()


def _persist_kitsu_work(
    kitsu_id: str, attributes: dict[str, Any], matched_title: str
) -> int | None:
    catalog_dir = work_catalog._CATALOG_DIRECTORY
    works_path = catalog_dir / "menu_works.csv"
    external_path = catalog_dir / "menu_work_external_entries.csv"
    with _catalog_lock(catalog_dir):
        works = _read_csv(works_path, _CATALOG_FIELDS)
        external_entries = _read_csv(external_path, _EXTERNAL_FIELDS)
        external_match = next(
            (
                int(row["menu_work_id"])
                for row in external_entries
                if row["source_id"] == "kitsu_anime_catalog"
                and row["external_id"] == kitsu_id
            ),
            None,
        )
        if external_match is not None:
            return external_match

        _clear_catalog_caches()
        existing_ids = work_catalog.resolve_title_work_ids(matched_title)
        if len(existing_ids) > 1:
            return None
        if existing_ids:
            menu_work_id = existing_ids[0]
        else:
            valid_ids = [
                int(row["menu_work_id"])
                for row in [*works, *external_entries]
                if row["menu_work_id"].isdigit()
            ]
            menu_work_id = max(valid_ids, default=0) + 1
            works.append(_work_row(menu_work_id, attributes))
            _atomic_write_csv(works_path, _CATALOG_FIELDS, works)
            _clear_catalog_caches()

        external_entries.append(_external_row(menu_work_id, kitsu_id, attributes))
        _atomic_write_csv(external_path, _EXTERNAL_FIELDS, external_entries)
        _clear_catalog_caches()
        return menu_work_id


def register_unmatched_work_from_kitsu(
    titles: list[str],
    *,
    structured_references: list[StructuredWorkReference] | None = None,
    timeout: float = 12.0,
) -> WorkMatchEvidence | None:
    """Add a genuinely unlisted anime when Kitsu confirms its title in the headline.

    The page body, generated text, and character names are not searched for a new
    series name. A Kitsu search result must contain a full catalog title verbatim
    (after typography/spacing normalization) in an article headline or an explicit
    structured ``about`` reference. Existing work aliases are resolved before an
    ID is allocated, and the new ID is the next number after the current maximum.
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
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for headline in eligible_titles:
            params = urlencode({"filter[text]": headline, "page[limit]": MAX_KITSU_PAGE_LIMIT})
            endpoint = f"{str(source.url).rstrip('/')}?{params}"
            try:
                pace_host(endpoint, source.min_interval_seconds)
                response = client.get(endpoint)
                response.raise_for_status()
                if len(response.content) > MAX_CATALOG_RESPONSE_BYTES:
                    continue
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                continue
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                continue
            for kitsu_id, matched_title, attributes in _title_match([headline], rows):
                exact_matches.setdefault(kitsu_id, (matched_title, attributes))

    if len(exact_matches) != 1:
        return None
    kitsu_id, (matched_title, attributes) = next(iter(exact_matches.items()))
    menu_work_id = _persist_kitsu_work(kitsu_id, attributes, matched_title)
    if menu_work_id is None:
        return None
    return WorkMatchEvidence(
        menu_work_id=menu_work_id,
        basis="kitsu_catalog_title",
        matched_value=matched_title,
        evidence_url=f"https://kitsu.io/anime/{kitsu_id}",
        confidence=0.97,
    )
