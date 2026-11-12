"""Repair imported AniList entry/work links from the local menu_dict crosswalk.

Default mode is read-only. --apply writes only ani_rag's existing SQLite database
and first creates a compressed, row-level backup of links for affected documents.
It never touches the period-agent database or applies schema changes.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ani_rag_live_20261109.sqlite3"
DEFAULT_ENTITY_CROSSWALK = ROOT / "data" / "reference" / "menu_dict_20261109" / "entity.csv"
DEFAULT_WORK_CATALOG = ROOT / "data" / "reference" / "menu_works.csv"
DEFAULT_BACKUP = ROOT / "data" / "recovery" / "menu_dict_work_links_before_parent_repair_20261111.csv.gz"
KINDS = {"entry", "work"}


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection


def _mapping(entity_path: Path, work_path: Path) -> tuple[dict[int, tuple[int, str]], set[int]]:
    direct: dict[int, tuple[int, str]] = {}
    with entity_path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            kind = row.get("kind")
            if kind == "work" and row.get("menu_work_id"):
                direct[int(row["entity_id"])] = (int(row["menu_work_id"]), "menu_dict_work_identity")
            elif kind == "entry" and row.get("parent_id"):
                direct[int(row["entity_id"])] = (int(row["parent_id"]), "menu_dict_entry_parent")
    with work_path.open("r", encoding="utf-8-sig", newline="") as stream:
        local_work_ids = {int(row["menu_work_id"]) for row in csv.DictReader(stream)}
    return direct, local_work_ids


def _populate_map(
    connection: sqlite3.Connection,
    direct: dict[int, tuple[int, str]],
    local_work_ids: set[int],
) -> dict[str, int]:
    connection.execute("CREATE TEMP TABLE repair_map(document_id TEXT PRIMARY KEY, menu_work_id INTEGER NOT NULL, matched_on TEXT NOT NULL)")
    missing_crosswalk = absent_from_catalog = 0
    rows = []
    for row in connection.execute(
        """SELECT document_id,metadata_json FROM rag_document
           WHERE json_extract(metadata_json,'$.dataset')='menu_dict'
             AND json_extract(metadata_json,'$.menu_dict_entity_kind') IN ('entry','work')"""
    ):
        metadata = json.loads(str(row["metadata_json"]))
        entity_id = int(metadata["menu_dict_entity_id"])
        mapping = direct.get(entity_id)
        if mapping is None:
            missing_crosswalk += 1
            continue
        menu_work_id, method = mapping
        if menu_work_id not in local_work_ids:
            absent_from_catalog += 1
            continue
        rows.append((str(row["document_id"]), menu_work_id, method))
        if len(rows) >= 5000:
            connection.executemany("INSERT INTO repair_map VALUES(?,?,?)", rows)
            rows.clear()
    if rows:
        connection.executemany("INSERT INTO repair_map VALUES(?,?,?)", rows)
    map_count = int(connection.execute("SELECT COUNT(*) FROM repair_map").fetchone()[0])
    link_count, already_correct = connection.execute(
        """SELECT COALESCE(SUM(link_count),0),COALESCE(SUM(is_correct),0) FROM (
             SELECT m.document_id,COUNT(w.link_id) AS link_count,
                    CASE WHEN COUNT(w.link_id)=1 AND SUM(CASE WHEN
                         w.menu_work_id=m.menu_work_id AND w.candidate_menu_work_id IS NULL
                         AND w.status='matched' THEN 1 ELSE 0 END)=1 THEN 1 ELSE 0 END AS is_correct
             FROM repair_map m LEFT JOIN rag_work_link w ON w.document_id=m.document_id
             GROUP BY m.document_id
           )"""
    ).fetchone()
    return {
        "documents": map_count,
        "current_link_rows": int(link_count),
        "documents_already_correct": already_correct,
        "documents_to_repair": map_count - already_correct,
        "extra_or_misassigned_link_rows": link_count - already_correct,
        "missing_crosswalk": missing_crosswalk,
        "parent_not_in_local_work_snapshot": absent_from_catalog,
    }


def _backup_links(connection: sqlite3.Connection, path: Path, expected_rows: int) -> tuple[int, str]:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing backup: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with path.open("xb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            import io

            text = io.TextIOWrapper(zipped, encoding="utf-8", newline="", write_through=True)
            writer = csv.writer(text)
            writer.writerow(("link_id", "document_id", "menu_work_id", "candidate_menu_work_id", "status", "matched_on"))
            cursor = connection.execute(
                """SELECT w.link_id,w.document_id,w.menu_work_id,w.candidate_menu_work_id,w.status,w.matched_on
                   FROM rag_work_link w JOIN repair_map m ON m.document_id=w.document_id"""
            )
            for row in cursor:
                writer.writerow(tuple(row))
                count += 1
            text.flush()
            text.detach()
        if count != expected_rows:
            raise RuntimeError(f"Backup row count mismatch: expected {expected_rows}, got {count}")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return count, digest.hexdigest()


def _apply(connection: sqlite3.Connection, backup_path: Path) -> tuple[int, str]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        before = _backup_links(connection, backup_path, int(connection.execute(
            "SELECT COUNT(*) FROM rag_work_link w JOIN repair_map m ON m.document_id=w.document_id"
        ).fetchone()[0]))
        connection.execute("DELETE FROM rag_work_link WHERE document_id IN (SELECT document_id FROM repair_map)")
        connection.execute(
            """INSERT INTO rag_work_link(link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on)
               SELECT 'wl_parent_'||document_id,document_id,menu_work_id,NULL,'matched',matched_on FROM repair_map"""
        )
        actual = int(connection.execute(
            """SELECT COUNT(*) FROM repair_map m JOIN rag_work_link w ON w.document_id=m.document_id
               WHERE w.menu_work_id=m.menu_work_id AND w.candidate_menu_work_id IS NULL
                 AND w.status='matched' AND w.matched_on=m.matched_on"""
        ).fetchone()[0])
        total = int(connection.execute("SELECT COUNT(*) FROM repair_map").fetchone()[0])
        if actual != total:
            raise RuntimeError(f"Post-repair verification failed: expected {total}, got {actual}")
        connection.commit()
        return before
    except BaseException:
        connection.rollback()
        backup_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--entity-crosswalk", type=Path, default=DEFAULT_ENTITY_CROSSWALK)
    parser.add_argument("--work-catalog", type=Path, default=DEFAULT_WORK_CATALOG)
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--apply", action="store_true", help="back up affected links and repair the RAG SQLite database")
    args = parser.parse_args()
    direct, local_work_ids = _mapping(args.entity_crosswalk, args.work_catalog)
    connection = _read_only(args.database) if not args.apply else sqlite3.connect(args.database, timeout=120)
    connection.row_factory = sqlite3.Row
    try:
        plan = _populate_map(connection, direct, local_work_ids)
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        if plan["missing_crosswalk"] or plan["parent_not_in_local_work_snapshot"]:
            raise RuntimeError("Crosswalk coverage is incomplete; refusing repair")
        if not args.apply:
            print("dry_run=true; main database was not modified")
            return 0
        if plan["documents_to_repair"] == 0:
            print("nothing_to_repair=true")
            return 0
        # Commit only the connection-local TEMP mapping before beginning the
        # explicit transaction against the read-write main database.
        connection.commit()
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=120000")
        backup_rows, backup_sha256 = _apply(connection, args.backup)
        print(f"backup={args.backup}")
        print(f"backup_rows={backup_rows}")
        print(f"backup_sha256={backup_sha256}")
        print("repair_committed=true")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
