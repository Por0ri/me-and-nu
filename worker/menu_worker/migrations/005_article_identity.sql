-- canonical_url and input_sha256 also identify sealed producer artifacts. Keep
-- both immutable when deriving the candidate identity for pre-v5 articles.
ALTER TABLE public.content_generation_job
    ADD COLUMN candidate_url TEXT GENERATED ALWAYS AS (
        CASE WHEN source_id = 'fixture' THEN canonical_url
             ELSE regexp_replace(canonical_url, '^(https://[^/?]+/[^?]+)/([?]|$)', '\1\2')
        END
    ) STORED,
    ADD COLUMN duplicate_of_job_id UUID REFERENCES public.content_generation_job(job_id);

-- Prefer an existing result or possibly started producer over unstarted work.
-- Keep both historical job IDs and each key's original attempt history.
WITH ranked AS (
    SELECT job_id, first_value(job_id) OVER (
        PARTITION BY (job_kind = 'anime_generate'), source_id, candidate_url
        ORDER BY (execution_status = 'succeeded') DESC, (generation_started_at IS NOT NULL) DESC,
                 CASE execution_status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                 created_at, job_id
    ) AS owner_id
    FROM public.content_generation_job
)
UPDATE public.content_generation_job AS job SET duplicate_of_job_id = ranked.owner_id
FROM ranked WHERE job.job_id = ranked.job_id AND ranked.job_id <> ranked.owner_id;

-- A delivered or leased sibling may still be waiting to launch. Fence both its
-- ledger claim and any late Celery delivery before releasing the migration lock.
UPDATE public.content_generation_attempt AS attempt
SET execution_status = 'cancelled', error_code = 'DUPLICATE_ARTICLE', finished_at = CURRENT_TIMESTAMP
FROM public.content_generation_job AS job
WHERE job.duplicate_of_job_id IS NOT NULL AND job.execution_status = 'running'
    AND job.generation_started_at IS NULL AND attempt.attempt_id = job.current_attempt_id;
UPDATE public.content_generation_job
SET execution_status = 'cancelled', claim_token = NULL, lease_expires_at = NULL,
    error_code = 'DUPLICATE_ARTICLE', updated_at = CURRENT_TIMESTAMP, finished_at = CURRENT_TIMESTAMP
WHERE duplicate_of_job_id IS NOT NULL AND generation_started_at IS NULL AND execution_status IN ('queued', 'running');

-- Flush the ledger's deferred attempt references before the remaining DDL.
SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE public.content_generation_job ADD CONSTRAINT ck_content_job_duplicate_execution
    CHECK (duplicate_of_job_id IS NULL OR (duplicate_of_job_id <> job_id AND execution_status <> 'queued'
        AND (execution_status <> 'running' OR generation_started_at IS NOT NULL)));
DROP INDEX public.uq_content_job_probe_url;
DROP INDEX public.uq_content_job_generation_url;
CREATE UNIQUE INDEX uq_content_job_probe_url ON public.content_generation_job(source_id, candidate_url)
    WHERE duplicate_of_job_id IS NULL AND job_kind IN ('fake', 'anime');
CREATE UNIQUE INDEX uq_content_job_generation_url ON public.content_generation_job(source_id, candidate_url)
    WHERE duplicate_of_job_id IS NULL AND job_kind = 'anime_generate';

ALTER TABLE public.menu_worker_schema DROP CONSTRAINT menu_worker_schema_version_check;
UPDATE public.menu_worker_schema SET version = 5 WHERE version = 4;
ALTER TABLE public.menu_worker_schema ADD CONSTRAINT menu_worker_schema_version_check CHECK (version = 5);
