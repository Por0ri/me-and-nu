from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.apply_sqlite_migrations import apply_migrations


def test_sqlite_migrations_fill_worker_schema_and_are_repeatable(tmp_path: Path) -> None:
    database = tmp_path / "ani_rag.sqlite3"
    project_root = Path(__file__).resolve().parents[1]
    migrations = project_root / "migrations"
    migration_tables = {
        "rag_source_read_ledger", "rag_web_candidate_page_cache",
        "rag_web_candidate_url_ledger", "rag_web_discovery_plan",
        "rag_document_content_type", "rag_document_entity_link",
        "rag_entity_fact", "rag_entity_work_link", "rag_image_entity_link", "rag_card_image_component",
    }
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    script = (project_root / "src" / "ani_rag" / "storage" / "schema.sql").read_text(
        encoding="utf-8"
    )
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if "CREATE TABLE IF NOT EXISTS" in statement:
                table_name = statement.split("CREATE TABLE IF NOT EXISTS", 1)[1].split("(", 1)[0].strip()
                if table_name not in migration_tables:
                    connection.executescript(statement)
            statement = ""
    connection.close()

    applied = apply_migrations(database, migrations)
    assert applied == [
        "0002_source_read_ledger.sql",
        "0003_web_candidate_page_cache.sql",
        "0004_web_candidate_url_ledger.sql",
        "0005_recurring_web_discovery_plan.sql",
        "0006_entity_evidence_links.sql",
        "0007_card_image_components.sql",
    ]

    connection = sqlite3.connect(database)
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "rag_source_read_ledger", "rag_web_candidate_page_cache",
        "rag_web_candidate_url_ledger", "rag_web_discovery_plan",
        "rag_document_content_type", "rag_document_entity_link",
        "rag_entity_fact", "rag_entity_work_link", "rag_image_entity_link",
        "ani_rag_schema_migration", "rag_card_image_component",
    } <= tables
    connection.execute("SELECT card_id, position, asset_id FROM rag_card_image_source LIMIT 1")
    connection.close()

    assert apply_migrations(database, migrations) == []
