-- The scheduler keeps every idempotency key global. A verified probe and an
-- explicitly requested live generation may now own separate jobs for one URL.
ALTER TABLE public.content_generation_job
    DROP CONSTRAINT content_generation_job_job_kind_check,
    DROP CONSTRAINT content_generation_job_canonical_url_key,
    DROP CONSTRAINT ck_content_job_work_source,
    DROP CONSTRAINT ck_content_job_kind_outcome,
    ADD CONSTRAINT ck_content_job_kind CHECK (job_kind IN ('fake', 'anime', 'anime_generate')),
    ADD CONSTRAINT ck_content_job_work_source CHECK (
        (job_kind = 'fake' AND menu_work_id IS NULL AND source_id = 'fixture')
        OR (job_kind = 'anime' AND menu_work_id IS NULL AND source_id = 'fixture'
            AND canonical_url LIKE 'https://example.invalid/success/_%'
            AND execution_status <> 'queued')
        OR (job_kind IN ('anime', 'anime_generate') AND menu_work_id IS NOT NULL AND menu_work_id > 0
            AND source_id NOT IN ('', 'fixture')
            AND source_url ~* '^https://[^/?#[:space:]]+[/?]' AND canonical_url ~ '^https://[^/?#[:space:]]+/'
            AND source_url !~* '^https://example[.]invalid([:/?#]|$)'
            AND canonical_url !~* '^https://example[.]invalid([:/?#]|$)')
    ),
    ADD CONSTRAINT ck_content_job_kind_outcome CHECK (
        (job_kind = 'fake' AND (outcome IS NULL OR outcome IN ('ready_for_staging', 'held', 'skipped')))
        OR (job_kind = 'anime' AND (
            (execution_status = 'succeeded' AND outcome IS NOT NULL AND outcome = 'routing_verified'
                AND producer_status IS NOT NULL AND producer_status = 'anime_probe_completed')
            OR (execution_status <> 'succeeded' AND outcome IS NULL)
        ))
        OR (job_kind = 'anime_generate' AND (
            (execution_status = 'succeeded' AND outcome IS NOT NULL
                AND outcome IN ('ready_for_staging', 'held', 'skipped', 'rejected')
                AND producer_status IS NOT NULL AND producer_status ~ '^[a-zA-Z0-9_.:-]{1,100}$')
            OR (execution_status <> 'succeeded' AND outcome IS NULL)
        ))
    );

CREATE UNIQUE INDEX uq_content_job_probe_url ON public.content_generation_job(canonical_url)
    WHERE job_kind IN ('fake', 'anime');
CREATE UNIQUE INDEX uq_content_job_generation_url ON public.content_generation_job(canonical_url)
    WHERE job_kind = 'anime_generate';

ALTER TABLE public.menu_worker_schema DROP CONSTRAINT menu_worker_schema_version_check;
UPDATE public.menu_worker_schema SET version = 4 WHERE version = 3;
ALTER TABLE public.menu_worker_schema ADD CONSTRAINT menu_worker_schema_version_check CHECK (version = 4);
