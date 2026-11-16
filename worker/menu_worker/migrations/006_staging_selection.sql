-- A v4 article can have several sealed results. Select one on verified export;
-- the retry owner established by v5 is not necessarily a publishable result.
CREATE TABLE public.content_staging_selection (
    article_job_id UUID PRIMARY KEY REFERENCES public.content_generation_job(job_id),
    selected_job_id UUID NOT NULL REFERENCES public.content_generation_job(job_id),
    generation_attempt_id UUID NOT NULL REFERENCES public.content_generation_attempt(attempt_id),
    result_sha256 CHAR(64) NOT NULL CHECK (result_sha256 ~ '^[0-9a-f]{64}$')
);

ALTER TABLE public.menu_worker_schema DROP CONSTRAINT menu_worker_schema_version_check;
UPDATE public.menu_worker_schema SET version = 6 WHERE version = 5;
ALTER TABLE public.menu_worker_schema ADD CONSTRAINT menu_worker_schema_version_check CHECK (version = 6);
