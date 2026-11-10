"""Import approved menu_dict snapshot facts into an existing ani_rag SQLite DB.

This command never creates a database or applies schema. Run it only while the
RAG workers are stopped; the same live SQLite file must not be imported while
the independent workers are writing to it.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from ani_rag.menu_dict_archive import MenuDictArchiveReader
from ani_rag.storage.menu_dict_import import SQLiteMenuDictSnapshotImporter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path, help="Path to the PostgreSQL custom-format menu_dict.dump")
    parser.add_argument("--rag-db", required=True, type=Path, help="Existing ani_rag SQLite DB with rag_* schema applied")
    parser.add_argument("--confirm-import", action="store_true", help="Required to write imported reference evidence")
    args = parser.parse_args()
    if not args.confirm_import:
        parser.error("Pass --confirm-import to write to the selected RAG database")
    archive = args.archive.resolve()
    database = args.rag_db.resolve()
    project_root = Path(__file__).resolve().parents[1]
    data_root = (project_root / "data").resolve()
    if data_root not in database.parents or database.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
        parser.error("--rag-db must be an existing SQLite file inside ani_rag/data")
    if not database.is_file():
        parser.error("--rag-db must already exist; this command never creates a database")
    if archive == database:
        parser.error("The source archive cannot also be the RAG database")

    reader = MenuDictArchiveReader(archive)
    connection = sqlite3.connect(str(database), check_same_thread=False)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        importer = SQLiteMenuDictSnapshotImporter(connection)
        sources_added = importer.register_sources(reader)
        summary = importer.import_documents(reader)
    finally:
        connection.close()
    print(
        f"snapshot={reader.archive_sha256} sources_added={sources_added} "
        f"eligible={summary.eligible_seen} inserted={summary.inserted} already_present={summary.already_present}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
