-- Applied only by the explicit JobStore.initialize() command in a dedicated DB.
CREATE TABLE public.menu_worker_schema (
    version INTEGER PRIMARY KEY CHECK (version = 1),
    installed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE public.content_generation_job (
    job_id UUID PRIMARY KEY,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    source_id VARCHAR(100) NOT NULL CHECK (source_id = 'fixture'),
    source_url TEXT NOT NULL CHECK (length(source_url) <= 2048),
    canonical_url TEXT NOT NULL UNIQUE CHECK (length(canonical_url) <= 2048),
    content_type VARCHAR(32) NOT NULL CHECK (content_type IN
        ('news','event','guide','people','goods','review','humor','character','curation','music')),
    input_sha256 CHAR(64) NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    execution_status VARCHAR(24) NOT NULL DEFAULT 'queued' CHECK (execution_status IN
        ('queued','running','succeeded','failed','needs_attention','cancelled')),
    outcome VARCHAR(32) CHECK (outcome IS NULL OR outcome IN ('ready_for_staging','held','skipped')),
    producer_status VARCHAR(100),
    current_attempt_id UUID,
    generation_attempt_id UUID,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    claim_token UUID,
    lease_expires_at TIMESTAMPTZ,
    generation_started_at TIMESTAMPTZ,
    dispatch_count INTEGER NOT NULL DEFAULT 0 CHECK (dispatch_count >= 0),
    last_dispatched_at TIMESTAMPTZ,
    last_task_id VARCHAR(100),
    artifact_path TEXT,
    result_sha256 CHAR(64) CHECK (result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'),
    error_code VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    CHECK ((execution_status = 'running' AND claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (execution_status <> 'running' AND claim_token IS NULL AND lease_expires_at IS NULL))
);

CREATE TABLE public.content_generation_attempt (
    attempt_id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES public.content_generation_job(job_id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('generation','recovery')),
    celery_task_id VARCHAR(100),
    claim_token UUID NOT NULL UNIQUE,
    execution_status VARCHAR(24) NOT NULL DEFAULT 'running' CHECK (execution_status IN
        ('running','succeeded','failed','needs_attention','cancelled')),
    generation_started_at TIMESTAMPTZ,
    outcome VARCHAR(32),
    producer_status VARCHAR(100),
    artifact_path TEXT,
    result_sha256 CHAR(64),
    error_code VARCHAR(100),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    UNIQUE (job_id, attempt_no)
);

ALTER TABLE public.content_generation_job
    ADD CONSTRAINT fk_content_job_current_attempt FOREIGN KEY (current_attempt_id)
    REFERENCES public.content_generation_attempt(attempt_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public.content_generation_job
    ADD CONSTRAINT fk_content_job_generation_attempt FOREIGN KEY (generation_attempt_id)
    REFERENCES public.content_generation_attempt(attempt_id) DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX idx_content_job_pending ON public.content_generation_job(last_dispatched_at, created_at)
    WHERE execution_status = 'queued';
CREATE INDEX idx_content_job_expired ON public.content_generation_job(lease_expires_at)
    WHERE execution_status = 'running';
INSERT INTO public.menu_worker_schema(version) VALUES (1);
