"""PostgreSQL 적재, 이미지 BYTEA 보관, 행사 정보 만료, 탈락 보관을 담당합니다."""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import re
import sqlite3
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

from PIL import Image

from ani_menu_agent.agents.content_generation.renderer import OUTPUT_IMAGE_SIZE
from ani_menu_agent.config import get_settings
from ani_menu_agent.duplicate_detection import duplicate_reason
from ani_menu_agent.image_selection import image_visual_keys
from ani_menu_agent.markdown_export import render_detail_markdown
from ani_menu_agent.models import (
    CONTENT_TYPE_LABELS,
    ContentPackage,
    ImageAsset,
    RightsStatus,
    SourceEvidence,
)
from ani_menu_agent.policy import POLICY
from ani_menu_agent.source_policy import validate_generation_sources
from ani_menu_agent.sources import load_source_registry
from ani_menu_agent.work_catalog import attach_menu_work_ids, resolve_menu_work_ids


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _card_overlay_json(card) -> str:
    """Make the page-to-image/text relationship explicit for frontend consumers."""
    return json.dumps(
        {
            "page": card.page,
            "image_key": card.rendered_image_key,
            "image_id": card.image_id,
            "visual_mode": card.visual_mode,
            "layout_template": card.layout.template,
            "image_region": card.layout.image_box.model_dump(mode="json"),
            "copy_region": card.layout.copy_box.model_dump(mode="json"),
            "text_region": card.layout.text_overlay_box.model_dump(mode="json"),
            "eyebrow": card.eyebrow,
            "title_lines": card.title_lines,
            "body_sentences": card.body_sentences,
        },
        ensure_ascii=False,
    )


_CONTEXT_SPECIFIC_ART_RE = re.compile(
    r"\b(?:goods?|merch(?:andise)?|product|figure|plush|popup|pop[\s-]?up|"
    r"event|campaign|collab(?:oration)?|exhibition|ticket)\b|"
    r"グッズ|商品|物販|ポップアップ|コラボ|展覧会|フィギュア|ぬいぐるみ|アクスタ",
    re.IGNORECASE,
)
_FAN_MADE_ART_RE = re.compile(
    r"\b(?:fan[\s-]?(?:art|made)|meme)\b|ファンアート|二次創作|ミーム",
    re.IGNORECASE,
)


def _classify_legacy_reference_art(
    image: ImageAsset,
    source: SourceEvidence,
    *,
    source_role: str,
    origin_content_type: str | None,
) -> ImageAsset | None:
    """Conservatively label old official same-work images missing asset metadata.

    Older rows predate ``asset_kind`` and deserialize as ``unknown``. Keep those
    rows useful for general same-work editorial cards, but never let a known
    event/goods source become a generic anime illustration.
    """
    if image.asset_kind != "unknown":
        return image
    if source_role != "primary" or origin_content_type in {"event", "goods"}:
        return None
    context = " ".join(
        (
            source.title,
            str(source.url),
            image.source_description,
            str(image.url or ""),
        )
    )
    context = unquote(context)
    if _CONTEXT_SPECIFIC_ART_RE.search(context) or _FAN_MADE_ART_RE.search(context):
        return None
    image_path = unquote(urlparse(str(image.url or "")).path).casefold()
    source_path = unquote(urlparse(str(source.url)).path).casefold()
    kind = (
        "character_art"
        if re.search(r"/characters?/", f"{source_path} {image_path}")
        else "official_key_visual"
    )
    return image.model_copy(
        update={"asset_kind": kind, "asset_metadata_confidence": 0.4}
    )


KST = ZoneInfo("Asia/Seoul")
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
_PSYCOPG_DLL_HANDLES: list[Any] = []


def _prepare_windows_psycopg_dlls() -> None:
    """Load psycopg-binary's bundled OpenSSL dependencies before libpq on Windows."""
    if sys.platform != "win32" or _PSYCOPG_DLL_HANDLES:
        return

    import ctypes
    from importlib.util import find_spec

    spec = find_spec("psycopg_binary")
    if spec is None or spec.origin is None:
        return
    library_dir = Path(spec.origin).parent.parent / "psycopg_binary.libs"
    if not library_dir.is_dir():
        return

    _PSYCOPG_DLL_HANDLES.append(os.add_dll_directory(str(library_dir)))
    for pattern in ("libcrypto-*.dll", "libssl-*.dll", "libpq-*.dll"):
        for library in library_dir.glob(pattern):
            _PSYCOPG_DLL_HANDLES.append(ctypes.WinDLL(str(library)))


class _PostgresConnection:
    """Small DB-API bridge so repository SQL can share qmark placeholders in tests."""

    def __init__(self, connection) -> None:
        self.connection = connection

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self.connection.__exit__(exc_type, exc, traceback)

    def execute(self, query: str, parameters=()):
        return self.connection.execute(query.replace("?", "%s"), parameters)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self.connection.execute(statement)


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _kst_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=KST)


def _expiration(event_end: str | None) -> str | None:
    """Event records remain stored 30 days after their last feed-visible day."""
    end_date = _as_date(event_end)
    if not end_date:
        return None
    return (
        _kst_start(end_date + timedelta(days=1 + POLICY.default_event_retention_days))
        .astimezone(UTC)
        .isoformat()
    )


def prepare_package_lifecycle(
    package: ContentPackage, now: datetime | None = None
) -> ContentPackage:
    """Set stable feed and purge deadlines once, before crawler records are persisted."""
    moment = now or _utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    local_today = moment.astimezone(KST).date()

    if package.feed_valid_until:
        raw_valid_until = package.feed_valid_until
        if len(raw_valid_until) == 10:
            explicit = _as_date(raw_valid_until)
            assert explicit is not None
            valid_until = _kst_start(explicit + timedelta(days=1))
        else:
            valid_until = datetime.fromisoformat(raw_valid_until)
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=KST)
    else:
        end_date = _as_date(package.event_end)
        if not end_date:
            end_date = next(
                (
                    parsed
                    for fact in package.facts
                    for key in ("event_end", "deadline", "valid_until")
                    if (parsed := _as_date(fact.details.get(key)))
                ),
                None,
            )
        release_date = next(
            (
                parsed
                for fact in package.facts
                if (parsed := _as_date(fact.details.get("release_date")))
            ),
            None,
        )
        if end_date:
            last_visible_day = end_date
        elif release_date:
            last_visible_day = release_date + timedelta(days=30)
        else:
            last_visible_day = local_today + timedelta(
                days=POLICY.default_content_validity_days - 1
            )
        valid_until = _kst_start(last_visible_day + timedelta(days=1))

    if package.purge_at:
        purge_at = datetime.fromisoformat(package.purge_at)
        if purge_at.tzinfo is None:
            purge_at = purge_at.replace(tzinfo=UTC)
    elif package.retention_expires_at and not package.event_end:
        purge_at = datetime.fromisoformat(package.retention_expires_at)
        if purge_at.tzinfo is None:
            purge_at = purge_at.replace(tzinfo=UTC)
    else:
        purge_at = valid_until + timedelta(days=POLICY.default_event_retention_days)
    if purge_at <= valid_until:
        valid_until = purge_at
    package.feed_valid_until = valid_until.astimezone(UTC).isoformat()
    package.purge_at = purge_at.astimezone(UTC).isoformat()
    return package


def _package_expiration(package: ContentPackage) -> str | None:
    prepare_package_lifecycle(package)
    return package.purge_at


