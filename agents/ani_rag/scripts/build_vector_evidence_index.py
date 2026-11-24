"""Build or resume the full evidence embedding index without writing to its source DB.

The source database is opened read-only. Vectors and provenance are stored in a
separate SQLite sidecar. Each successful API batch is committed as a checkpoint,
so rerunning this command resumes without paying twice for saved chunks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from dotenv import dotenv_values
from openai import BadRequestError, OpenAI

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DB = ROOT / "data" / "ani_rag_live_20261111.sqlite3"
DEFAULT_INDEX_DB = ROOT / "output" / "ani_rag_evidence_vectors_20261119.sqlite3"
MODEL = "text-embedding-3-small"
DIMENSIONS = 1024
MAX_CHARS = 1200
OVERLAP = 120
STEP = MAX_CHARS - OVERLAP
API_BATCH_SIZE = 512
SCHEMA_VERSION = "1"


def _chunk(text: str, *, document_id: str, source_kind: str,
           source_index: int | None, locator: str,
           seen: set[bytes]) -> Iterable[dict[str, object]]:
    for start in range(0, len(text), STEP):
        raw = text[start:start + MAX_CHARS]
        left_trimmed = len(raw) - len(raw.lstrip())
        right_bound = len(raw.rstrip())
        value = raw.strip()
        if value:
            actual_start = start + left_trimmed
            actual_end = start + right_bound
            text_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
            signature = bytes.fromhex(text_hash)
            if signature not in seen:
                seen.add(signature)
                chunk_identity = (
                    f"{document_id}\n{source_kind}\n{source_index}\n{locator}\n"
                    f"{actual_start}\n{value}"
                )
                chunk_id = hashlib.sha256(chunk_identity.encode("utf-8")).hexdigest()
                yield {
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "source_kind": source_kind,
                    "source_index": source_index,
                    "locator": f"{locator}:{actual_start}-{actual_end}",
                    "start_offset": actual_start,
                    "end_offset": actual_end,
                    "text_hash": text_hash,
                    "chunk_text": value,
                }
        if start + MAX_CHARS >= len(text):
            break


def _iter_evidence_chunks(
    source: sqlite3.Connection, *, after_raw_document_id: int = 0,
    through_raw_document_id: int | None = None,
) -> Iterable[dict[str, object]]:
    upper_bound = "" if through_raw_document_id is None else " AND d.raw_document_id <= ?"
    bounds = (after_raw_document_id,) if through_raw_document_id is None else (after_raw_document_id, through_raw_document_id)
    cursor = source.execute(f"""
        SELECT d.raw_document_id, d.document_id, d.source_id, d.content_type, d.evidence_type,
               d.title, d.url, d.original_language, d.published_at, d.raw_text,
               s.source_grade, e.locator AS excerpt_locator,
               e.excerpt_text, e.excerpt_id
        FROM rag_document d
        LEFT JOIN rag_source s ON s.source_id=d.source_id
        LEFT JOIN rag_excerpt e ON e.document_id=d.document_id
        WHERE d.evidence_type='reference_fact' AND d.raw_document_id>?{upper_bound}
        ORDER BY d.raw_document_id, e.excerpt_id
    """, bounds)
    current_id: str | None = None
    seen: set[bytes] = set()
    excerpt_index = 0
    for row in cursor:
        document_id = str(row["document_id"])
        if document_id != current_id:
            current_id = document_id
            seen = set()
            excerpt_index = 0
            body = row["raw_text"]
            source_id = str(row["source_id"])
            # Kitsu body is an API envelope; its stored excerpt is the readable
            # evidence and avoids embedding transport JSON as prose.
            if body and body.strip() and source_id != "kitsu_anime_catalog":
                for piece in _chunk(
                    str(body), document_id=document_id, source_kind="body",
                    source_index=None, locator="body", seen=seen,
                ):
                    yield _with_document_metadata(piece, row)
        excerpt = row["excerpt_text"]
        if excerpt and str(excerpt).strip():
            locator = str(row["excerpt_locator"] or f"excerpt:{excerpt_index}")
            for piece in _chunk(
                str(excerpt), document_id=document_id, source_kind="excerpt",
                source_index=excerpt_index, locator=locator, seen=seen,
            ):
                yield _with_document_metadata(piece, row)
            excerpt_index += 1


def _with_document_metadata(piece: dict[str, object], row: sqlite3.Row) -> dict[str, object]:
    return {
        **piece,
        "source_raw_document_id": int(row["raw_document_id"]),
        "source_id": str(row["source_id"]),
        "content_type": str(row["content_type"]),
        "evidence_type": str(row["evidence_type"]),
        "title": str(row["title"]),
        "source_url": str(row["url"]),
        "language": str(row["original_language"]),
        "published_at": row["published_at"],
        "source_grade": str(row["source_grade"] or "unknown"),
    }


def _open_index(path: Path, source_db: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS index_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS embedding_vector (
            text_hash TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector_f32 BLOB NOT NULL,
            input_tokens INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evidence_chunk (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_grade TEXT NOT NULL,
            content_type TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            title TEXT NOT NULL,
            source_url TEXT NOT NULL,
            language TEXT NOT NULL,
            published_at TEXT,
            source_kind TEXT NOT NULL,
            source_index INTEGER,
            locator TEXT NOT NULL,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            text_hash TEXT NOT NULL REFERENCES embedding_vector(text_hash),
            chunk_text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_evidence_chunk_document
            ON evidence_chunk(document_id);
        CREATE INDEX IF NOT EXISTS ix_evidence_chunk_work_scope
            ON evidence_chunk(source_id, content_type, evidence_type);
    """)
    expected = {
        "schema_version": SCHEMA_VERSION,
        "source_database": str(source_db.resolve()),
        "source_size_bytes": str(source_db.stat().st_size),
        "model": MODEL,
        "dimensions": str(DIMENSIONS),
        "max_chars": str(MAX_CHARS),
        "overlap": str(OVERLAP),
        "source_scope": "rag_document.evidence_type=reference_fact",
    }
    existing = dict(connection.execute("SELECT key,value FROM index_metadata"))
    for key, value in expected.items():
        if key in existing and existing[key] != value:
            raise ValueError(f"Existing index metadata mismatch for {key!r}; refusing to mix runs")
        connection.execute(
            "INSERT OR IGNORE INTO index_metadata(key,value) VALUES(?,?)", (key, value),
        )
    connection.execute(
        "INSERT OR IGNORE INTO index_metadata(key,value) VALUES('started_at',?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    connection.commit()
    return connection


def _existing_chunks(connection: sqlite3.Connection, chunk_ids: list[str]) -> set[str]:
    found: set[str] = set()
    for offset in range(0, len(chunk_ids), 800):
        batch = chunk_ids[offset:offset + 800]
        marks = ",".join("?" for _ in batch)
        found.update(row[0] for row in connection.execute(
            f"SELECT chunk_id FROM evidence_chunk WHERE chunk_id IN ({marks})", batch,
        ))
    return found


def _existing_vectors(connection: sqlite3.Connection, hashes: list[str]) -> set[str]:
    found: set[str] = set()
    for offset in range(0, len(hashes), 800):
        batch = hashes[offset:offset + 800]
        marks = ",".join("?" for _ in batch)
        found.update(row[0] for row in connection.execute(
            f"SELECT text_hash FROM embedding_vector WHERE text_hash IN ({marks})", batch,
        ))
    return found


def _request_embeddings(client: OpenAI, text_by_hash: dict[str, str]):
    """Recursively split a batch when its combined token count exceeds the API cap."""
    items = list(text_by_hash.items())
    try:
        response = client.embeddings.create(
            model=MODEL,
            input=[text for _, text in items],
            dimensions=DIMENSIONS,
            encoding_format="float",
        )
        if len(response.data) != len(items):
            raise RuntimeError("Embedding response count does not match the request")
        return [(key, item.embedding) for (key, _), item in zip(items, response.data, strict=True)], int(
            response.usage.prompt_tokens if response.usage else 0
        )
    except BadRequestError as error:
        message = str(error)
        if "maximum request size" not in message or len(items) <= 1:
            raise
        middle = len(items) // 2
        left_data, left_tokens = _request_embeddings(client, dict(items[:middle]))
        right_data, right_tokens = _request_embeddings(client, dict(items[middle:]))
        return left_data + right_data, left_tokens + right_tokens


def _flush_batch(connection: sqlite3.Connection, client: OpenAI,
                 pending: list[dict[str, object]], totals: dict[str, int | float],
                 *, started: float) -> None:
    if not pending:
        return
    chunk_ids = [str(item["chunk_id"]) for item in pending]
    already = _existing_chunks(connection, chunk_ids)
    fresh = [item for item in pending if str(item["chunk_id"]) not in already]
    if not fresh:
        pending.clear()
        return

    vector_hashes = _existing_vectors(connection, list(dict.fromkeys(
        str(item["text_hash"]) for item in fresh
    )))
    text_by_hash: dict[str, str] = {}
    for item in fresh:
        text_hash = str(item["text_hash"])
        if text_hash not in vector_hashes:
            text_by_hash.setdefault(text_hash, str(item["chunk_text"]))

    vectors: dict[str, tuple[bytes, int]] = {}
    if text_by_hash:
        embedded, provider_tokens = _request_embeddings(client, text_by_hash)
        tokens_left = provider_tokens
        entries = list(text_by_hash.items())
        token_shares = [0] * len(entries)
        # Keep total provider usage exact even though per-input usage is unavailable.
        if entries:
            approximate = [max(1, len(value) // 4) for _, value in entries]
            denom = sum(approximate)
            for index, amount in enumerate(approximate):
                share = min(tokens_left, round(provider_tokens * amount / denom))
                token_shares[index] = share
                tokens_left -= share
            if token_shares:
                token_shares[-1] += tokens_left
        for (text_hash, embedding), input_tokens in zip(embedded, token_shares, strict=True):
            if len(embedding) != DIMENSIONS:
                raise RuntimeError(f"Expected {DIMENSIONS} dimensions; got {len(embedding)}")
            vectors[text_hash] = (struct.pack(f"<{DIMENSIONS}f", *embedding), input_tokens)
        totals["provider_tokens"] = int(totals["provider_tokens"]) + provider_tokens
        totals["estimated_usd"] = int(totals["provider_tokens"]) * 0.02 / 1_000_000

    now = datetime.now(timezone.utc).isoformat()
    with connection:
        connection.executemany(
            "INSERT OR IGNORE INTO embedding_vector(text_hash,model,dimensions,vector_f32,input_tokens,created_at) VALUES(?,?,?,?,?,?)",
            [(text_hash, MODEL, DIMENSIONS, blob, tokens, now)
             for text_hash, (blob, tokens) in vectors.items()],
        )
        connection.executemany("""
            INSERT OR IGNORE INTO evidence_chunk(
                chunk_id,document_id,source_id,source_grade,content_type,evidence_type,
                title,source_url,language,published_at,source_kind,source_index,locator,
                start_offset,end_offset,text_hash,chunk_text
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(
            item["chunk_id"], item["document_id"], item["source_id"], item["source_grade"],
            item["content_type"], item["evidence_type"], item["title"], item["source_url"],
            item["language"], item["published_at"], item["source_kind"], item["source_index"],
            item["locator"], item["start_offset"], item["end_offset"], item["text_hash"],
            item["chunk_text"],
        ) for item in fresh])
        connection.execute(
            "INSERT OR REPLACE INTO index_metadata(key,value) VALUES('updated_at',?)", (now,),
        )
        # The next batch may still contain excerpts from the last document.
        # Its saved chunks are reused if this pass fails and resumes.
        completed_through = max(
            int(item["source_raw_document_id"]) for item in fresh
            if item.get("source_raw_document_id") is not None
        ) - 1
        previous = connection.execute(
            "SELECT value FROM index_metadata WHERE key='last_source_raw_document_id'"
        ).fetchone()
        if previous is None or completed_through > int(previous[0]):
            connection.execute(
                "INSERT OR REPLACE INTO index_metadata(key,value) VALUES('last_source_raw_document_id',?)",
                (str(completed_through),),
            )
    totals["chunks_saved"] = int(totals["chunks_saved"]) + len(fresh)
    totals["batches_saved"] = int(totals["batches_saved"]) + 1
    if int(totals["batches_saved"]) % 10 == 0:
        print(json.dumps({
            "progress": "embedding",
            "chunks_saved_this_run": int(totals["chunks_saved"]),
            "provider_tokens_this_run": int(totals["provider_tokens"]),
            "estimated_cost_usd_this_run": round(float(totals["estimated_usd"]), 6),
            "elapsed_minutes": round((time.monotonic() - started) / 60, 1),
        }), flush=True)
    pending.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--index-db", type=Path, default=DEFAULT_INDEX_DB)
    args = parser.parse_args()
    source_db = args.source_db.resolve()
    index_db = args.index_db.resolve()
    if not source_db.is_file():
        raise FileNotFoundError(source_db)

    secrets = dotenv_values(ROOT / ".env")
    api_key = secrets.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is unavailable; the key value is never printed")

    source = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True, timeout=60)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    index = _open_index(index_db, source_db)
    client = OpenAI(api_key=api_key, timeout=180.0, max_retries=6)
    totals: dict[str, int | float] = {
        "chunks_saved": 0, "batches_saved": 0, "provider_tokens": 0, "estimated_usd": 0.0,
    }
    pending: list[dict[str, object]] = []
    started = time.monotonic()
    try:
        source_high_water = source.execute(
            "SELECT COALESCE(MAX(raw_document_id),0) FROM rag_document WHERE evidence_type='reference_fact'"
        ).fetchone()[0]
        for chunk in _iter_evidence_chunks(source, through_raw_document_id=source_high_water):
            pending.append(chunk)
            if len(pending) >= API_BATCH_SIZE:
                _flush_batch(index, client, pending, totals, started=started)
        _flush_batch(index, client, pending, totals, started=started)
        index.execute(
            "INSERT OR REPLACE INTO index_metadata(key,value) VALUES('last_source_raw_document_id',?)",
            (str(int(source_high_water)),),
        )
        counts = index.execute(
            "SELECT count(*),count(DISTINCT text_hash) FROM evidence_chunk"
        ).fetchone()
        summary = {
            "status": "complete",
            "index_db": str(index_db),
            "source_db_modified": False,
            "index_chunk_count": counts[0],
            "unique_vectors": counts[1],
            "chunks_added_this_run": int(totals["chunks_saved"]),
            "provider_tokens_this_run": int(totals["provider_tokens"]),
            "estimated_cost_usd_this_run": round(float(totals["estimated_usd"]), 6),
            "elapsed_minutes": round((time.monotonic() - started) / 60, 1),
        }
        index.execute(
            "INSERT OR REPLACE INTO index_metadata(key,value) VALUES('completed_at',?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        index.commit()
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except Exception as error:
        index.execute(
            "INSERT OR REPLACE INTO index_metadata(key,value) VALUES('last_error_type',?)",
            (type(error).__name__,),
        )
        index.execute(
            "INSERT OR REPLACE INTO index_metadata(key,value) VALUES('last_error_at',?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        index.commit()
        print(json.dumps({
            "status": "interrupted_resumable",
            "index_db": str(index_db),
            "saved_chunks": index.execute("SELECT count(*) FROM evidence_chunk").fetchone()[0],
            "provider_tokens_this_run": int(totals["provider_tokens"]),
            "error_type": type(error).__name__,
            "elapsed_minutes": round((time.monotonic() - started) / 60, 1),
        }, ensure_ascii=False), flush=True)
        raise
    finally:
        source.close()
        index.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
