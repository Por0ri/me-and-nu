"""SQLite persistence adapter for the period-agent ERD.

The operational tables used by the existing graph remain available as a
compatibility layer. This module writes the domain dataset into normalized ERD
tables and keeps only the request-to-content crosswalk outside that ERD.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from ani_menu_agent.models import ContentPackage
from ani_menu_agent.sources import load_source_registry
from ani_menu_agent.work_catalog import (
    load_work_aliases,
    load_work_catalog,
    load_work_entries,
    load_work_external_entries,
)


ERD_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_site (
    source_site_id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER,
    media_name TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    collect_method TEXT NOT NULL,
    site_url TEXT NOT NULL UNIQUE,
    feed_url TEXT,
    source_grade TEXT,
    copyright_status TEXT NOT NULL DEFAULT 'unknown',
    license_basis TEXT,
    is_collecting INTEGER NOT NULL DEFAULT 0,
    last_collected_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_detail (
    event_detail_id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER,
    event_type TEXT,
    event_name TEXT NOT NULL,
    starts_on TEXT,
    ends_on TEXT,
    operating_hours TEXT,
    region TEXT,
    venue_name TEXT,
    address TEXT,
    admission_type TEXT,
    price_info TEXT,
    reservation_opens_at TEXT,
    reservation_url TEXT,
    perks TEXT,
    official_url TEXT,
    event_status TEXT,
    dedupe_key TEXT UNIQUE,
    last_verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    country_code TEXT
);

CREATE TABLE IF NOT EXISTS raw_document (
    raw_document_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_site_id INTEGER NOT NULL REFERENCES source_site(source_site_id),
    event_detail_id INTEGER REFERENCES event_detail(event_detail_id),
    external_id TEXT,
    url TEXT NOT NULL,
    title TEXT,
    raw_text TEXT,
    raw_payload TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    parse_error TEXT,
    purge_at TEXT,
    UNIQUE(source_site_id, external_id)
);

CREATE TABLE IF NOT EXISTS entity (
    entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER,
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    external_id TEXT NOT NULL DEFAULT '{}',
    attributes TEXT NOT NULL DEFAULT '{}',
    entity_profile TEXT,
    needs_refresh INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content (
    content_id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER,
    content_type TEXT,
    event_detail_id INTEGER REFERENCES event_detail(event_detail_id),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    summary TEXT,
    keyword TEXT NOT NULL DEFAULT '[]',
    thumbnail_url TEXT,
    is_sponsored INTEGER NOT NULL DEFAULT 0,
    is_changed_notice INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    ai_judgment_basis TEXT NOT NULL DEFAULT '{}',
    embedding BLOB,
    published_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    valid_until TEXT
);

CREATE TABLE IF NOT EXISTS content_request_map (
    request_id TEXT PRIMARY KEY,
    content_id INTEGER NOT NULL UNIQUE REFERENCES content(content_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_tag (
    content_id INTEGER NOT NULL REFERENCES content(content_id) ON DELETE CASCADE,
    subtopic_id INTEGER NOT NULL,
    tagged_by TEXT NOT NULL,
    tagged_at TEXT NOT NULL,
    PRIMARY KEY(content_id, subtopic_id)
);

CREATE TABLE IF NOT EXISTS content_card (
    content_id INTEGER NOT NULL REFERENCES content(content_id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL,
    card_role TEXT NOT NULL,
    title TEXT,
    body TEXT,
    card_image_url TEXT,
    source_image_url TEXT,
    source_page_url TEXT,
    image_rights_status TEXT,
    image_credit TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(content_id, page_no)
);

CREATE TABLE IF NOT EXISTS content_entity_link (
    content_id INTEGER NOT NULL REFERENCES content(content_id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entity(entity_id),
    link_method TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    PRIMARY KEY(content_id, entity_id),
    UNIQUE(content_id, sort_order),
    CHECK(sort_order > 0)
);

CREATE TABLE IF NOT EXISTS entity_relation (
    relation_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE,
    related_entity_id INTEGER NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE,
    score REAL,
    model_version TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(relation_type, entity_id, related_entity_id),
    CHECK(entity_id <> related_entity_id)
);

CREATE TABLE IF NOT EXISTS content_source (
    content_id INTEGER NOT NULL REFERENCES content(content_id) ON DELETE CASCADE,
    raw_document_id INTEGER NOT NULL REFERENCES raw_document(raw_document_id),
    source_site_id INTEGER NOT NULL REFERENCES source_site(source_site_id),
    source_url TEXT NOT NULL,
    publisher TEXT,
    published_at TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(content_id, raw_document_id)
);

CREATE TABLE IF NOT EXISTS judgment_log (
    judgment_log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL REFERENCES content(content_id) ON DELETE CASCADE,
    agent_code TEXT NOT NULL,
    stage TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    prompt_version TEXT,
    model_name TEXT,
    input_summary TEXT,
    judgment_result TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL,
    is_adopted INTEGER NOT NULL DEFAULT 0,
    token_cost TEXT,
    judged_at TEXT NOT NULL,
    UNIQUE(content_id, stage, attempt_no)
);

CREATE INDEX IF NOT EXISTS idx_raw_document_fetched ON raw_document(fetched_at);
CREATE INDEX IF NOT EXISTS idx_content_status_valid ON content(status, valid_until);
CREATE INDEX IF NOT EXISTS idx_content_source_site ON content_source(source_site_id);
CREATE INDEX IF NOT EXISTS idx_judgment_content_stage ON judgment_log(content_id, stage, judged_at);
"""