class AgentRepository:
    """PostgreSQL repository for crawler records, image bytes, and editorial results."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        database_url: str | None = None,
    ) -> None:
        settings = get_settings()
        self.path: Path | None = None
        self.database_url: str | None = None
        if path is not None:
            configured = path
            assert configured is not None
            self.path = Path(configured)
            if not self.path.is_absolute():
                self.path = Path.cwd() / self.path
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.dialect = "sqlite"
        elif settings.database_backend == "sqlite":
            self.path = Path(settings.database_path or "data/ani_menu_local.sqlite3")
            if not self.path.is_absolute():
                self.path = Path.cwd() / self.path
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.dialect = "sqlite"
        elif database_url or settings.database_url:
            self.database_url = database_url or settings.database_url
            self.dialect = "postgresql"
        elif settings.database_path:
            self.path = Path(settings.database_path)
            if not self.path.is_absolute():
                self.path = Path.cwd() / self.path
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.dialect = "sqlite"
        else:
            raise RuntimeError(
                "PostgreSQL 연결 주소가 없습니다. .env의 ANI_MENU_DATABASE_URL을 설정하세요."
            )
        self.initialize()

    def connect(self):
        if self.dialect == "sqlite":
            assert self.path is not None
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            return connection

        _prepare_windows_psycopg_dlls()
        import psycopg
        from psycopg.rows import dict_row

        assert self.database_url is not None
        return _PostgresConnection(
            psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=10)
        )

    def active_content_types(self) -> list[dict[str, str]]:
        """Return the editable, active taxonomy used by generation and feed ranking."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT type_code, label_ko FROM content_types "
                "WHERE active=1 ORDER BY sort_order, type_code"
            ).fetchall()
        return [{"code": row["type_code"], "label_ko": row["label_ko"]} for row in rows]

    def initialize(self) -> None:
        with self.connect() as connection:
            schema = """
                CREATE TABLE IF NOT EXISTS agent_records (
                    agent_type TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    record_kind TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    region TEXT NOT NULL,
                    event_start TEXT,
                    event_end TEXT,
                    valid_until TEXT,
                    expires_at TEXT,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (agent_type, record_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_records_expiry
                    ON agent_records(expires_at);
                CREATE INDEX IF NOT EXISTS idx_agent_records_request
                    ON agent_records(request_id);
                CREATE TABLE IF NOT EXISTS content_types (
                    type_code TEXT PRIMARY KEY,
                    label_ko TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS rejected_archive (
                    request_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    content_kind TEXT NOT NULL,
                    content_type TEXT,
                    review_failures INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL,
                    package_json TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    purge_at TEXT
                );
                CREATE TABLE IF NOT EXISTS feed_queue (
                    request_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    content_type TEXT,
                    package_json TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready',
                    valid_until TEXT,
                    purge_at TEXT,
                    event_start TEXT,
                    event_end TEXT,
                    event_location TEXT,
                    event_country TEXT,
                    event_region TEXT NOT NULL DEFAULT 'unknown'
                );
                CREATE TABLE IF NOT EXISTS content_drafts (
                    request_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    content_kind TEXT NOT NULL,
                    content_type TEXT,
                    status TEXT NOT NULL,
                    package_json TEXT NOT NULL,
                    valid_until TEXT NOT NULL,
                    purge_at TEXT NOT NULL,
                    quality_score INTEGER,
                    quality_report_json TEXT,
                    event_start TEXT,
                    event_end TEXT,
                    event_location TEXT,
                    event_country TEXT,
                    event_region TEXT NOT NULL DEFAULT 'unknown',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS content_cards (
                    request_id TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    eyebrow TEXT NOT NULL,
                    title_lines_json TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    fact_ids_json TEXT NOT NULL,
                    image_id TEXT NOT NULL,
                    visual_mode TEXT NOT NULL DEFAULT 'source_image',
                    image_explanation TEXT NOT NULL,
                    layout_json TEXT NOT NULL,
                    rendered_image_key TEXT,
                    overlay_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (request_id, page)
                );
                CREATE TABLE IF NOT EXISTS content_work_links (
                    request_id TEXT NOT NULL,
                    menu_work_id INTEGER NOT NULL,
                    sort_order INTEGER NOT NULL,
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (request_id, menu_work_id),
                    UNIQUE (request_id, sort_order)
                );
                CREATE TABLE IF NOT EXISTS image_blobs (
                    sha256 TEXT PRIMARY KEY,
                    media_type TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    binary_data BYTEA NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_image_blobs_created
                    ON image_blobs(created_at);
                """
            connection.executescript(schema)
            migrations = {
                "agent_records": {"valid_until": "TEXT"},
                "rejected_archive": {"purge_at": "TEXT", "content_type": "TEXT"},
                "feed_queue": {
                    "valid_until": "TEXT",
                    "purge_at": "TEXT",
                    "content_type": "TEXT",
                    "event_start": "TEXT",
                    "event_end": "TEXT",
                    "event_location": "TEXT",
                    "event_country": "TEXT",
                    "event_region": "TEXT NOT NULL DEFAULT 'unknown'",
                },
                "content_drafts": {
                    "quality_score": "INTEGER",
                    "quality_report_json": "TEXT",
                    "content_type": "TEXT",
                    "event_start": "TEXT",
                    "event_end": "TEXT",
                    "event_location": "TEXT",
                    "event_country": "TEXT",
                    "event_region": "TEXT NOT NULL DEFAULT 'unknown'",
                },
                "content_cards": {
                    "rendered_image_key": "TEXT",
                    "visual_mode": "TEXT NOT NULL DEFAULT 'source_image'",
                    "overlay_json": "TEXT NOT NULL DEFAULT '{}'",
                },
            }
            if self.dialect == "sqlite":
                for table, columns in migrations.items():
                    existing = {
                        row["name"]
                        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
                    }
                    for column, declaration in columns.items():
                        if column not in existing:
                            connection.execute(
                                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
                            )
            else:
                for table, columns in migrations.items():
                    for column, declaration in columns.items():
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {declaration}"
                        )
            for table in ("content_drafts", "feed_queue"):
                for row in connection.execute(
                    f"SELECT request_id, package_json FROM {table}"
                ).fetchall():
                    try:
                        package_data = json.loads(row["package_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    event_region = package_data.get("event_region") or "unknown"
                    if event_region not in {"domestic", "overseas", "unknown"}:
                        event_region = "unknown"
                    connection.execute(
                        f"UPDATE {table} SET event_start=COALESCE(event_start, ?), "
                        "event_end=COALESCE(event_end, ?), "
                        "event_location=COALESCE(event_location, ?), "
                        "event_country=COALESCE(event_country, ?), "
                        "event_region=CASE WHEN event_region='unknown' "
                        "THEN ? ELSE event_region END WHERE request_id=?",
                        (
                            package_data.get("event_start"),
                            package_data.get("event_end"),
                            package_data.get("event_location"),
                            package_data.get("event_country"),
                            event_region,
                            row["request_id"],
                        ),
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_records_valid_until "
                "ON agent_records(valid_until)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_content_drafts_purge ON content_drafts(purge_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_content_drafts_type_status "
                "ON content_drafts(content_type, status)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_feed_queue_type_status "
                "ON feed_queue(content_type, status, queued_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_feed_queue_event_region_dates "
                "ON feed_queue(event_region, event_start, event_end)"
            )
            for order, (content_type, label) in enumerate(CONTENT_TYPE_LABELS.items(), start=1):
                connection.execute(
                    "INSERT INTO content_types (type_code, label_ko, sort_order, active) "
                    "VALUES (?, ?, ?, 1) ON CONFLICT(type_code) DO NOTHING",
                    (content_type.value, label, order),
                )
            # The public taxonomy is now `review`; migrate legacy package and index values.
            connection.execute("DELETE FROM content_types WHERE type_code='review_analysis'")
            # `behind` remains a model alias so old payloads can be read, but it is
            # no longer an active feed type; new and legacy records route as humor.
            connection.execute("UPDATE content_types SET active=0 WHERE type_code='behind'")
            for table in ("content_drafts", "feed_queue", "rejected_archive"):
                country_column = "event_country" if table != "rejected_archive" else "NULL"
                rows = connection.execute(
                    f"SELECT request_id, content_type, {country_column} AS event_country, "
                    f"package_json FROM {table}"
                ).fetchall()
                for row in rows:
                    try:
                        package_data = json.loads(row["package_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if row["content_type"] and not package_data.get("content_type"):
                        package_data["content_type"] = row["content_type"]
                    package = ContentPackage.model_validate(package_data)
                    code = package.content_type
                    country = package.event_country
                    if row["content_type"] == "review_analysis":
                        code = "review"
                        package.content_type = code
                    elif code == "behind":
                        code = "humor"
                        package.content_type = code
                    if code != row["content_type"] or country != row["event_country"]:
                        if table == "rejected_archive":
                            connection.execute(
                                "UPDATE rejected_archive SET content_type=?, package_json=? "
                                "WHERE request_id=?",
                                (code, package.model_dump_json(), row["request_id"]),
                            )
                        else:
                            connection.execute(
                                f"UPDATE {table} SET content_type=?, event_country=?, "
                                "package_json=? "
                                "WHERE request_id=?",
                                (code, country, package.model_dump_json(), row["request_id"]),
                            )
            for row in connection.execute(
                "SELECT agent_type, record_id, payload_json FROM agent_records"
            ).fetchall():
                try:
                    payload = json.loads(row["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                changed = False
                if payload.get("content_type") == "behind":
                    payload["content_type"] = "humor"
                    changed = True
                payload_content_types = payload.get("content_types")
                if isinstance(payload_content_types, list) and "behind" in payload_content_types:
                    payload["content_types"] = list(
                        dict.fromkeys(
                            "humor" if code == "behind" else code
                            for code in payload_content_types
                        )
                    )
                    changed = True
                if changed:
                    connection.execute(
                        "UPDATE agent_records SET payload_json=? "
                        "WHERE agent_type=? AND record_id=?",
                        (
                            json.dumps(payload, ensure_ascii=False),
                            row["agent_type"],
                            row["record_id"],
                        ),
                    )
            for table in ("content_drafts", "feed_queue", "rejected_archive"):
                rows = connection.execute(
                    f"SELECT request_id, package_json FROM {table} WHERE content_type IS NULL"
                ).fetchall()
                for row in rows:
                    try:
                        content_type = json.loads(row["package_json"]).get("content_type")
                    except (TypeError, json.JSONDecodeError):
                        content_type = None
                    if content_type:
                        connection.execute(
                            f"UPDATE {table} SET content_type=? WHERE request_id=?",
                            (content_type, row["request_id"]),
                        )
            feed_rows = connection.execute(
                "SELECT request_id, package_json FROM feed_queue "
                "WHERE valid_until IS NULL OR purge_at IS NULL"
            ).fetchall()
            for row in feed_rows:
                try:
                    package = ContentPackage.model_validate_json(row["package_json"])
                    has_expiry_anchor = bool(package.event_end) or any(
                        fact.details.get(key)
                        for fact in package.facts
                        for key in ("event_end", "deadline", "release_date", "valid_until")
                    )
                    if has_expiry_anchor:
                        prepare_package_lifecycle(package)
                    else:
                        package.feed_valid_until = _utc_now().isoformat()
                        package.purge_at = (
                            _utc_now() + timedelta(days=POLICY.default_event_retention_days)
                        ).isoformat()
                    connection.execute(
                        "UPDATE feed_queue SET valid_until=?, purge_at=?, package_json=? "
                        "WHERE request_id=?",
                        (
                            package.feed_valid_until,
                            package.purge_at,
                            package.model_dump_json(),
                            row["request_id"],
                        ),
                    )
                except Exception:
                    connection.execute(
                        "UPDATE feed_queue SET status='expired', valid_until=?, purge_at=? "
                        "WHERE request_id=?",
                        (_utc_now().isoformat(), _utc_now().isoformat(), row["request_id"]),
                    )
            rejected_rows = connection.execute(
                "SELECT request_id, package_json FROM rejected_archive WHERE purge_at IS NULL"
            ).fetchall()
            for row in rejected_rows:
                try:
                    package = ContentPackage.model_validate_json(row["package_json"])
                    has_expiry_anchor = bool(package.event_end) or any(
                        fact.details.get(key)
                        for fact in package.facts
                        for key in ("event_end", "deadline", "release_date", "valid_until")
                    )
                    if has_expiry_anchor:
                        prepare_package_lifecycle(package)
                    else:
                        package.purge_at = (
                            _utc_now() + timedelta(days=POLICY.default_event_retention_days)
                        ).isoformat()
                    connection.execute(
                        "UPDATE rejected_archive SET purge_at=? WHERE request_id=?",
                        (package.purge_at, row["request_id"]),
                    )
                except Exception:
                    connection.execute(
                        "UPDATE rejected_archive SET purge_at=? WHERE request_id=?",
                        (
                            (
                                _utc_now() + timedelta(days=POLICY.default_event_retention_days)
                            ).isoformat(),
                            row["request_id"],
                        ),
                    )
        self.cleanup_expired()

    def prepare_lifecycle(self, package: ContentPackage) -> ContentPackage:
        return prepare_package_lifecycle(package)

    def get_work_content_counts(self) -> dict[int, dict[str, int]]:
        """Return generated/ready counts per canonical work; this never caps generation."""
        query = """
            SELECT links.menu_work_id,
                   COUNT(DISTINCT drafts.request_id) AS draft_count,
                   COUNT(DISTINCT queue.request_id) AS ready_count
            FROM content_work_links AS links
            LEFT JOIN content_drafts AS drafts
              ON drafts.request_id = links.request_id
            LEFT JOIN feed_queue AS queue
              ON queue.request_id = links.request_id AND queue.status = 'ready'
            GROUP BY links.menu_work_id
        """
        with self.connect() as connection:
            rows = connection.execute(query).fetchall()
        return {
            int(row["menu_work_id"]): {
                "draft_count": int(row["draft_count"] or 0),
                "ready_count": int(row["ready_count"] or 0),
            }
            for row in rows
        }

    def upsert_record(
        self,
        *,
        agent_type: str,
        record_id: str,
        record_kind: str,
        package: ContentPackage,
        payload: dict[str, Any],
        status: str = "collected",
        connection=None,
    ) -> None:
        now = _utc_now().isoformat()
        prepare_package_lifecycle(package)
        expires_at = _package_expiration(package)
        query = """
                INSERT INTO agent_records (
                    agent_type, record_id, record_kind, request_id, topic, region,
                    event_start, event_end, valid_until, expires_at, status, payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_type, record_id) DO UPDATE SET
                    record_kind=excluded.record_kind,
                    request_id=excluded.request_id,
                    topic=excluded.topic,
                    region=excluded.region,
                    event_start=excluded.event_start,
                    event_end=excluded.event_end,
                    valid_until=excluded.valid_until,
                    expires_at=excluded.expires_at,
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """
        params = (
            agent_type,
            record_id,
            record_kind,
            package.request_id,
            package.topic,
            package.audience_region,
            package.event_start,
            package.event_end,
            package.feed_valid_until,
            expires_at,
            status,
            json.dumps(payload, ensure_ascii=False),
            now,
            now,
        )
        if connection is not None:
            connection.execute(query, params)
        else:
            with self.connect() as db:
                db.execute(query, params)

    def persist_information(self, package: ContentPackage) -> int:
        count = 0
        for source in package.sources:
            source_payload = source.model_dump(mode="json")
            source_payload["menu_work_ids"] = list(package.menu_work_ids)
            source_payload["work_match_evidence"] = [
                item.model_dump(mode="json") for item in package.work_match_evidence
            ]
            self.upsert_record(
                agent_type="information",
                record_id=f"source:{package.request_id}:{source.id}",
                record_kind="source",
                package=package,
                payload=source_payload,
            )
            count += 1
        for fact in package.facts:
            self.upsert_record(
                agent_type="information",
                record_id=f"fact:{package.request_id}:{fact.id}",
                record_kind="fact",
                package=package,
                payload=fact.model_dump(mode="json"),
            )
            count += 1
        return count

    def persist_humor(self, package: ContentPackage) -> int:
        count = 0
        for source in package.sources:
            source_payload = source.model_dump(mode="json")
            source_payload["menu_work_ids"] = list(package.menu_work_ids)
            source_payload["work_match_evidence"] = [
                item.model_dump(mode="json") for item in package.work_match_evidence
            ]
            self.upsert_record(
                agent_type="humor",
                record_id=f"source:{package.request_id}:{source.id}",
                record_kind="trend_source",
                package=package,
                payload=source_payload,
                status="trend_only",
            )
            count += 1
        for fact in package.facts:
            self.upsert_record(
                agent_type="humor",
                record_id=f"idea:{package.request_id}:{fact.id}",
                record_kind="humor_idea",
                package=package,
                payload=fact.model_dump(mode="json"),
                status="needs_editorial_review",
            )
            count += 1
        return count

    def persist_images(self, package: ContentPackage) -> int:
        with self.connect() as connection:
            for index, image in enumerate(package.images):
                binary_data = image.content_bytes
                source_path = Path(image.local_path) if image.local_path else None
                if source_path and not source_path.is_absolute():
                    source_path = Path.cwd() / source_path
                if binary_data is None and source_path and source_path.is_file():
                    binary_data = source_path.read_bytes()
                if binary_data is not None:
                    if len(binary_data) > MAX_IMAGE_BYTES:
                        raise ValueError("이미지 파일이 20MB 제한을 초과합니다.")
                    try:
                        with Image.open(io.BytesIO(binary_data)) as decoded:
                            width, height = decoded.size
                            image_format = decoded.format
                            decoded.verify()
                    except Exception as exc:
                        raise ValueError("정상적인 이미지 바이너리가 아닙니다.") from exc
                    if image_format not in {"JPEG", "PNG", "WEBP", "GIF"}:
                        raise ValueError(f"지원하지 않는 이미지 포맷입니다: {image_format}")
                    if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                        raise ValueError("이미지 픽셀 수가 4천만 제한을 초과합니다.")
                    file_hash = hashlib.sha256(binary_data).hexdigest()
                    media_type = (
                        image.media_type
                        or (mimetypes.guess_type(str(source_path))[0] if source_path else None)
                        or "application/octet-stream"
                    )
                    connection.execute(
                        "INSERT INTO image_blobs "
                        "(sha256, media_type, file_size_bytes, binary_data, created_at) "
                        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(sha256) DO NOTHING",
                        (
                            file_hash,
                            media_type,
                            len(binary_data),
                            binary_data,
                            _utc_now().isoformat(),
                        ),
                    )
                    package.images[index] = image.model_copy(
                        update={
                            "storage_key": file_hash,
                            "fingerprint": image.fingerprint or file_hash,
                            "media_type": media_type,
                            "file_size_bytes": len(binary_data),
                            "local_path": None,
                            "content_bytes": None,
                            "download_status": "downloaded",
                            "download_error": None,
                            "width": image.width or width,
                            "height": image.height or height,
                        }
                    )
                image = package.images[index]
                self.upsert_record(
                    agent_type="image",
                    record_id=f"image:{package.request_id}:{image.id}",
                    record_kind="image_asset",
                    package=package,
                    payload=image.model_dump(mode="json"),
                    status=image.rights_status.value,
                    connection=connection,
                )
        return len(package.images)

    def get_image_bytes(self, storage_key: str) -> tuple[bytes, str] | None:
        """Return a stored image's bytes and media type for rendering or feed delivery."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT binary_data, media_type FROM image_blobs WHERE sha256=?",
                (storage_key,),
            ).fetchone()
        if not row:
            return None
        return bytes(row["binary_data"]), row["media_type"]

    def persist_rendered_cards(
        self,
        package: ContentPackage,
        png_by_page: dict[int, bytes],
    ) -> ContentPackage:
        """Store one verified 1308×1632 PNG per card and attach its content-addressed key."""
        expected_pages = {card.page for card in package.cards}
        if set(png_by_page) != expected_pages:
            raise ValueError("렌더 PNG 페이지가 카드 페이지와 일치하지 않습니다.")
        with self.connect() as connection:
            for index, card in enumerate(package.cards):
                binary_data = png_by_page[card.page]
                try:
                    with Image.open(io.BytesIO(binary_data)) as decoded:
                        width, height = decoded.size
                        image_format = decoded.format
                        decoded.verify()
                except Exception as exc:
                    raise ValueError(
                        f"{card.page}장 렌더 파일을 이미지로 읽을 수 없습니다."
                    ) from exc
                if image_format != "PNG" or (width, height) != OUTPUT_IMAGE_SIZE:
                    raise ValueError(
                        f"{card.page}장 렌더는 "
                        f"{OUTPUT_IMAGE_SIZE[0]}×{OUTPUT_IMAGE_SIZE[1]} PNG여야 합니다. "
                        f"현재 {image_format} {width}×{height}"
                    )
                if len(binary_data) > MAX_IMAGE_BYTES:
                    raise ValueError(f"{card.page}장 PNG가 20MB 제한을 초과합니다.")
                storage_key = hashlib.sha256(binary_data).hexdigest()
                connection.execute(
                    "INSERT INTO image_blobs "
                    "(sha256, media_type, file_size_bytes, binary_data, created_at) "
                    "VALUES (?, 'image/png', ?, ?, ?) ON CONFLICT(sha256) DO NOTHING",
                    (storage_key, len(binary_data), binary_data, _utc_now().isoformat()),
                )
                package.cards[index] = card.model_copy(
                    update={
                        "rendered_image_key": storage_key,
                        "rendered_width": width,
                        "rendered_height": height,
                    }
                )
        return package

    def update_rendered_card_refs(self, package: ContentPackage) -> None:
        """Attach preview-rendered keys to existing records without changing queue order/status."""
        package_json = package.model_dump_json()
        with self.connect() as connection:
            for table in ("content_drafts", "feed_queue", "rejected_archive"):
                connection.execute(
                    f"UPDATE {table} SET package_json=? WHERE request_id=?",
                    (package_json, package.request_id),
                )
            for card in package.cards:
                connection.execute(
                    "UPDATE content_cards SET rendered_image_key=? WHERE request_id=? AND page=?",
                    (card.rendered_image_key, package.request_id, card.page),
                )

    def recent_copy_examples(
        self,
        *,
        exclude_request_id: str,
        limit: int = 6,
    ) -> list[dict[str, str]]:
        """Return recent cover/ending copy so the writer can avoid repeating its shape."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT request_id, topic, package_json FROM content_drafts "
                "WHERE request_id<>? ORDER BY updated_at DESC LIMIT ?",
                (exclude_request_id, max(limit * 4, limit)),
            ).fetchall()

        examples: list[dict[str, str]] = []
        for row in rows:
            try:
                stored_package = json.loads(row["package_json"])
                cards = stored_package.get("cards", [])
            except (TypeError, json.JSONDecodeError):
                continue
            if not cards:
                continue
            cards = sorted(cards, key=lambda card: card.get("page", 0))
            cover, ending = cards[0], cards[-1]

            def copy_text(card: dict[str, Any]) -> str:
                title = " ".join(card.get("title_lines", []))
                body = " ".join(card.get("body_sentences", []))
                return f"{title} {body}".strip()[:180]

            examples.append(
                {
                    "topic": row["topic"],
                    "cover": copy_text(cover),
                    "ending": copy_text(ending),
                    "editorial_angle": stored_package.get("editorial_angle", ""),
                    "cover_mode": stored_package.get("cover_mode", ""),
                    "ending_mode": stored_package.get("ending_mode", ""),
                }
            )
            if len(examples) >= limit:
                break
        return examples

    def find_duplicate_content(
        self,
        package: ContentPackage,
        *,
        retry_same_request: bool = False,
    ) -> dict[str, str] | None:
        """Find an existing same-work story before spending tokens on card generation."""
        with self.connect() as connection:
            rows = []
            for table in ("content_drafts", "feed_queue"):
                rows.extend(
                    connection.execute(
                        f"SELECT request_id, topic, package_json, status FROM {table}"
                    ).fetchall()
                )
            rows.extend(
                connection.execute(
                    "SELECT request_id, topic, package_json, 'rejected_archived' AS status "
                    "FROM rejected_archive"
                ).fetchall()
            )
        baseline_path = get_settings().duplicate_baseline_path
        if baseline_path and self.dialect == "sqlite":
            baseline = Path(baseline_path).expanduser().resolve()
            current = self.path.resolve() if self.path else None
            if baseline.is_file() and baseline != current:
                try:
                    readonly = sqlite3.connect(f"{baseline.as_uri()}?mode=ro", uri=True, timeout=10)
                    readonly.row_factory = sqlite3.Row
                    try:
                        for table in ("content_drafts", "feed_queue"):
                            try:
                                rows.extend(
                                    readonly.execute(
                                        "SELECT request_id, topic, package_json, status "
                                        f"FROM {table}"
                                    ).fetchall()
                                )
                            except sqlite3.OperationalError:
                                continue
                        try:
                            rows.extend(
                                readonly.execute(
                                    "SELECT request_id, topic, package_json, "
                                    "'rejected_archived' AS status FROM rejected_archive"
                                ).fetchall()
                            )
                        except sqlite3.OperationalError:
                            pass
                    finally:
                        readonly.close()
                except sqlite3.Error as exc:
                    raise RuntimeError(
                        "중복 기준 SQLite를 읽지 못해 생성을 안전하게 진행할 수 없습니다: "
                        f"{baseline}"
                    ) from exc
        seen: set[str] = set()
        for row in rows:
            request_id = str(row["request_id"])
            if request_id in seen:
                continue
            seen.add(request_id)
            try:
                existing = json.loads(row["package_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            retryable_status = row["status"] in {
                "generated_pending_review",
                "needs_crawler_update",
                "rejected_archived",
                "review_returned",
                "held_for_quality_review",
            }
            if retry_same_request and retryable_status:
                if request_id == package.request_id:
                    continue
                retryable_duplicate_reasons = {
                    "same_source_url",
                    "same_work_and_near_identical_source_title",
                    "same_work_and_overlapping_core_facts",
                }
                if duplicate_reason(package, existing) in retryable_duplicate_reasons:
                    continue
            reason = duplicate_reason(package, existing)
            if reason:
                return {"request_id": request_id, "topic": str(row["topic"]), "reason": reason}
        return None

    def load_content_package(
        self,
        request_id: str,
        content_kind: str,
        content_type: str | None = None,
    ) -> ContentPackage:
        """Load generation inputs from crawler-owned records, never from discovery captures."""
        if content_kind not in {"information", "humor"}:
            raise ValueError("content_kind는 information 또는 humor여야 합니다.")
        agent_type = content_kind
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT agent_type, record_kind, status, payload_json, topic, region, "
                "event_start, event_end, valid_until, expires_at FROM agent_records "
                "WHERE request_id=? AND agent_type IN (?, 'image') "
                "AND status NOT IN ('rejected', 'expired') "
                "ORDER BY created_at, record_id",
                (request_id, agent_type),
            ).fetchall()
        if not rows:
            raise ValueError(f"DB에 request_id={request_id!r} 자료가 없습니다.")

        sources = []
        facts = []
        images = []
        persisted_work_ids: list[int] = []
        persisted_work_evidence = []
        eligible_rows = []
        for row in rows:
            record_kind = row["record_kind"]
            payload = json.loads(row["payload_json"])
            if record_kind in {"source", "trend_source"}:
                sources.append(payload)
                for raw_work_id in payload.get("menu_work_ids", []):
                    try:
                        work_id = int(raw_work_id)
                    except (TypeError, ValueError):
                        continue
                    if work_id not in persisted_work_ids:
                        persisted_work_ids.append(work_id)
                persisted_work_evidence.extend(payload.get("work_match_evidence", []))
                eligible_rows.append(row)
            elif record_kind == "fact":
                if content_kind == "information" and payload.get("verified") is not True:
                    continue
                facts.append(payload)
                eligible_rows.append(row)
            elif record_kind == "humor_idea" and content_kind == "humor":
                facts.append(payload)
                eligible_rows.append(row)
            elif record_kind == "image_asset":
                if payload.get("storage_key") and not self.get_image_bytes(payload["storage_key"]):
                    payload["download_status"] = "failed"
                    payload["download_error"] = "PostgreSQL 이미지 바이너리를 찾을 수 없습니다."
                images.append(payload)
                eligible_rows.append(row)

        if not sources:
            raise ValueError(
                "생성에 사용할 출처 자료가 없습니다. 발견 후보/원문 캡처는 검증 후 적재해야 합니다."
            )
        if not facts:
            raise ValueError(
                "생성 가능한 사실/아이디어가 없습니다. discovery_candidate와 "
                "official_page_capture는 검증된 콘텐츠 자료가 아닙니다."
            )
        # Refresh the captured source's approved asset CDN from the current
        # source registry. This lets an approved editorial publisher's own
        # image CDN be fetched without treating it as official artwork.
        configured_sources = {source.id: source for source in load_source_registry().sources}
        for source in sources:
            configured = configured_sources.get(source.get("id", ""))
            if configured and configured.status == "active":
                source["allowed_asset_hosts"] = sorted(
                    set(source.get("allowed_asset_hosts", [])) | set(configured.allowed_asset_hosts)
                )
        valid_until = next(
            (row["valid_until"] for row in eligible_rows if row["valid_until"]), None
        )
        if valid_until and datetime.fromisoformat(valid_until) <= _utc_now():
            raise ValueError(
                "이 자료는 피드 노출 유효기간이 끝나 콘텐츠 생성에 사용할 수 없습니다."
            )
        source_map = {source["id"]: source for source in sources}
        for fact in facts:
            missing = set(fact.get("source_ids", [])) - source_map.keys()
            if missing:
                raise ValueError(
                    f"팩트 {fact.get('id')}의 DB 출처가 없습니다: {', '.join(sorted(missing))}"
                )
        validate_generation_sources(sources, facts, content_kind)

        meta = eligible_rows[0]
        event_start = meta["event_start"]
        event_end = meta["event_end"]
        event_location = None
        event_country = None
        event_region = "unknown"
        if content_kind == "information" and facts:
            # Event metadata is reconstructed from verified crawler facts because
            # agent_records stores structured dates but venue/country metadata lives
            # in quote-backed fact details.
            from ani_menu_agent.extraction import (
                _infer_event_location_from_facts,
                _verified_event_dates_from_facts,
            )
            from ani_menu_agent.models import Fact

            fact_models = [Fact.model_validate(fact) for fact in facts]
            fact_start, fact_end = _verified_event_dates_from_facts(fact_models)
            event_start = event_start or fact_start
            event_end = event_end or fact_end
            evidence_text = "\n".join(
                str(fact.details.get("source_quote", "")) for fact in fact_models
            )
            event_location, event_country, event_region = _infer_event_location_from_facts(
                fact_models, evidence_text
            )
        from ani_menu_agent.models import WorkMatchEvidence
        from ani_menu_agent.work_catalog import load_work_catalog

        valid_work_ids = {int(row["menu_work_id"]) for row in load_work_catalog()}
        persisted_work_ids = [
            work_id for work_id in persisted_work_ids if work_id in valid_work_ids
        ]
        work_evidence = []
        seen_evidence = set()
        for raw_evidence in persisted_work_evidence:
            try:
                evidence = WorkMatchEvidence.model_validate(raw_evidence)
            except (TypeError, ValueError):
                continue
            if evidence.menu_work_id not in valid_work_ids:
                continue
            signature = (
                evidence.menu_work_id,
                evidence.basis,
                evidence.matched_value,
                evidence.evidence_url,
            )
            if signature not in seen_evidence:
                seen_evidence.add(signature)
                work_evidence.append(evidence)
        if work_evidence:
            evidence_ids = list(dict.fromkeys(item.menu_work_id for item in work_evidence))
            # The stored work evidence is authoritative only when its IDs agree
            # with the stored package mapping; reject stale or partial metadata.
            if persisted_work_ids and set(evidence_ids) == set(persisted_work_ids):
                persisted_work_ids = evidence_ids
            else:
                work_evidence = []
        package = ContentPackage(
            request_id=request_id,
            topic=meta["topic"],
            content_kind=content_kind,
            content_type=content_type,
            audience_region=meta["region"],
            event_start=event_start,
            event_end=event_end,
            event_location=event_location,
            event_country=event_country,
            event_region=event_region,
            feed_valid_until=valid_until,
            purge_at=meta["expires_at"],
            retention_expires_at=meta["expires_at"],
            sources=sources,
            facts=facts,
            images=images,
            menu_work_ids=persisted_work_ids,
            work_match_evidence=work_evidence,
        )
        attach_menu_work_ids(package)
        if not package.menu_work_ids:
            raise ValueError(
                "대표 작품을 제목에서 menu_work_id로 매칭하지 못했습니다. "
                "작품 ID 없는 콘텐츠는 추천 피드에 적재하지 않습니다."
            )
        package.images = self._supplement_same_work_official_images(package)
        return package

    def _supplement_same_work_official_images(
        self,
        package: ContentPackage,
        *,
        max_images: int = 48,
    ) -> list[ImageAsset]:
        """Add reusable, provenance-checked public images for this exact work only.

        Text sources remain untouched: these images are visual assets, not factual
        citations. Assets must come from an active primary or editorial source,
        match its allowed page/CDN hosts, and resolve to a package work ID.
        """
        # Event and product cards require imagery of that exact event or item.
        # A same-franchise catalog image is not a valid substitute.
        if package.content_type in {"event", "goods"}:
            return package.images

        registry = load_source_registry()
        active_image_sources = {
            source.id: source
            for source in registry.sources
            if source.status == "active"
            and (
                source.role in {"primary", "editorial"}
                or (source.role == "reference" and source.category == "kitsu_anime_catalog")
            )
        }
        if not active_image_sources:
            return package.images

        existing_keys = {key for image in package.images for key in image_visual_keys(image)}
        image_rows_query = """
            SELECT ? AS asset_store,
                   image.request_id AS image_request_id,
                   image.payload_json AS image_payload,
                   source.payload_json AS source_payload,
                   (SELECT draft.content_type
                      FROM content_drafts AS draft
                     WHERE draft.request_id = image.request_id
                     LIMIT 1) AS origin_content_type
              FROM agent_records AS image
              LEFT JOIN agent_records AS source
                ON source.request_id = image.request_id
               AND source.record_kind = 'source'
               AND source.agent_type IN ('information', 'humor')
             WHERE image.agent_type = 'image'
               AND image.record_kind = 'image_asset'
               AND image.status NOT IN ('rejected', 'expired')
               AND (source.status IS NULL OR source.status NOT IN ('rejected', 'expired'))
             ORDER BY image.created_at DESC, image.record_id
        """
        rows = []
        try:
            with self.connect() as connection:
                rows.extend(connection.execute(image_rows_query, ("workspace",)).fetchall())
        except sqlite3.Error:
            pass

        # The generation workspace stays isolated. A configured duplicate-baseline
        # database is only an optional, read-only image reference library; only
        # source-proven assets resolving to this exact work are copied into the
        # new package and persisted by the current workspace.
        reference_path = os.environ.get("ANI_MENU_IMAGE_REFERENCE_PATH") or os.environ.get(
            "ANI_MENU_DUPLICATE_BASELINE_PATH", ""
        )
        if reference_path:
            try:
                resolved_reference = Path(reference_path).resolve()
                current_path = self.path.resolve()
                if resolved_reference != current_path and resolved_reference.is_file():
                    reference_uri = f"{resolved_reference.as_uri()}?mode=ro"
                    with sqlite3.connect(reference_uri, uri=True) as connection:
                        connection.row_factory = sqlite3.Row
                        rows.extend(
                            connection.execute(image_rows_query, ("reference",)).fetchall()
                        )
            except (OSError, sqlite3.Error, ValueError):
                # A missing/unreadable reference library must not stop generation.
                pass
        if not rows:
            return package.images

        result = list(package.images)
        seen_ids = {image.id for image in result}
        source_work_cache: dict[tuple[str, str, str], list[int]] = {}
        for row in rows:
            if len(result) >= len(package.images) + max_images:
                break
            if row["image_request_id"] == package.request_id:
                continue
            try:
                image_payload = json.loads(row["image_payload"])
            except (TypeError, json.JSONDecodeError):
                continue
            source_payload = image_payload.get("provenance_source") or row["source_payload"]
            if not source_payload:
                continue
            try:
                source = SourceEvidence.model_validate(
                    json.loads(source_payload)
                    if isinstance(source_payload, str)
                    else source_payload
                )
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            configured = active_image_sources.get(source.id)
            if configured is None:
                continue
            source = source.model_copy(
                update={
                    "is_official": configured.role == "primary",
                    "role": configured.role,
                    "allowed_asset_hosts": configured.allowed_asset_hosts,
                    "publisher": configured.name,
                }
            )
            cache_key = (source.id, source.title, str(source.url))
            work_ids = source_work_cache.get(cache_key)
            if work_ids is None:
                source_package = ContentPackage(
                    request_id=f"image-source-{source.id}",
                    topic=source.title,
                    content_kind="information",
                    sources=[source],
                )
                work_ids = resolve_menu_work_ids(source_package)
                if not work_ids and image_payload.get("menu_work_ids"):
                    work_ids = [int(value) for value in image_payload["menu_work_ids"]]
                source_work_cache[cache_key] = work_ids
            if not set(package.menu_work_ids).intersection(work_ids):
                continue
            try:
                image = ImageAsset.model_validate(image_payload)
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            origin_content_type = row["origin_content_type"]
            if image.asset_kind == "unknown":
                image = _classify_legacy_reference_art(
                    image,
                    source,
                    source_role=configured.role,
                    origin_content_type=origin_content_type,
                )
                if image is None:
                    continue
            if row["asset_store"] == "reference":
                # A baseline storage key points into the other database. Keep
                # the public URL/provenance, but let the active workspace fetch
                # and persist its own binary instead of retaining a dangling key.
                image = image.model_copy(
                    update={
                        "storage_key": None,
                        "local_path": None,
                    }
                )
            matches_source = (
                image.public_official_source_matches([source])
                if configured.role == "primary"
                else image.public_editorial_source_matches([source])
            )
            if not matches_source:
                continue
            if image.download_status not in {"downloaded", "available"}:
                continue
            if image.used_in_previous_content or image.official != (configured.role == "primary"):
                continue
            if image.rights_status == RightsStatus.NEEDS_REVIEW:
                allowed = (
                    POLICY.allow_public_official_image_risk
                    if image.official
                    else POLICY.allow_public_editorial_image_risk
                )
                if not allowed:
                    continue
                image = image.model_copy(
                    update={
                        "rights_status": (
                            RightsStatus.OFFICIAL_PUBLIC_RISK_ACCEPTED
                            if image.official
                            else RightsStatus.PUBLIC_EDITORIAL_RISK_ACCEPTED
                        ),
                        "rights_evidence_url": image.source_url,
                        "rights_checked_at": image.rights_checked_at or _utc_now().isoformat(),
                        "attribution_text": image.attribution_text.strip()
                        or f"이미지 출처: {source.publisher} · {source.title}",
                    }
                )
            if not image.eligible_for_editorial_use(
                [source],
                allow_risk_accepted=(
                    POLICY.allow_public_official_image_risk
                    or POLICY.allow_public_editorial_image_risk
                ),
            ):
                continue
            if (
                row["asset_store"] == "workspace"
                and image.storage_key
                and not self.get_image_bytes(image.storage_key)
            ):
                continue
            image = image.model_copy(
                update={
                    "provenance_source": source,
                    "menu_work_ids": work_ids,
                    **(
                        {"download_status": "pending"}
                        if row["asset_store"] == "reference"
                        else {}
                    ),
                }
            )
            keys = image_visual_keys(image)
            if image.id in seen_ids or existing_keys.intersection(keys):
                continue
            result.append(image)
            seen_ids.add(image.id)
            existing_keys.update(keys)
        return result

    def mark_request_status(self, request_id: str, status: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE agent_records SET status=?, updated_at=? WHERE request_id=?",
                (status, _utc_now().isoformat(), request_id),
            )
            return cursor.rowcount

    def persist_discovery(self, items: list[dict[str, Any]], region_policy: str) -> int:
        count = 0
        now = _utc_now().isoformat()
        with self.connect() as connection:
            for item in items:
                payload = dict(item)
                is_trend = item.get("source_role") == "trend"
                agent_type = "humor" if is_trend else "information"
                record_kind = "trend_source" if is_trend else "discovery_candidate"
                record_prefix = "trend" if is_trend else "discovery"
                status = "trend_only" if is_trend else item.get("status", "discovery_only")
                prior = connection.execute(
                    "SELECT status, payload_json FROM agent_records "
                    "WHERE agent_type=? AND record_id=?",
                    (agent_type, f"{record_prefix}:{item['id']}"),
                ).fetchone()
                if prior and prior["status"] == "unresolved_work_id":
                    previous_payload = json.loads(prior["payload_json"])
                    if previous_payload.get("work_resolution"):
                        payload["work_resolution"] = previous_payload["work_resolution"]
                    status = "unresolved_work_id"
                connection.execute(
                    """
                    INSERT INTO agent_records (
                        agent_type, record_id, record_kind, request_id, topic, region,
                        event_start, event_end, valid_until, expires_at, status, payload_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_type, record_id) DO UPDATE SET
                        topic=excluded.topic, region=excluded.region,
                        valid_until=excluded.valid_until,
                        expires_at=excluded.expires_at,
                        status=excluded.status, payload_json=excluded.payload_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        agent_type,
                        f"{record_prefix}:{item['id']}",
                        record_kind,
                        item["id"],
                        item["title"],
                        item.get("region", "global"),
                        item.get("published_at"),
                        now,
                        (_utc_now() + timedelta(days=30)).isoformat(),
                        f"{status}:{region_policy}",
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                if is_trend and item.get("is_community_post"):
                    source_payload = {
                        "id": item["source_id"],
                        "title": item["title"],
                        "url": item["url"],
                        "publisher": "Mastodon public community post",
                        "is_official": False,
                        "published_at": item.get("published_at"),
                        "role": "trend",
                        "region": item.get("region", "global"),
                    }
                    idea_payload = {
                        "id": f"idea:{item['id']}",
                        "statement": str(item.get("summary", "")),
                        "source_ids": [item["source_id"]],
                        "verified": False,
                        "time_sensitive": False,
                        "fact_type": "community_opinion",
                        "details": {
                            "source_quote": str(item.get("summary", "")),
                            "source_url": str(item["url"]),
                            "attribution_policy": "paraphrase_without_author_identity",
                        },
                    }
                    for record_id, record_kind, idea in (
                        (
                            f"source:{item['id']}:{item['source_id']}",
                            "trend_source",
                            source_payload,
                        ),
                        (f"idea:{item['id']}", "humor_idea", idea_payload),
                    ):
                        connection.execute(
                            """
                            INSERT INTO agent_records (
                                agent_type, record_id, record_kind, request_id, topic, region,
                                event_start, event_end, valid_until, expires_at, status,
                                payload_json, created_at, updated_at
                            ) VALUES ('humor', ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'trend_only', ?, ?, ?)
                            ON CONFLICT(agent_type, record_id) DO UPDATE SET
                                topic=excluded.topic, region=excluded.region,
                                valid_until=excluded.valid_until, expires_at=excluded.expires_at,
                                status=excluded.status, payload_json=excluded.payload_json,
                                updated_at=excluded.updated_at
                            """,
                            (
                                record_id,
                                record_kind,
                                item["id"],
                                item["title"],
                                item.get("region", "global"),
                                item.get("published_at"),
                                now,
                                (_utc_now() + timedelta(days=30)).isoformat(),
                                json.dumps(idea, ensure_ascii=False),
                                now,
                                now,
                            ),
                        )
                count += 1
        return count

    def mark_discovery_work_unresolved(self, candidate_id: str, reason: str) -> bool:
        """Keep an unresolved work title searchable instead of treating it as a dead lead."""
        now = _utc_now().isoformat()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT agent_type, record_id, payload_json FROM agent_records "
                "WHERE request_id=? AND record_kind='discovery_candidate' "
                "ORDER BY updated_at DESC LIMIT 1",
                (candidate_id,),
            ).fetchone()
            if row is None:
                return False
            payload = json.loads(row["payload_json"])
            payload["work_resolution"] = {
                "status": "unresolved_work_id",
                "reason": reason,
                "recorded_at": now,
            }
            connection.execute(
                "UPDATE agent_records SET status=?, payload_json=?, updated_at=? "
                "WHERE agent_type=? AND record_id=?",
                (
                    "unresolved_work_id",
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    row["agent_type"],
                    row["record_id"],
                ),
            )
            return True

    def load_discovery_candidate(self, candidate_id: str) -> dict[str, Any]:
        """Load one lead by its discovery ID; it remains unverified until recaptured."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_records "
                "WHERE request_id=? AND record_kind='discovery_candidate' "
                "ORDER BY updated_at DESC LIMIT 1",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"발견 후보를 찾을 수 없습니다: {candidate_id}")
        payload = json.loads(row["payload_json"])
        if payload.get("id") != candidate_id or not payload.get("url"):
            raise ValueError("DB 후보 레코드의 ID 또는 원문 URL이 올바르지 않습니다.")
        return payload

    def list_source_discovery_captures(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Return captured candidate pages for discovering previously unregistered hosts."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM agent_records "
                "WHERE record_kind='official_page_capture' "
                "ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 5000)),),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def persist_source_candidate(self, candidate: dict[str, Any]) -> None:
        """Save a new-site lead separately from individual article candidates."""
        now = _utc_now().isoformat()
        payload = dict(candidate)
        candidate_id = str(payload["id"])
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_records (
                    agent_type, record_id, record_kind, request_id, topic, region,
                    expires_at, status, payload_json, created_at, updated_at
                ) VALUES ('information', ?, 'source_candidate', ?, ?, 'global', ?, ?, ?, ?, ?)
                ON CONFLICT(agent_type, record_id) DO UPDATE SET
                    topic=excluded.topic, status=excluded.status,
                    payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (
                    candidate_id,
                    candidate_id,
                    str(payload.get("host") or candidate_id),
                    (_utc_now() + timedelta(days=3650)).isoformat(),
                    str(payload.get("status") or "pending_validation"),
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    def load_source_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_records "
                "WHERE record_id=? AND record_kind='source_candidate' LIMIT 1",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"출처 후보를 찾을 수 없습니다: {candidate_id}")
        return json.loads(row["payload_json"])

    def list_source_candidates(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT payload_json FROM agent_records WHERE record_kind='source_candidate'"
        )
        params: list[Any] = []
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def persist_page_capture(
        self,
        capture: dict[str, Any],
        region: str,
        event_end: str | None = None,
    ) -> None:
        """공식 페이지에서 가져온 원문 캡처를 미검증 상태로 적재합니다."""
        now = _utc_now().isoformat()
        page_url = str(capture["url"])
        record_id = f"page:{hashlib.sha1(page_url.encode('utf-8')).hexdigest()[:16]}"
        crawl_request_id = f"crawl-{hashlib.sha1(page_url.encode('utf-8')).hexdigest()[:14]}"
        current_image_ids = {
            str(image.get("id"))
            for image in capture.get("images", [])
            if isinstance(image, dict) and image.get("id")
        }
        title = str(capture.get("title") or page_url)
        with self.connect() as connection:
            # A retry can recapture fewer assets than an earlier run. Remove
            # stale per-request image records before extraction so old related-
            # article images cannot silently re-enter the new package.
            if current_image_ids:
                image_record_ids = [
                    f"image:{crawl_request_id}:{image_id}" for image_id in current_image_ids
                ]
                placeholders = ",".join("?" for _ in image_record_ids)
                connection.execute(
                    "DELETE FROM agent_records WHERE agent_type='image' "
                    "AND record_kind='image_asset' AND request_id=? "
                    f"AND record_id NOT IN ({placeholders})",
                    (crawl_request_id, *image_record_ids),
                )
            else:
                connection.execute(
                    "DELETE FROM agent_records WHERE agent_type='image' "
                    "AND record_kind='image_asset' AND request_id=?",
                    (crawl_request_id,),
                )
            connection.execute(
                """
                INSERT INTO agent_records (
                    agent_type, record_id, record_kind, request_id, topic, region,
                    event_start, event_end, expires_at, status, payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_type, record_id) DO UPDATE SET
                    topic=excluded.topic,
                    region=excluded.region,
                    event_end=excluded.event_end,
                    expires_at=excluded.expires_at,
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    "information",
                    record_id,
                    "official_page_capture",
                    record_id,
                    title,
                    region,
                    event_end,
                    _expiration(event_end)
                    or (
                        _utc_now() + timedelta(days=POLICY.default_event_retention_days)
                    ).isoformat(),
                    "unverified_capture",
                    json.dumps(capture, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    def persist_generated_content(
        self,
        package: ContentPackage,
        status: str,
        quality_report: dict[str, Any] | None = None,
    ) -> None:
        """Save the latest generated version and its card rows, including drafts under review."""
        attach_menu_work_ids(package)
        if package.content_type != "event":
            package.event_country = None
        prepare_package_lifecycle(package)
        now = _utc_now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO content_drafts (
                    request_id, topic, content_kind, content_type, status, package_json,
                    valid_until, purge_at, quality_score, quality_report_json,
                    event_start, event_end, event_location, event_country, event_region,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    topic=excluded.topic,
                    content_kind=excluded.content_kind,
                    content_type=excluded.content_type,
                    status=excluded.status,
                    package_json=excluded.package_json,
                    valid_until=excluded.valid_until,
                    purge_at=excluded.purge_at,
                    quality_score=excluded.quality_score,
                    quality_report_json=excluded.quality_report_json,
                    event_start=excluded.event_start,
                    event_end=excluded.event_end,
                    event_location=excluded.event_location,
                    event_country=excluded.event_country,
                    event_region=excluded.event_region,
                    updated_at=excluded.updated_at
                """,
                (
                    package.request_id,
                    package.topic,
                    package.content_kind.value,
                    package.content_type,
                    status,
                    package.model_dump_json(),
                    package.feed_valid_until,
                    package.purge_at,
                    int(quality_report["total"]) if quality_report else None,
                    json.dumps(quality_report, ensure_ascii=False) if quality_report else None,
                    package.event_start,
                    package.event_end,
                    package.event_location,
                    package.event_country,
                    package.event_region,
                    now,
                    now,
                ),
            )
            if status != "ready_for_feed":
                connection.execute(
                    "UPDATE feed_queue SET status='held_for_quality_review' WHERE request_id=?",
                    (package.request_id,),
                )
            connection.execute(
                "DELETE FROM content_cards WHERE request_id=?", (package.request_id,)
            )
            self._replace_work_links(connection, package)
            for card in package.cards:
                connection.execute(
                    """
                    INSERT INTO content_cards (
                        request_id, page, role, eyebrow, title_lines_json, body_json,
                        fact_ids_json, image_id, visual_mode, image_explanation, layout_json,
                        rendered_image_key, overlay_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        package.request_id,
                        card.page,
                        card.role,
                        card.eyebrow,
                        json.dumps(card.title_lines, ensure_ascii=False),
                        json.dumps(card.body_sentences, ensure_ascii=False),
                        json.dumps(card.fact_ids, ensure_ascii=False),
                        card.image_id,
                        card.visual_mode,
                        card.image_explanation,
                        card.layout.model_dump_json(),
                        card.rendered_image_key,
                        _card_overlay_json(card),
                    ),
                )

    def set_content_status(self, request_id: str, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE content_drafts SET status=?, updated_at=? WHERE request_id=?",
                (status, _utc_now().isoformat(), request_id),
            )
            # A previously approved revision can remain in the queue when a later
            # retry of the same request is rejected or returned for new evidence.
            # Keep that stale revision out of the serving set without deleting it.
            if status != "ready_for_feed":
                connection.execute(
                    "UPDATE feed_queue SET status='held_for_quality_review' "
                    "WHERE request_id=? AND status='ready'",
                    (request_id,),
                )

    def archive_rejected(
        self,
        package: ContentPackage,
        review_failures: int,
        reasons: list[dict[str, Any]],
    ) -> None:
        now = _utc_now().isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO rejected_archive (
                    request_id, topic, content_kind, content_type, review_failures,
                    reasons_json, package_json, archived_at, purge_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    review_failures=excluded.review_failures,
                    content_type=excluded.content_type,
                    reasons_json=excluded.reasons_json,
                    package_json=excluded.package_json,
                    archived_at=excluded.archived_at,
                    purge_at=excluded.purge_at
                """,
                (
                    package.request_id,
                    package.topic,
                    package.content_kind.value,
                    package.content_type,
                    review_failures,
                    json.dumps(reasons, ensure_ascii=False),
                    package.model_dump_json(),
                    now,
                    prepare_package_lifecycle(package).purge_at,
                ),
            )
            connection.execute(
                "UPDATE content_drafts SET status='rejected_archived', updated_at=? "
                "WHERE request_id=?",
                (now, package.request_id),
            )
            connection.execute(
                "UPDATE agent_records SET status='rejected' WHERE request_id=?",
                (package.request_id,),
            )

    def enqueue_for_feed(self, package: ContentPackage) -> None:
        if package.content_type is None:
            raise ValueError("분류되지 않은 콘텐츠는 피드 대기열에 적재할 수 없습니다.")
        if package.content_type not in {item["code"] for item in self.active_content_types()}:
            raise ValueError(f"활성 콘텐츠 유형이 아닙니다: {package.content_type}")
        attach_menu_work_ids(package)
        if package.content_type != "event":
            package.event_country = None
        prepare_package_lifecycle(package)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO feed_queue (
                    request_id, topic, content_type, package_json, queued_at, status,
                    valid_until, purge_at, event_start, event_end, event_location,
                    event_country, event_region
                ) VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    topic=excluded.topic, content_type=excluded.content_type,
                    package_json=excluded.package_json,
                    queued_at=excluded.queued_at, status='ready',
                    valid_until=excluded.valid_until, purge_at=excluded.purge_at,
                    event_start=excluded.event_start, event_end=excluded.event_end,
                    event_location=excluded.event_location,
                    event_country=excluded.event_country, event_region=excluded.event_region
                """,
                (
                    package.request_id,
                    package.topic,
                    package.content_type,
                    package.model_dump_json(),
                    _utc_now().isoformat(),
                    package.feed_valid_until,
                    package.purge_at,
                    package.event_start,
                    package.event_end,
                    package.event_location,
                    package.event_country,
                    package.event_region,
                ),
            )
            connection.execute(
                "UPDATE content_drafts SET status='ready_for_feed', updated_at=? "
                "WHERE request_id=?",
                (_utc_now().isoformat(), package.request_id),
            )
            self._replace_work_links(connection, package)
            for card in package.cards:
                connection.execute(
                    """
                    INSERT INTO content_cards (
                        request_id, page, role, eyebrow, title_lines_json, body_json,
                        fact_ids_json, image_id, visual_mode, image_explanation, layout_json,
                        rendered_image_key, overlay_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(request_id, page) DO UPDATE SET
                        role=excluded.role, eyebrow=excluded.eyebrow,
                        title_lines_json=excluded.title_lines_json,
                        body_json=excluded.body_json, fact_ids_json=excluded.fact_ids_json,
                        image_id=excluded.image_id,
                        visual_mode=excluded.visual_mode,
                        image_explanation=excluded.image_explanation,
                        layout_json=excluded.layout_json,
                        rendered_image_key=excluded.rendered_image_key,
                        overlay_json=excluded.overlay_json
                    """,
                    (
                        package.request_id,
                        card.page,
                        card.role,
                        card.eyebrow,
                        json.dumps(card.title_lines, ensure_ascii=False),
                        json.dumps(card.body_sentences, ensure_ascii=False),
                        json.dumps(card.fact_ids, ensure_ascii=False),
                        card.image_id,
                        card.visual_mode,
                        card.image_explanation,
                        card.layout.model_dump_json(),
                        card.rendered_image_key,
                        _card_overlay_json(card),
                    ),
                )

    @staticmethod
    def _replace_work_links(connection, package: ContentPackage) -> None:
        """Persist canonical work links in representative-first order."""
        connection.execute(
            "DELETE FROM content_work_links WHERE request_id=?", (package.request_id,)
        )
        for sort_order, menu_work_id in enumerate(package.menu_work_ids, start=1):
            connection.execute(
                "INSERT INTO content_work_links "
                "(request_id, menu_work_id, sort_order, is_primary) VALUES (?, ?, ?, ?)",
                (package.request_id, menu_work_id, sort_order, int(sort_order == 1)),
            )

    def active_feed_items(self, now: datetime | None = None) -> list[dict[str, Any]]:
        cutoff = (now or _utc_now()).astimezone(UTC).isoformat()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT request_id, topic, content_type, package_json, queued_at, status "
                "FROM feed_queue WHERE status='ready' AND content_type IS NOT NULL "
                "AND (valid_until IS NULL OR valid_until > ?) "
                "ORDER BY queued_at DESC",
                (cutoff,),
            ).fetchall()
        hidden_package_fields = {
            "audience_region",
            "region_policy",
            "event_location",
            "event_region",
            "feed_valid_until",
            "purge_at",
            "retention_expires_at",
        }
        result = []
        for row in rows:
            package = json.loads(row["package_json"])
            if row["content_type"] != "event":
                package.pop("event_country", None)
            for field in hidden_package_fields:
                package.pop(field, None)
            detail_package = ContentPackage.model_validate(package)
            detail_markdown = render_detail_markdown(detail_package)
            article_body = package.get("detail_body", "")
            # The feed payload's display field is complete; retain the clean prose
            # separately for editors that need to re-render the article section.
            package["detail_body"] = detail_markdown
            package["detail_markdown"] = detail_markdown
            # Frontend detail view should use reader_text/markdown so source credits
            # and the AI disclosure are always shown after the article body.
            package["detail_article"] = {
                "body": detail_markdown,
                "article_body": article_body,
                "fact_ids": package.get("detail_fact_ids", []),
                "source_ids": package.get("detail_source_ids", []),
                "reader_text": detail_markdown,
                "markdown": detail_markdown,
            }
            result.append(dict(row) | {"package": package})
        return result

    def cleanup_expired(self, now: datetime | None = None) -> int:
        cutoff = (now or _utc_now()).astimezone(UTC).isoformat()
        expired_assets: list[tuple[str, str, dict[str, Any]]] = []
        with self.connect() as connection:
            connection.execute(
                "UPDATE feed_queue SET status='expired' "
                "WHERE status='ready' AND valid_until IS NOT NULL AND valid_until <= ?",
                (cutoff,),
            )
            expired_requests = {
                row["request_id"]
                for row in connection.execute(
                    "SELECT request_id FROM content_drafts WHERE purge_at <= ? "
                    "UNION SELECT request_id FROM feed_queue WHERE purge_at <= ? "
                    "UNION SELECT request_id FROM rejected_archive WHERE purge_at <= ?",
                    (cutoff, cutoff, cutoff),
                ).fetchall()
            }
            cards_removed = 0
            for request_id in expired_requests:
                cards_removed += connection.execute(
                    "DELETE FROM content_cards WHERE request_id=?", (request_id,)
                ).rowcount
            drafts_removed = connection.execute(
                "DELETE FROM content_drafts WHERE purge_at <= ?", (cutoff,)
            ).rowcount
            feed_removed = connection.execute(
                "DELETE FROM feed_queue WHERE purge_at <= ?", (cutoff,)
            ).rowcount
            rejected_removed = connection.execute(
                "DELETE FROM rejected_archive WHERE purge_at <= ?", (cutoff,)
            ).rowcount
            rows = connection.execute(
                "SELECT request_id, record_id, payload_json FROM agent_records "
                "WHERE agent_type='image' AND expires_at IS NOT NULL AND expires_at <= ?",
                (cutoff,),
            ).fetchall()
            expired_assets = [
                (row["request_id"], row["record_id"], json.loads(row["payload_json"]))
                for row in rows
            ]
            cursor = connection.execute(
                "DELETE FROM agent_records WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (cutoff,),
            )
            removed = (
                cursor.rowcount + cards_removed + drafts_removed + feed_removed + rejected_removed
            )
            candidate_keys = {
                row["sha256"]
                for row in connection.execute("SELECT sha256 FROM image_blobs").fetchall()
            }
            referenced_keys: set[str] = set()
            image_rows = connection.execute(
                "SELECT payload_json FROM agent_records WHERE agent_type='image'"
            ).fetchall()
            for row in image_rows:
                key = json.loads(row["payload_json"]).get("storage_key")
                if key:
                    referenced_keys.add(key)
            for table in ("content_drafts", "feed_queue", "rejected_archive"):
                for row in connection.execute(f"SELECT package_json FROM {table}").fetchall():
                    package_payload = json.loads(row["package_json"])
                    referenced_keys.update(
                        image.get("storage_key")
                        for image in package_payload.get("images", [])
                        if image.get("storage_key")
                    )
                    referenced_keys.update(
                        card.get("rendered_image_key")
                        for card in package_payload.get("cards", [])
                        if card.get("rendered_image_key")
                    )
            for storage_key in candidate_keys - referenced_keys:
                removed += connection.execute(
                    "DELETE FROM image_blobs WHERE sha256=?", (storage_key,)
                ).rowcount
            for request_id, record_id, payload in expired_assets:
                image_id = record_id.split(":", 2)[-1]
                referenced = connection.execute(
                    "SELECT 1 FROM content_cards WHERE request_id=? AND image_id=? LIMIT 1",
                    (request_id, image_id),
                ).fetchone()
                still_collected = connection.execute(
                    "SELECT 1 FROM agent_records WHERE request_id=? AND record_id=? LIMIT 1",
                    (request_id, record_id),
                ).fetchone()
                local_path = payload.get("local_path", "")
                if referenced or still_collected or not local_path:
                    continue
                managed_root = (Path.cwd() / "data" / "assets").resolve()
                asset_path = (Path.cwd() / local_path).resolve()
                if asset_path.is_relative_to(managed_root) and asset_path.is_file():
                    asset_path.unlink()
            return removed

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            records = connection.execute(
                "SELECT agent_type, COUNT(*) AS count FROM agent_records GROUP BY agent_type"
            ).fetchall()
            rejected = connection.execute(
                "SELECT COUNT(*) AS count FROM rejected_archive"
            ).fetchone()
            feed = connection.execute("SELECT COUNT(*) AS count FROM feed_queue").fetchone()
            cards = connection.execute("SELECT COUNT(*) AS count FROM content_cards").fetchone()
            drafts = connection.execute("SELECT COUNT(*) AS count FROM content_drafts").fetchone()
        result = {row["agent_type"]: int(row["count"]) for row in records}
        result["rejected"] = int(rejected["count"])
        result["feed_queue"] = int(feed["count"])
        result["content_cards"] = int(cards["count"])
        result["content_drafts"] = int(drafts["count"])
        return result
