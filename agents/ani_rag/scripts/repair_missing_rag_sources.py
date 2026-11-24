"""Restore missing parent rows for existing RAG source reads and images, offline."""

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ani_rag.settings import load_source_registry
from ani_rag.storage.sqlite_core import SQLiteRagStore


async def reconcile(db, registry, *, apply=False):
    store = SQLiteRagStore(db)
    missing = {
        row[0] for row in db.execute(
            """SELECT source_id FROM (
                 SELECT source_id FROM rag_source_read_ledger UNION SELECT source_id FROM rag_image_asset
               ) EXCEPT SELECT source_id FROM rag_source"""
        )
    }
    approved = {source.source_id: source for source in registry.approved_sources}
    targets = sorted(missing.intersection(approved))
    if apply:
        for key in targets:
            await store.register_source(approved[key], only_if_missing=True)
    return {"missing_approved": targets, "missing_unapproved": sorted(missing - set(targets))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    mode = "rw" if args.apply else "ro"
    db = sqlite3.connect(args.database.resolve().as_uri() + f"?mode={mode}", uri=True, check_same_thread=False)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        print(json.dumps(asyncio.run(reconcile(db, load_source_registry(), apply=args.apply)), sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
