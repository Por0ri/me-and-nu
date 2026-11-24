"""Reconcile character/person/studio/song documents with the verified snapshot's entry relations."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from itertools import groupby
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ani_rag.menu_dict_archive import MenuDictArchiveReader

if __package__:
    from .repair_menu_dict_work_links import _backup_links
else:
    from repair_menu_dict_work_links import _backup_links


def _plan(connection: sqlite3.Connection, reader: MenuDictArchiveReader) -> dict[str, int]:
    connection.execute("CREATE TEMP TABLE derived_docs(document_id TEXT PRIMARY KEY, entity_id INTEGER NOT NULL) WITHOUT ROWID")
    connection.execute("CREATE TEMP TABLE repair_changes(document_id TEXT PRIMARY KEY) WITHOUT ROWID")
    connection.execute("CREATE TEMP TABLE desired(document_id TEXT, ordinal INTEGER, work_id INTEGER, PRIMARY KEY(document_id,ordinal)) WITHOUT ROWID")
    for document_id, entity_id, kind, archive_sha in connection.execute(
        """SELECT document_id, json_extract(metadata_json,'$.menu_dict_entity_id'),
                  json_extract(metadata_json,'$.menu_dict_entity_kind'),json_extract(metadata_json,'$.archive_sha256')
           FROM rag_document WHERE json_extract(metadata_json,'$.dataset')='menu_dict'
             AND json_extract(metadata_json,'$.menu_dict_entity_kind') NOT IN ('work','entry')"""
    ):
        entity = reader.entities.get(int(entity_id))
        if archive_sha != reader.archive_sha256 or entity is None or entity.kind != kind:
            raise ValueError(f"Cannot verify source entity and snapshot for {document_id}")
        connection.execute("INSERT INTO derived_docs VALUES(?,?)", (document_id, entity.entity_id))
    links = connection.execute(
        """SELECT d.document_id, d.entity_id, w.link_id,w.menu_work_id,w.candidate_menu_work_id,w.status,w.matched_on
           FROM derived_docs d LEFT JOIN rag_work_link w ON w.document_id=d.document_id ORDER BY d.document_id"""
    )
    documents = changed = old_rows = new_rows = backup_rows = 0
    for document_id, rows in groupby(links, key=lambda row: row[0]):
        records = list(rows)
        expected = reader.derived_work_ids(records[0][1])
        current = [row for row in records if row[2] is not None]
        required = [(work_id, None, "matched") for work_id in expected] or [(None, None, "unresolved")]
        documents += 1
        if len(current) == len(required) and {tuple(row[3:6]) for row in current} == set(required):
            continue
        if any(row[6] and not row[6].startswith("menu_dict_") for row in current):
            raise ValueError(f"Unexpected curated link on archive document {document_id}")
        changed += 1
        old_rows += len(current)
        backup_rows += max(1, len(current))
        new_rows += len(required)
        connection.execute("INSERT INTO repair_changes VALUES(?)", (document_id,))
        connection.executemany("INSERT INTO desired VALUES(?,?,?)", (
            (document_id, ordinal, link[0]) for ordinal, link in enumerate(required)
        ))
    return dict(documents=documents, documents_to_repair=changed, old_rows=old_rows,
                new_rows=new_rows, backup_rows=backup_rows)


def _apply(connection: sqlite3.Connection, backup: Path, plan: dict[str, int]) -> None:
    if not connection.in_transaction:
        raise RuntimeError("Reconcile within the same write transaction as the audit")
    _backup_links(connection, backup, plan["backup_rows"])
    connection.execute("DELETE FROM rag_work_link WHERE document_id IN (SELECT document_id FROM repair_changes)")
    connection.execute(
        """INSERT INTO rag_work_link(link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on)
           SELECT 'wl_dict_relation_'||document_id||'_'||ordinal, document_id, work_id, NULL,
                  CASE WHEN work_id IS NULL THEN 'unresolved' ELSE 'matched' END,
                  CASE WHEN work_id IS NOT NULL THEN 'menu_dict_explicit_entity_relation' END
           FROM desired"""
    )
    missing = connection.execute(
        """SELECT document_id,work_id FROM desired EXCEPT
           SELECT w.document_id,w.menu_work_id FROM rag_work_link w JOIN repair_changes r USING(document_id)
           WHERE w.candidate_menu_work_id IS NULL AND
               ((w.status='matched' AND w.menu_work_id IS NOT NULL) OR (w.status='unresolved' AND w.menu_work_id IS NULL))
           LIMIT 1"""
    ).fetchone()
    actual = connection.execute("SELECT count(*) FROM rag_work_link w JOIN repair_changes r USING(document_id)").fetchone()[0]
    if missing or actual != plan["new_rows"]:
        raise RuntimeError("Derived document work links failed post-write verification")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path, help="New gzip CSV for replaced link rows")
    args = parser.parse_args()
    if args.apply and args.backup is None:
        parser.error("--apply requires --backup")
    if args.apply and args.backup.exists():
        raise FileExistsError(args.backup)
    reader = MenuDictArchiveReader(args.archive)
    path = args.database.resolve().as_uri() + ("?mode=rw" if args.apply else "?mode=ro")
    with sqlite3.connect(path, uri=True, timeout=120) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE" if args.apply else "BEGIN")
        plan = _plan(connection, reader)
        print(json.dumps(plan, sort_keys=True), flush=True)
        if args.apply and plan["documents_to_repair"]:
            _apply(connection, args.backup, plan)
    print("verified=true", flush=True)


if __name__ == "__main__":
    main()
