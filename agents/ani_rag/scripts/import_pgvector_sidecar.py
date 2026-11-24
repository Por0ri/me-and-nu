"""Copy a verified SQLite vector snapshot into an explicitly selected RAG DB.

Does not recreate embeddings, start workers, or connect to the service database.
The DSN is read from one named environment variable and is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ani_rag.storage.postgres_vector import PostgresVectorStore, connect_postgres


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--index-db", type=Path, required=True)
    parser.add_argument("--database-url-env", default="ANI_RAG_VECTOR_DATABASE_URL")
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--immutable-snapshot", action="store_true",
                        help="Only for verified frozen handoff files; never use with a live SQLite DB")
    args = parser.parse_args()
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        raise ValueError("The selected database URL environment variable is empty")
    connection = connect_postgres(database_url, expected_database=args.expected_database)
    batches = 0

    def progress(event):
        nonlocal batches
        batches += 1
        if batches % 25 == 0:
            print(json.dumps({**event, "batches_committed": batches}), flush=True)

    try:
        result = PostgresVectorStore(connection).import_sqlite(
            source_path=args.source_db, index_path=args.index_db,
            batch_size=args.batch_size, progress=progress, immutable_snapshot=args.immutable_snapshot,
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
