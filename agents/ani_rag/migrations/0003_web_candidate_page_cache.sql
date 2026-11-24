-- Apply only to the dedicated ani_rag database after the existing schema.
-- Stores short-lived untrusted discovery pages, never RAG factual evidence.
-- This migration artifact has not been applied to any database.
CREATE TABLE IF NOT EXISTS rag_web_candidate_page_cache (
    cache_id TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL UNIQUE,
    final_canonical_url TEXT,
    status TEXT NOT NULL CHECK(status IN ('fetching','ready','failed')),
    page_json TEXT,
    fetched_at TEXT,
    expires_at TEXT,
    lease_expires_at TEXT,
    retry_after TEXT,
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
    last_error_code TEXT,
    updated_at TEXT NOT NULL,
    CHECK((status='ready' AND page_json IS NOT NULL AND fetched_at IS NOT NULL AND expires_at IS NOT NULL)
       OR status!='ready')
);
CREATE INDEX IF NOT EXISTS ix_web_candidate_cache_expiry
    ON rag_web_candidate_page_cache(status, expires_at, retry_after);
