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
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ani_rag.archive_identity import archive_work_links, official_works_path
from ani_rag.menu_dict_archive import DEFAULT_POLICY_PATH

DEFAULT_ENTITY_CROSSWALK = ROOT / "data" / "reference" / "menu_dict_20261109" / "entity.csv"


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection


def _mapping(entity_path: Path, work_path: Path, external_path: Path | None = None,
             work_entries_path: Path | None = None) -> tuple[dict[int, tuple[int, str]], set[int]]:
    works = {}
    entries = {}
    with entity_path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            kind = row.get("kind")
            if kind == "work" and row.get("menu_work_id"):
                entity_id, work_id = int(row["entity_id"]), int(row["menu_work_id"])
                titles = tuple(row.get(key) or "" for key in ("name_ko", "name_native", "name_romaji"))
                works[entity_id] = (work_id, titles)
            elif kind == "entry" and row.get("parent_id"):
                entries[int(row["entity_id"])] = int(row["parent_id"])
    with (external_path or entity_path.with_name("external_id.csv")).open(encoding="utf-8-sig", newline="") as stream:
        external = ((int(row["entity_id"]), row["external_id"]) for row in csv.DictReader(stream)
                    if row["source_id"] == "anilist" and row["id_kind"] == "anime")
        return archive_work_links(works, entries, external, work_path,
                                  work_entries_path or work_path.with_name("menu_work_entries.csv"))


def _populate_map(
    connection: sqlite3.Connection,
    direct: dict[int, tuple[int, str]],
    local_work_ids: set[int],
    archive_sha256: str | None = None,
) -> dict[str, int]:
    connection.execute("DROP TABLE IF EXISTS temp.repair_changes")
    connection.execute("DROP TABLE IF EXISTS temp.repair_map")
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
        if (mapping is None or (mapping[1] == "menu_dict_work_identity") != (metadata["menu_dict_entity_kind"] == "work")
                or (archive_sha256 is not None and metadata.get("archive_sha256") != archive_sha256)):
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
    connection.execute(
        """CREATE TEMP TABLE repair_changes AS
           SELECT m.*, COUNT(w.link_id) AS old_count,
             COALESCE(MIN(CASE WHEN w.menu_work_id=m.menu_work_id AND w.candidate_menu_work_id IS NULL
                 AND w.status='matched' THEN w.link_id END),MIN(w.link_id)) AS keep_link_id
           FROM repair_map m LEFT JOIN rag_work_link w ON w.document_id=m.document_id
           GROUP BY m.document_id
           HAVING COUNT(w.link_id) != 1 OR MAX(CASE WHEN w.menu_work_id=m.menu_work_id
             AND w.candidate_menu_work_id IS NULL AND w.status='matched' THEN 1 ELSE 0 END) != 1"""
    )
    connection.execute("CREATE UNIQUE INDEX repair_changes_doc ON repair_changes(document_id)")
    changed, old_rows = connection.execute("SELECT COUNT(*), COALESCE(SUM(old_count),0) FROM repair_changes").fetchone()
    already_correct = map_count - changed
    return {
        "documents": map_count,
        "current_link_rows": int(old_rows + already_correct),
        "documents_already_correct": already_correct,
        "documents_to_repair": map_count - already_correct,
        "old_links_on_changed_documents": old_rows,
        "missing_crosswalk": missing_crosswalk,
        "parent_identity_unconfirmed": absent_from_catalog,
    }


def _backup_links(connection: sqlite3.Connection, path: Path, expected_rows: int) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    raw = path.open("xb")
    try:
        with raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            import io

            text = io.TextIOWrapper(zipped, encoding="utf-8", newline="", write_through=True)
            writer = csv.writer(text)
            writer.writerow(("link_id", "document_id", "menu_work_id", "candidate_menu_work_id", "status", "matched_on"))
            cursor = connection.execute(
                """SELECT w.link_id,m.document_id,w.menu_work_id,w.candidate_menu_work_id,w.status,w.matched_on
                   FROM repair_changes m LEFT JOIN rag_work_link w ON m.document_id=w.document_id"""
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
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        before = _backup_links(connection, backup_path, int(connection.execute(
            "SELECT COALESCE(SUM(MAX(1,old_count)),0) FROM repair_changes"
        ).fetchone()[0]))
        connection.execute(
            """DELETE FROM rag_work_link WHERE link_id IN (
               SELECT w.link_id FROM repair_changes c JOIN rag_work_link w ON w.document_id=c.document_id
               WHERE c.old_count > 1 AND w.link_id != c.keep_link_id)"""
        )
        connection.execute(
            """UPDATE rag_work_link AS w SET menu_work_id=c.menu_work_id, candidate_menu_work_id=NULL,
                 status='matched', matched_on=c.matched_on
               FROM repair_changes AS c WHERE w.link_id=c.keep_link_id AND
               (w.menu_work_id IS NOT c.menu_work_id OR w.candidate_menu_work_id IS NOT NULL OR w.status!='matched')"""
        )
        connection.execute(
            """INSERT INTO rag_work_link(link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on)
               SELECT 'wl_parent_'||document_id,document_id,menu_work_id,NULL,'matched',matched_on
               FROM repair_changes WHERE old_count=0"""
        )
        invalid = int(connection.execute(
            """SELECT COUNT(*) FROM (
               SELECT m.document_id FROM repair_changes m LEFT JOIN rag_work_link w ON w.document_id=m.document_id
               GROUP BY m.document_id HAVING COUNT(w.link_id)!=1 OR MAX(CASE WHEN
               w.menu_work_id=m.menu_work_id AND w.candidate_menu_work_id IS NULL AND w.status='matched'
               THEN 1 ELSE 0 END)!=1)"""
        ).fetchone()[0])
        if invalid:
            raise RuntimeError(f"Post-repair verification failed for {invalid} documents")
        connection.commit()
        return before
    except BaseException:
        connection.rollback()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--entity-crosswalk", type=Path, default=DEFAULT_ENTITY_CROSSWALK)
    parser.add_argument("--work-catalog", type=Path, default=official_works_path())
    parser.add_argument("--backup", type=Path, help="New gzip CSV file for previous links (required with --apply)")
    parser.add_argument("--apply", action="store_true", help="back up affected links and repair the RAG SQLite database")
    args = parser.parse_args()
    if args.apply and args.backup is None:
        parser.error("--apply requires --backup pointing to a new file")
    direct, local_work_ids = _mapping(args.entity_crosswalk, args.work_catalog)
    connection = _read_only(args.database) if not args.apply else sqlite3.connect(
        args.database.resolve().as_uri() + "?mode=rw", uri=True, timeout=120
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("BEGIN IMMEDIATE" if args.apply else "BEGIN")
        expected_hash = json.loads(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))["snapshot_sha256"]
        plan = _populate_map(connection, direct, local_work_ids, expected_hash)
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        if plan["missing_crosswalk"] or plan["parent_identity_unconfirmed"]:
            raise RuntimeError("Crosswalk coverage is incomplete; refusing repair")
        if not args.apply:
            print("dry_run=true; main database was not modified")
            return 0
        if plan["documents_to_repair"] == 0:
            print("nothing_to_repair=true")
            return 0
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