def initialize_erd_schema(connection) -> None:
    """Create the SQLite equivalents of the ERD tables without altering legacy data."""
    connection.executescript(ERD_SCHEMA)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _site_url(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}/"


def _source_registry_by_id() -> dict[str, Any]:
    return {source.id: source for source in load_source_registry().sources}


def _ensure_capture_site(connection, capture: dict[str, Any], registry: dict[str, Any], now: str) -> int:
    source_id = str(capture.get("source_id") or "")
    configured = registry.get(source_id)
    url = str(capture.get("url") or "")
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("captured page has no absolute URL")
    site_url = f"{parsed.scheme}://{parsed.netloc}/"
    feed_url = getattr(configured, "feed_url", None)
    row = connection.execute(
        """INSERT INTO source_site(
               media_name,source_kind,collect_method,site_url,feed_url,source_grade,
               copyright_status,license_basis,is_collecting,last_collected_at,created_at
           ) VALUES(?,?,?,?,?,?,'unknown',NULL,?,?,?)
           ON CONFLICT(site_url) DO UPDATE SET
               media_name=excluded.media_name,source_kind=excluded.source_kind,
               collect_method=excluded.collect_method,
               feed_url=COALESCE(excluded.feed_url,source_site.feed_url),
               source_grade=COALESCE(excluded.source_grade,source_site.source_grade),
               is_collecting=MAX(source_site.is_collecting,excluded.is_collecting),
               last_collected_at=CASE
                   WHEN excluded.last_collected_at IS NOT NULL AND
                        (source_site.last_collected_at IS NULL OR
                         excluded.last_collected_at>source_site.last_collected_at)
                   THEN excluded.last_collected_at ELSE source_site.last_collected_at END
           RETURNING source_site_id""",
        (
            getattr(configured, "name", None) or parsed.netloc,
            getattr(configured, "category", None) or "unknown",
            getattr(configured, "collection", None) or "manual",
            site_url,
            str(feed_url) if feed_url else None,
            getattr(configured, "role", None),
            int(bool(configured and configured.status == "active")),
            capture.get("fetched_at"),
            now,
        ),
    ).fetchone()
    return int(row[0])


def _persist_page_captures(connection, registry: dict[str, Any], now: str) -> int:
    rows = connection.execute(
        "SELECT record_id,expires_at,payload_json FROM agent_records "
        "WHERE record_kind='official_page_capture' ORDER BY created_at,record_id"
    ).fetchall()
    saved = 0
    for row in rows:
        try:
            capture = json.loads(row["payload_json"] or "{}")
            if not isinstance(capture, dict) or not capture.get("url"):
                continue
            site_id = _ensure_capture_site(connection, capture, registry, now)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        raw_text = capture.get("text") or capture.get("main_text")
        content_hash = hashlib.sha256(
            (str(raw_text) if raw_text else _json(capture)).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """INSERT INTO raw_document(
                   source_site_id,external_id,url,title,raw_text,raw_payload,content_hash,
                   published_at,fetched_at,parse_status,purge_at
               ) VALUES(?,?,?,?,?,?,?,?,?,'captured',?)
               ON CONFLICT(source_site_id,external_id) DO UPDATE SET
                   url=excluded.url,title=excluded.title,
                   raw_text=COALESCE(excluded.raw_text,raw_document.raw_text),
                   raw_payload=excluded.raw_payload,content_hash=excluded.content_hash,
                   published_at=COALESCE(excluded.published_at,raw_document.published_at),
                   fetched_at=excluded.fetched_at,parse_status='captured',
                   purge_at=COALESCE(excluded.purge_at,raw_document.purge_at)""",
            (
                site_id,
                row["record_id"],
                str(capture["url"]),
                capture.get("title"),
                raw_text if isinstance(raw_text, str) else None,
                _json(capture),
                content_hash,
                capture.get("published_at"),
                capture.get("fetched_at") or now,
                row["expires_at"],
            ),
        )
        saved += 1
    return saved


