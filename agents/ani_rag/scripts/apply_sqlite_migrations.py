"""Apply the checked-in, versioned SQLite migrations to an existing ani_rag DB.

This is an explicit operator command. Workers never migrate databases on startup.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
BASE_TABLES = frozenset({
    "rag_source", "rag_document", "rag_entity", "rag_work_catalog",
    "rag_chunk", "rag_image_asset",
})


def apply_migrations(database: Path, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply unapplied migrations and return their version names.

    A checksum mismatch stops the run instead of silently accepting an edited
    migration. Each migration and its history row commit in one transaction.
    """
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"ani_rag SQLite database does not exist: {database}")

    connection = sqlite3.connect(database, timeout=60)
    applied_now: list[str] = []
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing_base = BASE_TABLES - tables
        if missing_base:
            raise RuntimeError(
                "Database is missing base ani_rag tables: " + ", ".join(sorted(missing_base))
            )

        connection.execute(
            """CREATE TABLE IF NOT EXISTS ani_rag_schema_migration (
                version TEXT PRIMARY KEY,
                checksum_sha256 TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )"""
        )
        migration_files = sorted(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        for migration in migration_files:
            sql = migration.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            existing = connection.execute(
                "SELECT checksum_sha256 FROM ani_rag_schema_migration WHERE version=?",
                (migration.name,),
            ).fetchone()
            if existing:
                if existing[0] != checksum:
                    raise RuntimeError(
                        f"Applied migration was edited; refusing to continue: {migration.name}"
                    )
                continue

            applied_at = datetime.now(timezone.utc).isoformat()
            script = (
                "BEGIN IMMEDIATE;\n"
                + sql
                + "\nINSERT INTO ani_rag_schema_migration(version,checksum_sha256,applied_at) "
                + f"VALUES ({_sql_literal(migration.name)}, {_sql_literal(checksum)}, "
                + f"{_sql_literal(applied_at)});\nCOMMIT;"
            )
            try:
                connection.executescript(script)
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
            applied_now.append(migration.name)

        schema_sql = (PROJECT_ROOT / "src" / "ani_rag" / "storage" / "schema.sql").read_text(
            encoding="utf-8"
        )
        required_tables = {
            line.split()[5].split("(")[0]
            for line in schema_sql.splitlines()
            if line.startswith("CREATE TABLE IF NOT EXISTS ")
        }
        actual_tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = required_tables - actual_tables
        if missing:
            raise RuntimeError(
                "Migrations did not create the complete worker schema: "
                + ", ".join(sorted(missing))
            )
        return applied_now
    finally:
        connection.close()


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Existing ani_rag SQLite database")
    args = parser.parse_args()
    applied = apply_migrations(args.db)
    print(f"Applied {len(applied)} migration(s): {', '.join(applied) if applied else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
