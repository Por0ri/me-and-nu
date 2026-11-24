-- Apply only to the dedicated ani_rag database after migration 0001/schema.sql.
-- This migration is an artifact; it has not been executed against any database.
CREATE TABLE IF NOT EXISTS rag_source_read_ledger (
    claim_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES rag_source(source_id),
    source_native_id TEXT,
    canonical_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('fetching','complete','failed')),
    last_success_at TEXT,
    next_check_at TEXT,
    lease_expires_at TEXT,
    content_hash TEXT,
    document_id TEXT REFERENCES rag_document(document_id),
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
    last_error_code TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(source_id, canonical_url)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_source_read_native_id
    ON rag_source_read_ledger(source_id, source_native_id)
    WHERE source_native_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_source_read_due
    ON rag_source_read_ledger(source_id, next_check_at, status);
