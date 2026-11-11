"""PostgreSQL 적재, 이미지 BYTEA 보관, 행사 정보 만료, 탈락 보관을 담당합니다."""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import sqlite3
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image

from ani_rag.content.agents.content_generation.renderer import OUTPUT_IMAGE_SIZE
from ani_rag.content.config import get_settings
from ani_rag.content.models import (
    CONTENT_TYPE_LABELS,
    ContentPackage,
)
from ani_rag.content.policy import POLICY
from ani_rag.content.work_catalog import attach_menu_work_ids


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _card_overlay_json(card) -> str:
    """Make the page-to-image/text relationship explicit for frontend consumers."""
    return json.dumps(
        {
            "page": card.page,
            "menu_work_ids": card.menu_work_ids,
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
        # Extraction may find dates on product, news, guide, or interview pages.
        # Only an event package's event_end is an event expiry anchor.
        end_date = _as_date(package.event_end) if package.content_type == "event" else None
        if not end_date and package.content_type == "event":
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
    elif package.retention_expires_at and not (
        package.content_type == "event" and package.event_end
    ):
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
                    menu_work_ids_json TEXT NOT NULL DEFAULT '[]',
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
                    "menu_work_ids_json": "TEXT NOT NULL DEFAULT '[]'",
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

    def mark_request_status(self, request_id: str, status: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE agent_records SET status=?, updated_at=? WHERE request_id=?",
                (status, _utc_now().isoformat(), request_id),
            )
            return cursor.rowcount

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
                        fact_ids_json, menu_work_ids_json, image_id, visual_mode,
                        image_explanation, layout_json, rendered_image_key, overlay_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        package.request_id,
                        card.page,
                        card.role,
                        card.eyebrow,
                        json.dumps(card.title_lines, ensure_ascii=False),
                        json.dumps(card.body_sentences, ensure_ascii=False),
                        json.dumps(card.fact_ids, ensure_ascii=False),
                        json.dumps(card.menu_work_ids, ensure_ascii=False),
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
                        fact_ids_json, menu_work_ids_json, image_id, visual_mode,
                        image_explanation, layout_json, rendered_image_key, overlay_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(request_id, page) DO UPDATE SET
                        role=excluded.role, eyebrow=excluded.eyebrow,
                        title_lines_json=excluded.title_lines_json,
                        body_json=excluded.body_json, fact_ids_json=excluded.fact_ids_json,
                        menu_work_ids_json=excluded.menu_work_ids_json,
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
                        json.dumps(card.menu_work_ids, ensure_ascii=False),
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

