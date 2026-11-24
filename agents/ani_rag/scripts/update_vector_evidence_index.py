"""Add only newly collected reference-fact documents to the existing vector sidecar.

The source RAG SQLite file is attached read-only. The sidecar is checkpointed
after each committed chunk batch; reruns skip saved chunk IDs and reuse vectors
by exact text hash.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from openai import OpenAI

from build_vector_evidence_index import (
    API_BATCH_SIZE,
    DEFAULT_INDEX_DB,
    DEFAULT_SOURCE_DB,
    MODEL,
    _flush_batch,
    _iter_evidence_chunks,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--index-db", type=Path, default=DEFAULT_INDEX_DB)
    parser.add_argument(
        "--repair-raw-id", type=int, action="append", metavar="ID",
        help="Recheck this already checkpointed document and save only missing chunks. Repeatable.",
    )
    args = parser.parse_args()
    source_db = args.source_db.resolve()
    index_db = args.index_db.resolve()
    if not source_db.is_file() or not index_db.is_file():
        raise FileNotFoundError("Both the source RAG DB and completed vector sidecar must exist")

    source = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True, timeout=60)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    index = sqlite3.connect(index_db, timeout=60)
    index.row_factory = sqlite3.Row
    try:
        metadata = dict(index.execute("SELECT key,value FROM index_metadata"))
        if metadata.get("completed_at") is None:
            raise RuntimeError("The initial full vector index has not completed")
        if Path(metadata.get("source_database", "")).resolve() != source_db:
            raise ValueError("Source DB path differs from the vector index provenance")
        if metadata.get("model") != MODEL or metadata.get("dimensions") != "1024":
            raise ValueError("Vector model or dimensions differ from the existing index")
        if metadata.get("max_chars") != "1200" or metadata.get("overlap") != "120":
            raise ValueError("Chunking rules differ from the existing index")

        # Older sidecars may contain partially indexed documents. Saved chunks
        # are free to revisit, so resume from zero without a recorded boundary.
        checkpoint_id = int(metadata.get("last_source_raw_document_id", "0"))

        secrets = dotenv_values(ROOT / ".env")
        api_key = secrets.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is unavailable; the key value is never printed")
        client = OpenAI(api_key=api_key, timeout=180.0, max_retries=6)
        totals: dict[str, int | float] = {
            "chunks_saved": 0, "batches_saved": 0, "provider_tokens": 0, "estimated_usd": 0.0,
        }
        pending: list[dict[str, object]] = []
        started = time.monotonic()
        # The crawler may append documents while the embedding request is in flight.
        # Only advance through the range this pass actually scanned.
        source_high_water = int(source.execute(
            "SELECT COALESCE(MAX(raw_document_id),0) FROM rag_document WHERE evidence_type='reference_fact'"
        ).fetchone()[0])
        if source_high_water < checkpoint_id:
            raise RuntimeError("Vector checkpoint exceeds the last source reference; verify database provenance")
        repair_ids = sorted(set(args.repair_raw_id or ()))
        intervals = [(checkpoint_id, source_high_water)]
        if repair_ids:
            for raw_id in repair_ids:
                if not 1 <= raw_id <= checkpoint_id or not source.execute(
                    "SELECT 1 FROM rag_document WHERE raw_document_id=? AND evidence_type='reference_fact'",
                    (raw_id,),
                ).fetchone():
                    raise ValueError(f"Expected a checkpointed reference-fact document, got {raw_id}")
            intervals = [(raw_id - 1, raw_id) for raw_id in repair_ids]
        for lower, upper in intervals:
            for chunk in _iter_evidence_chunks(
                source, after_raw_document_id=lower, through_raw_document_id=upper,
            ):
                pending.append(chunk)
                if len(pending) >= API_BATCH_SIZE:
                    _flush_batch(index, client, pending, totals, started=started)
        _flush_batch(index, client, pending, totals, started=started)

        now = datetime.now(timezone.utc).isoformat()
        if not repair_ids:
            index.execute(
                "INSERT OR REPLACE INTO index_metadata(key,value) VALUES('last_source_raw_document_id',?)",
                (str(source_high_water),),
            )
        index.execute(
            "INSERT OR REPLACE INTO index_metadata(key,value) VALUES('source_size_bytes',?)",
            (str(source_db.stat().st_size),),
        )
        index.execute(
            "INSERT OR REPLACE INTO index_metadata(key,value) VALUES('incremental_updated_at',?)", (now,),
        )
        index.commit()
        counts = index.execute("SELECT count(*),count(DISTINCT text_hash) FROM evidence_chunk").fetchone()
        print(json.dumps({
            "status": "complete",
            "index_db": str(index_db),
            "source_db_modified": False,
            "checkpoint_before": checkpoint_id,
            "checkpoint_after": checkpoint_id if repair_ids else source_high_water,
            "repair_raw_ids": repair_ids,
            "index_chunk_count": counts[0],
            "unique_vectors": counts[1],
            "chunks_added_this_run": int(totals["chunks_saved"]),
            "provider_tokens_this_run": int(totals["provider_tokens"]),
            "estimated_cost_usd_this_run": round(float(totals["estimated_usd"]), 6),
            "elapsed_minutes": round((time.monotonic() - started) / 60, 1),
        }, ensure_ascii=False), flush=True)
    except Exception as error:
        index.execute(
            "INSERT OR REPLACE INTO index_metadata(key,value) VALUES('incremental_last_error_type',?)",
            (type(error).__name__,),
        )
        index.execute(
            "INSERT OR REPLACE INTO index_metadata(key,value) VALUES('incremental_last_error_at',?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        index.commit()
        raise
    finally:
        source.close()
        index.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