def _ensure_source_site(connection, source, registry: dict[str, Any], now: str) -> int:
    url = str(source.url)
    configured = registry.get(source.id)
    site_url = _site_url(url)
    media_name = getattr(configured, "name", None) or source.publisher
    source_kind = getattr(configured, "category", None) or source.role
    collect_method = getattr(configured, "collection", None) or "manual"
    feed_url = getattr(configured, "feed_url", None)
    cursor = connection.execute(
        """INSERT INTO source_site (
               media_name, source_kind, collect_method, site_url, feed_url, source_grade,
               copyright_status, license_basis, is_collecting, last_collected_at, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, 'unknown', NULL, ?, ?, ?)
           ON CONFLICT(site_url) DO UPDATE SET
               media_name=excluded.media_name,
               source_kind=excluded.source_kind,
               collect_method=excluded.collect_method,
               feed_url=COALESCE(excluded.feed_url, source_site.feed_url),
               source_grade=excluded.source_grade,
               is_collecting=excluded.is_collecting,
               last_collected_at=CASE
                   WHEN excluded.last_collected_at IS NOT NULL AND
                        (source_site.last_collected_at IS NULL OR
                         excluded.last_collected_at>source_site.last_collected_at)
                   THEN excluded.last_collected_at ELSE source_site.last_collected_at END
           RETURNING source_site_id""",
        (
            media_name,
            source_kind,
            collect_method,
            site_url,
            str(feed_url) if feed_url else None,
            source.role,
            int(bool(configured and configured.status == "active")),
            source.checked_at,
            now,
        ),
    ).fetchone()
    return int(cursor[0])


def _event_detail_id(connection, package: ContentPackage, now: str) -> int | None:
    if package.content_type != "event":
        return None
    first_source = package.sources[0] if package.sources else None
    fact_details = [fact.details for fact in package.facts if fact.details]

    def fact_value(*keys: str) -> str | None:
        for details in fact_details:
            for key in keys:
                value = details.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
        return None

    event_name = fact_value("event_name", "event_title", "product_name") or package.topic
    event_type = next(
        (fact.fact_type for fact in package.facts if "event" in fact.fact_type.lower()),
        package.content_type,
    )
    dedupe_key = hashlib.sha256(
        "|".join(
            [
                package.topic,
                package.event_start or "",
                package.event_end or "",
                package.event_location or "",
                str(first_source.url) if first_source else "",
            ]
        ).encode("utf-8")
    ).hexdigest()
    row = connection.execute(
        """INSERT INTO event_detail (
               event_type,event_name,starts_on,ends_on,operating_hours,region,venue_name,address,
               admission_type,price_info,reservation_opens_at,reservation_url,perks,official_url,
               event_status,dedupe_key,last_verified_at,created_at,updated_at,country_code
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(dedupe_key) DO UPDATE SET
               event_type=excluded.event_type,event_name=excluded.event_name,
               starts_on=excluded.starts_on,ends_on=excluded.ends_on,
               operating_hours=excluded.operating_hours,region=excluded.region,
               venue_name=excluded.venue_name,address=excluded.address,
               admission_type=excluded.admission_type,price_info=excluded.price_info,
               reservation_opens_at=excluded.reservation_opens_at,
               reservation_url=excluded.reservation_url,perks=excluded.perks,
               official_url=excluded.official_url,
               event_status=excluded.event_status,
               last_verified_at=excluded.last_verified_at, updated_at=excluded.updated_at,
               country_code=excluded.country_code
           RETURNING event_detail_id""",
        (
            event_type,
            event_name,
            package.event_start,
            package.event_end,
            fact_value("operating_hours", "business_hours"),
            package.event_region,
            fact_value("venue_name", "venue") or package.event_location,
            fact_value("address", "venue_address"),
            fact_value("admission_type", "admission"),
            fact_value("price_info", "price", "admission_fee"),
            fact_value("reservation_opens_at", "reservation_start"),
            fact_value("reservation_url", "ticket_url"),
            fact_value("perks", "benefits"),
            str(first_source.url) if first_source and first_source.is_official else None,
            fact_value("event_status", "status"),
            dedupe_key,
            now,
            now,
            now,
            package.event_country,
        ),
    ).fetchone()
    return int(row[0])


