"""CSV format and writer lock used by the period and RAG work registries."""

from __future__ import annotations

import csv
import os
import re
import tempfile
import unicodedata
import zlib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit


WORK_FIELDS = (
    "menu_work_id", "title_ko", "title_ko_source", "title_ko_check", "title_romaji",
    "title_english", "title_native", "first_year", "last_year", "n_entries", "popularity", "anilist_ids",
)
EXTERNAL_FIELDS = (
    "menu_work_id", "source_id", "external_id", "source_url", "canonical_title", "format", "year", "registered_at",
)
DISCOVERY_FIELDS = (
    "kitsu_id", "canonical_title", "title_romaji", "title_english", "title_native", "format",
    "first_year", "source_title", "source_url", "status", "first_seen_at", "last_seen_at",
)


def title_key(title: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", title).casefold() if c.isalnum())


def temporary_id(title: str, kitsu_id: str | None) -> int:
    if kitsu_id:
        return 1_000_000 + int(kitsu_id)
    return 2_000_000 + zlib.crc32(title_key(title).encode("utf-8")) % 1_000_000_000


def valid_source_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme in {"http", "https"} and bool(parts.hostname)
            and parts.username is None and parts.password is None
            and parts.port != 0
        )
    except ValueError:
        return False


def canonical_namespace(name: str) -> str:
    value = name.strip().casefold()
    return "kitsu_anime_catalog" if value in {"kitsu", "kitsu_anime_catalog"} else value


def kitsu_page_id(url: str) -> str | None:
    if not valid_source_url(url):
        return None
    parts = urlsplit(url)
    if parts.hostname not in {"kitsu.io", "www.kitsu.io", "kitsu.app", "www.kitsu.app"}:
        return None
    if parts.port not in {None, 80 if parts.scheme == "http" else 443}:
        return None
    match = re.fullmatch(r"/(?:api/edge/)?anime/([1-9][0-9]*)/?", parts.path)
    return match.group(1) if match else None


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_rows(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    )
    try:
        with temp:
            writer = csv.DictWriter(temp, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp.name, path)
    finally:
        Path(temp.name).unlink(missing_ok=True)


@contextmanager
def registry_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".menu_work_catalog.lock"
    path.touch(exist_ok=True)
    with path.open("r+b") as stream:
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
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


def save_discovery(directory: Path, title: str, url: str, *, kitsu_id: str | None = None) -> None:
    """Called with the registry lock held."""
    path = directory / "discovered_works.csv"
    key = kitsu_id or f"title:{title_key(title)}"
    rows = read_rows(path)
    now = datetime.now(UTC).isoformat()
    for row in rows:
        if row["kitsu_id"] == key:
            row["last_seen_at"] = now
            for field, value in (("source_url", url), ("source_title", title)):
                row[field] = " | ".join(dict.fromkeys(filter(None, [*row[field].split(" | "), value])))
            break
    else:
        rows.append({
            "kitsu_id": key, "canonical_title": title, "title_romaji": title, "source_title": title,
            "source_url": url, "status": "pending_team_review", "first_seen_at": now, "last_seen_at": now,
        })
    write_rows(path, DISCOVERY_FIELDS, rows)


def save_work(directory: Path, identifier: int, title: str, source_url: str) -> None:
    """Append a new temporary row after checking both catalogs under the lock."""
    path = directory / "agent_works.csv"
    rows = read_rows(path)
    korean = bool(re.search(r"[\uac00-\ud7a3]", title))
    japanese = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", title))
    rows.append({
        "menu_work_id": str(identifier), "title_ko": title if korean else "",
        "title_ko_source": source_url if korean else "", "title_ko_check": "source-grounded; review pending" if korean else "",
        "title_english": "" if korean or japanese else title, "title_native": title if japanese else "",
        "n_entries": "0", "popularity": "0",
    })
    write_rows(path, WORK_FIELDS, rows)


def save_kitsu_link(directory: Path, identifier: int, title: str, kitsu_id: str) -> None:
    """Persist the external key in the configured external-ID table."""
    path = directory / "menu_work_external_entries.csv"
    rows = read_rows(path)
    for row in rows:
        if canonical_namespace(row["source_id"]) == "kitsu_anime_catalog" and row["external_id"] == kitsu_id:
            if row["menu_work_id"] == str(identifier):
                return
            row["menu_work_id"] = str(identifier)
            break
    else:
        rows.append({
            "menu_work_id": str(identifier), "source_id": "kitsu_anime_catalog", "external_id": kitsu_id,
            "source_url": f"https://kitsu.io/anime/{kitsu_id}", "canonical_title": title,
            "registered_at": datetime.now(UTC).isoformat(),
        })
    write_rows(path, EXTERNAL_FIELDS, rows)
