-- Apply only to the dedicated ani_rag database after migrations 0002 and 0003.
-- Keeps canonical candidate URL history after short-lived page bodies expire.
-- This migration artifact has not been applied to any database.
CREATE TABLE IF NOT EXISTS rag_web_candidate_url_ledger (
    canonical_url TEXT PRIMARY KEY,
    final_canonical_url TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_fetched_at TEXT,
    next_check_at TEXT,
    last_content_hash TEXT,
    last_error_code TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_web_candidate_url_due
    ON rag_web_candidate_url_ledger(next_check_at, last_seen_at);