def _raw_source_payload(connection, request_id: str, source_id: str) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT payload_json FROM agent_records WHERE request_id=? "
        "AND record_kind IN ('source','trend_source') AND agent_type IN ('information','humor') "
        "ORDER BY created_at",
        (request_id,),
    ).fetchall()
    for row in rows:
        payload = json.loads(row[0] or "{}")
        if payload.get("id") == source_id:
            return payload
    return {}


def _raw_page_capture(connection, url: str) -> dict[str, Any]:
    page_record_id = f"page:{hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]}"
    row = connection.execute(
        "SELECT payload_json FROM agent_records WHERE record_kind='official_page_capture' "
        "AND request_id=? ORDER BY updated_at DESC LIMIT 1",
        (page_record_id,),
    ).fetchone()
    if row is None:
        return {}
    try:
        payload = json.loads(row[0] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _ensure_raw_document(
    connection, package: ContentPackage, source, site_id: int, event_id: int | None, now: str
) -> int:
    raw_payload = _raw_source_payload(connection, package.request_id, source.id)
    capture = _raw_page_capture(connection, str(source.url))
    if not capture and source.linked_from_url:
        parent_capture = _raw_page_capture(connection, str(source.linked_from_url))
        linked_capture = next(
            (
                item
                for item in parent_capture.get("linked_page_evidence", [])
                if str(item.get("url") or "") == str(source.url)
            ),
            None,
        )
        if linked_capture:
            capture = linked_capture
    facts = [fact.model_dump(mode="json") for fact in package.facts if source.id in fact.source_ids]
    extracted_text = next(
        (
            capture.get(key)
            for key in ("raw_text", "body_text", "text", "content", "body")
            if isinstance(capture.get(key), str) and capture.get(key).strip()
        ),
        next(
            (
                raw_payload.get(key)
                for key in ("raw_text", "body_text", "text", "content", "body")
                if isinstance(raw_payload.get(key), str) and raw_payload.get(key).strip()
            ),
            None,
        ),
    )
    payload = {"source": raw_payload, "capture": capture, "verified_facts": facts}
    hash_material = extracted_text or _json(capture or raw_payload)
    content_hash = hashlib.sha256(hash_material.encode("utf-8")).hexdigest()
    external_id = (
        f"page:{hashlib.sha1(str(source.url).encode('utf-8')).hexdigest()[:16]}"
        if capture and str(capture.get("url") or "") == str(source.url)
        else f"{package.request_id}:{source.id}"
    )
    row = connection.execute(
        """INSERT INTO raw_document (
               source_site_id, event_detail_id, external_id, url, title, raw_text, raw_payload,
               content_hash, published_at, fetched_at, parse_status, parse_error, purge_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
           ON CONFLICT(source_site_id, external_id) DO UPDATE SET
               event_detail_id=excluded.event_detail_id, url=excluded.url,
               title=excluded.title, raw_text=excluded.raw_text, raw_payload=excluded.raw_payload,
               content_hash=excluded.content_hash, published_at=excluded.published_at,
               fetched_at=excluded.fetched_at, parse_status=excluded.parse_status,
               purge_at=excluded.purge_at
           RETURNING raw_document_id""",
        (
            site_id,
            event_id,
            external_id,
            str(source.url),
            source.title,
            extracted_text,
            _json(payload),
            content_hash,
            source.published_at,
            source.checked_at or now,
            "parsed" if facts else "captured",
            package.purge_at,
        ),
    ).fetchone()
    return int(row[0])


def _work_rows() -> tuple[
    dict[int, dict[str, Any]], dict[int, list[str]], dict[int, list[dict]], dict[int, list[dict]]
]:
    works = {int(row["menu_work_id"]): row for row in load_work_catalog()}
    aliases: dict[int, list[str]] = {}
    for row in load_work_aliases():
        if row.get("status") == "verified":
            try:
                aliases.setdefault(int(row["menu_work_id"]), []).append(row["alias"])
            except (TypeError, ValueError):
                continue
    entries: dict[int, list[dict]] = {}
    for row in load_work_entries():
        entries.setdefault(int(row["menu_work_id"]), []).append(row)
    external_entries: dict[int, list[dict]] = {}
    for row in load_work_external_entries():
        external_entries.setdefault(int(row["menu_work_id"]), []).append(row)
    return works, aliases, entries, external_entries


def _ensure_work_entity(connection, menu_work_id: int, now: str, works, aliases, entries, external_entries) -> int | None:
    work = works.get(menu_work_id)
    if work is None:
        return None
    existing_entity_id = None
    for row in connection.execute("SELECT entity_id, external_id FROM entity WHERE entity_type='work'"):
        try:
            if int(json.loads(row["external_id"]).get("menu_work_id")) == menu_work_id:
                existing_entity_id = int(row["entity_id"])
                break
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    names = [work.get(key, "").strip() for key in ("title_romaji", "title_english", "title_native")]
    alt = list(dict.fromkeys([name for name in names if name and name != work.get("title_ko")]))
    alt.extend(value for value in aliases.get(menu_work_id, []) if value not in alt)
    anilist_ids = [value for value in str(work.get("anilist_ids") or "").split(";") if value]
    entry_attributes = [
        {
            key: row.get(key)
            for key in ("anilist_id", "mal_id", "format", "year", "title_ko", "title_romaji", "title_english", "title_native")
        }
        for row in entries.get(menu_work_id, [])
    ]
    external_ids = [
        {key: row.get(key) for key in ("source_id", "external_id", "source_url")}
        for row in external_entries.get(menu_work_id, [])
    ]
    entity_values = (
        work.get("title_ko") or work.get("title_english") or f"menu_work:{menu_work_id}",
        _json(alt),
        _json({"menu_work_id": menu_work_id, "anilist_ids": anilist_ids, "entries": external_ids}),
        _json(
            {
                "title_romaji": work.get("title_romaji"),
                "title_english": work.get("title_english"),
                "title_native": work.get("title_native"),
                "first_year": work.get("first_year"),
                "last_year": work.get("last_year"),
                "entries": entry_attributes,
            }
        ),
        now,
    )
    if existing_entity_id is not None:
        connection.execute(
            "UPDATE entity SET name=?,aliases=?,external_id=?,attributes=?,updated_at=? WHERE entity_id=?",
            (*entity_values, existing_entity_id),
        )
        return existing_entity_id
    cursor = connection.execute(
        """INSERT INTO entity (
               entity_type, name, aliases, external_id, attributes, entity_profile,
               needs_refresh, created_at, updated_at
           ) VALUES ('work', ?, ?, ?, ?, NULL, 0, ?, ?) RETURNING entity_id""",
        (*entity_values, now),
    )
    return int(cursor.fetchone()[0])


def persist_crawl_dataset(connection, capture: dict[str, Any], package: ContentPackage | None = None) -> dict[str, int]:
    """Persist crawler output into normalized ERD source/raw/entity tables.

    This runs before the generation queue is populated. New catalog IDs therefore
    cannot be treated as complete until their entity row and captured source row
    are both present in the same SQLite transaction.
    """
    now = _now()
    registry = _source_registry_by_id()
    site_id = _ensure_capture_site(connection, capture, registry, now)
    raw_text = capture.get("text") or capture.get("main_text")
    digest = hashlib.sha256(
        (str(raw_text) if raw_text else _json(capture)).encode("utf-8")
    ).hexdigest()
    event_id = _event_detail_id(connection, package, now) if package else None
    record_id = f"page:{hashlib.sha1(str(capture['url']).encode('utf-8')).hexdigest()[:16]}"
    connection.execute(
        """INSERT INTO raw_document(
               source_site_id,event_detail_id,external_id,url,title,raw_text,raw_payload,
               content_hash,published_at,fetched_at,parse_status,purge_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,'captured',?)
           ON CONFLICT(source_site_id,external_id) DO UPDATE SET
               event_detail_id=COALESCE(excluded.event_detail_id,raw_document.event_detail_id),
               url=excluded.url,title=excluded.title,
               raw_text=COALESCE(excluded.raw_text,raw_document.raw_text),
               raw_payload=excluded.raw_payload,content_hash=excluded.content_hash,
               published_at=COALESCE(excluded.published_at,raw_document.published_at),
               fetched_at=excluded.fetched_at,parse_status='captured',
               purge_at=COALESCE(excluded.purge_at,raw_document.purge_at)""",
        (
            site_id,
            event_id,
            record_id,
            str(capture["url"]),
            capture.get("title"),
            raw_text if isinstance(raw_text, str) else None,
            _json(capture),
            digest,
            capture.get("published_at"),
            capture.get("fetched_at") or now,
            capture.get("expires_at"),
        ),
    )
    work_ids = list(dict.fromkeys(int(value) for value in capture.get("menu_work_ids", []) if str(value).isdigit()))
    works, aliases, entries, external_entries = _work_rows()
    entity_ids = []
    for work_id in work_ids:
        entity_id = _ensure_work_entity(
            connection, work_id, now, works, aliases, entries, external_entries
        )
        if entity_id is None:
            raise ValueError(
                f"menu_work_id={work_id} was assigned but is missing from the canonical work catalog"
            )
        entity_ids.append(entity_id)
    return {"source_site_id": site_id, "raw_document_count": 1, "entity_count": len(entity_ids)}


def _title_from_package(package: ContentPackage) -> str:
    cover = next((card for card in package.cards if card.role == "cover"), None)
    title = " ".join(cover.title_lines).strip() if cover else ""
    return title or package.topic


def _sync_legacy_package_status(connection, request_id: str, status: str) -> None:
    for table in ("content_drafts", "feed_queue", "rejected_archive"):
        row = connection.execute(
            f"SELECT package_json FROM {table} WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            continue
        try:
            package = json.loads(row[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(package, dict) or package.get("status") == status:
            continue
        package["status"] = status
        connection.execute(
            f"UPDATE {table} SET package_json=? WHERE request_id=?",
            (_json(package), request_id),
        )


def _card_image_url(card, image) -> str | None:
    if card.rendered_image_key:
        return f"imageblob://{card.rendered_image_key}"
    return str(image.url) if image and image.url else None


def persist_package(
    connection,
    package: ContentPackage,
    status: str,
    quality_report: dict[str, Any] | None,
    *,
    usage_records: list[dict[str, Any]] | None = None,
    record_judgment: bool = True,
) -> int:
    """Upsert a generated package into ERD tables and return its content_id."""
    now = _now()
    event_id = _event_detail_id(connection, package, now)
    source_registry = _source_registry_by_id()
    source_rows = []
    for source in package.sources:
        site_id = _ensure_source_site(connection, source, source_registry, now)
        raw_id = _ensure_raw_document(connection, package, source, site_id, event_id, now)
        source_rows.append((source, site_id, raw_id))

    existing = connection.execute(
        "SELECT content_id FROM content_request_map WHERE request_id=?",
        (package.request_id,),
    ).fetchone()
    if existing:
        content_id = int(existing[0])
    else:
        content_id = None

    cover = next((card for card in package.cards if card.role == "cover"), None)
    image_by_id = {image.id: image for image in package.images}
    cover_image = image_by_id.get(cover.image_id) if cover and cover.image_id else None
    report = quality_report or {}
    judgment_basis = {
        "quality_report": report,
        "detail_fact_ids": package.detail_fact_ids,
        "detail_source_ids": package.detail_source_ids,
        "work_match_evidence": [item.model_dump(mode="json") for item in package.work_match_evidence],
    }
    values = (
        package.content_type,
        event_id,
        _title_from_package(package),
        package.detail_body,
        None,
        _json([]),
        _card_image_url(cover, cover_image) if cover else None,
        status,
        _json(judgment_basis),
        now,
        now,
        package.feed_valid_until,
    )
    if content_id is None:
        row = connection.execute(
            """INSERT INTO content (
                   content_type, event_detail_id, title, body, summary, keyword, thumbnail_url,
                   status, ai_judgment_basis, created_at, updated_at, valid_until
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING content_id""",
            values,
        ).fetchone()
        content_id = int(row[0])
        connection.execute(
            "INSERT INTO content_request_map(request_id,content_id,created_at,updated_at) VALUES (?,?,?,?)",
            (package.request_id, content_id, now, now),
        )
    else:
        connection.execute(
            """UPDATE content SET content_type=?, event_detail_id=?, title=?, body=?, summary=?,
                   keyword=?, thumbnail_url=?, status=?, ai_judgment_basis=?, updated_at=?, valid_until=?
               WHERE content_id=?""",
            (*values[:9], values[10], values[11], content_id),
        )
        connection.execute(
            "UPDATE content_request_map SET updated_at=? WHERE request_id=?",
            (now, package.request_id),
        )

    works, aliases, entries, external_entries = _work_rows()
    connection.execute("DELETE FROM content_entity_link WHERE content_id=?", (content_id,))
    evidence_by_work = {item.menu_work_id: item for item in package.work_match_evidence}
    for order, work_id in enumerate(dict.fromkeys(package.menu_work_ids), start=1):
        entity_id = _ensure_work_entity(
            connection, int(work_id), now, works, aliases, entries, external_entries
        )
        if entity_id is None:
            continue
        evidence = evidence_by_work.get(int(work_id))
        method = "source" if evidence and evidence.basis in {"source_registry", "registered_official_link"} else "agent_verified"
        connection.execute(
            """INSERT INTO content_entity_link(content_id,entity_id,link_method,created_at,sort_order)
               VALUES (?,?,?,?,?)""",
            (content_id, entity_id, method, now, order),
        )

    connection.execute("DELETE FROM content_source WHERE content_id=?", (content_id,))
    primary_ids = set(package.detail_source_ids[:1])
    for source, site_id, raw_id in source_rows:
        connection.execute(
            """INSERT INTO content_source(
                   content_id,raw_document_id,source_site_id,source_url,publisher,published_at,is_primary
               ) VALUES (?,?,?,?,?,?,?)""",
            (
                content_id,
                raw_id,
                site_id,
                str(source.url),
                source.publisher,
                source.published_at,
                int(source.id in primary_ids or (not primary_ids and source is package.sources[0])),
            ),
        )

    connection.execute("DELETE FROM content_card WHERE content_id=?", (content_id,))
    for card in package.cards:
        image = image_by_id.get(card.image_id) if card.image_id else None
        connection.execute(
            """INSERT INTO content_card(
                   content_id,page_no,card_role,title,body,card_image_url,source_image_url,
                   source_page_url,image_rights_status,image_credit,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                content_id,
                card.page,
                card.role,
                " ".join(card.title_lines),
                " ".join(card.body_sentences),
                _card_image_url(card, image),
                str(image.url) if image and image.url else None,
                str(image.source_url) if image else None,
                image.rights_status.value if image else None,
                getattr(image, "attribution_text", None) if image else None,
                now,
            ),
        )

    connection.execute("DELETE FROM content_tag WHERE content_id=?", (content_id,))
    if record_judgment:
        stage = "review" if quality_report else "generation"
        attempt = connection.execute(
            "SELECT COALESCE(MAX(attempt_no),0)+1 FROM judgment_log WHERE content_id=? AND stage=?",
            (content_id, stage),
        ).fetchone()[0]
        result = quality_report or {"status": status}
        stage_usage = [
            row
            for row in usage_records or []
            if (row.get("stage") == "review") == (stage == "review")
        ]
        if not stage_usage and usage_records:
            stage_usage = list(usage_records)
        model_names = list(dict.fromkeys(str(row.get("model")) for row in stage_usage if row.get("model")))
        token_cost = None
        if stage_usage:
            token_cost = _json(
                {
                    "records": stage_usage,
                    "input_tokens": sum(int(row.get("input_tokens", 0) or 0) for row in stage_usage),
                    "output_tokens": sum(int(row.get("output_tokens", 0) or 0) for row in stage_usage),
                    "total_tokens": sum(int(row.get("total_tokens", 0) or 0) for row in stage_usage),
                    "estimated_cost_usd": sum(float(row.get("estimated_cost_usd", 0) or 0) for row in stage_usage),
                }
            )
        connection.execute(
            """INSERT INTO judgment_log(
                   content_id,agent_code,stage,attempt_no,prompt_version,model_name,input_summary,
                   judgment_result,verdict,confidence,is_adopted,token_cost,judged_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                content_id,
                "review_agent" if stage == "review" else "content_generator",
                stage,
                attempt,
                None,
                ",".join(model_names)[:80] or None,
                _json({"request_id": package.request_id, "fact_count": len(package.facts), "source_count": len(package.sources)}),
                _json(result),
                str(result.get("decision") or status),
                None,
                int(status == "ready_for_feed"),
                token_cost,
                now,
            ),
        )
    return content_id


def set_content_status(connection, request_id: str, status: str, *, adopted: bool | None = None) -> None:
    """Keep the normalized content status aligned with feed/review transitions."""
    row = connection.execute(
        "SELECT content_id FROM content_request_map WHERE request_id=?", (request_id,)
    ).fetchone()
    if row is None:
        return
    content_id = int(row[0])
    connection.execute(
        "UPDATE content SET status=?,updated_at=? WHERE content_id=?",
        (status, _now(), content_id),
    )
    _sync_legacy_package_status(connection, request_id, status)
    if adopted is not None:
        connection.execute(
            "UPDATE judgment_log SET is_adopted=? WHERE judgment_log_id=("
            "SELECT judgment_log_id FROM judgment_log WHERE content_id=? ORDER BY judged_at DESC, judgment_log_id DESC LIMIT 1)",
            (int(adopted), content_id),
        )


def migrate_legacy_records(connection) -> dict[str, Any]:
    """Add normalized ERD rows for legacy drafts without deleting legacy rows.

    The operation is repeatable: content/source/card/link rows are upserted, and
    a current judgment row is only added when that content has no judgment log.
    Callers should take a database backup before running this on an existing DB.
    """
    initialize_erd_schema(connection)
    registry = _source_registry_by_id()
    captured_pages = _persist_page_captures(connection, registry, _now())
    archive_rows = {
        row["request_id"]: row
        for row in connection.execute(
            "SELECT request_id, reasons_json, package_json FROM rejected_archive"
        ).fetchall()
    }
    rows = connection.execute(
        "SELECT request_id,status,package_json,quality_report_json FROM content_drafts ORDER BY created_at,request_id"
    ).fetchall()
    seen: set[str] = set()
    migrated = 0
    skipped: list[dict[str, str]] = []
    for row in rows:
        request_id = row["request_id"]
        seen.add(request_id)
        try:
            package = ContentPackage.model_validate(json.loads(row["package_json"]))
            status = row["status"]
            package.status = status
            report = json.loads(row["quality_report_json"]) if row["quality_report_json"] else None
            archive = archive_rows.get(request_id)
            if archive:
                reasons = json.loads(archive["reasons_json"] or "[]")
                report = {**(report or {}), "archive_reasons": reasons}
        except (ValueError, TypeError, KeyError) as exc:
            skipped.append({"request_id": request_id, "error": str(exc)[:400]})
            continue
        content_id = persist_package(
            connection, package, status, report, record_judgment=False
        )
        _sync_legacy_package_status(connection, request_id, status)
        judgment_exists = connection.execute(
            "SELECT 1 FROM judgment_log WHERE content_id=? LIMIT 1", (content_id,)
        ).fetchone()
        if not judgment_exists and report:
            persist_package(connection, package, status, report, record_judgment=True)
        set_content_status(
            connection,
            request_id,
            status,
            adopted=(status == "ready_for_feed"),
        )
        migrated += 1

    for request_id, archive in archive_rows.items():
        if request_id in seen:
            continue
        try:
            package = ContentPackage.model_validate(json.loads(archive["package_json"]))
            package.status = "rejected_archived"
            reasons = json.loads(archive["reasons_json"] or "[]")
        except (ValueError, TypeError, KeyError) as exc:
            skipped.append({"request_id": request_id, "error": str(exc)[:400]})
            continue
        report = {"decision": "rejected_archived", "archive_reasons": reasons}
        content_id = persist_package(
            connection, package, "rejected_archived", report, record_judgment=False
        )
        if not connection.execute(
            "SELECT 1 FROM judgment_log WHERE content_id=? LIMIT 1", (content_id,)
        ).fetchone():
            persist_package(
                connection, package, "rejected_archived", report, record_judgment=True
            )
        _sync_legacy_package_status(connection, request_id, "rejected_archived")
        migrated += 1

    # Older backfills used request-scoped IDs for captured pages. Once content
    # sources point at the canonical capture IDs, remove only those now-orphaned
    # duplicates from the new ERD table.
    connection.execute(
        "DELETE FROM raw_document WHERE external_id LIKE 'crawl-%' "
        "AND raw_document_id NOT IN (SELECT raw_document_id FROM content_source)"
    )
    return {"migrated": migrated, "captured_pages": captured_pages, "skipped": skipped}
