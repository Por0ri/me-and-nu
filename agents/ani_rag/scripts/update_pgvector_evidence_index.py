"""Resume pgvector indexing using already saved vectors only.

New text without a saved vector stops this command before advancing its document
checkpoint. This command deliberately has no OpenAI credential or client path.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import closing
from pathlib import Path

from ani_rag.storage.postgres_vector import (
    PostgresVectorStore, connect_postgres, open_sqlite_readonly,
)
from build_vector_evidence_index import _iter_evidence_chunks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--database-url-env", default="ANI_RAG_VECTOR_DATABASE_URL")
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--immutable-snapshot", action="store_true",
                        help="Only for verified frozen handoff files; live indexing keeps normal read-only mode")
    args = parser.parse_args()
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        raise ValueError("The selected database URL environment variable is empty")
    with closing(open_sqlite_readonly(args.source_db, immutable_snapshot=args.immutable_snapshot)) as source:
        with closing(connect_postgres(database_url, expected_database=args.expected_database)) as connection:
            result = PostgresVectorStore(connection).update_from_source(
                source, chunk_iterator=_iter_evidence_chunks, batch_size=args.batch_size,
            )
            print(json.dumps({**result, "paid_embedding_enabled": False}, ensure_ascii=False), flush=True)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
