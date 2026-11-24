# Worker runtime

`runtime.py` contains three worker loops that run in separate OS processes:

- `run_crawler_worker` discovers `news`, `event`, and `goods`, persists source/evidence/image records, and enqueues an idempotent `generate` job. It never calls the content generation graph when using `crawl-type --enqueue-only`.
- If deterministic work matching fails, the crawler passes the captured original page and publisher metadata to the work-matching LLM. It reads the official works and entries in the root `catalog/` and checks `catalog/agent_works.csv`, shared with the RAG agent. An existing match reuses its ID. A confirmed new title can receive a stable temporary ID derived from its Kitsu ID or normalized title. Both agents record ambiguous titles and ID collisions in `catalog/discovered_works.csv` for review.
- Before generation is enqueued, the capture transaction stores the `source_site`, `raw_document`, and ERD `entity` for the matched work. Official and registered temporary IDs can enter the generation queue. The team assigns official IDs. Existing SQLite work links are preserved.
- The SQLite table `raw_document_schedule` records the original article's event, sale, reservation, release and premiere dates. Query `content_schedule` by `content_id` for the dates and source URL of a generated article. A start without an announced end leaves `ends_at` NULL. A month-only announcement keeps its month precision.
- Workers check known closing dates before creating or handing off a new article. The backend controls the visibility of articles already handed off.
- `run_queue_worker(stage=generate)` claims only generation jobs, validates rights/work mapping, invokes only `build_content_generation_agent`, stores the draft, and enqueues review.
- `run_queue_worker(stage=review)` claims only review jobs, invokes only `build_reviewer_agent`, then enqueues a revision, requests crawler repair, archives rejection, or feeds an approved draft. The time-sensitive worker uses `minimal_review`: it checks carousel size/nonempty copy/repeated images and asks the reviewer model to assess readability and flow. Fact-checking and copyright/image-rights review are skipped in this pilot mode.

The crawler checks one returned request per content type on each listing scan. It reopens the original article up to three times for missing dates or images, with waits of five and ten minutes between attempts. When the article supplies the missing evidence and the minimum card layout is possible, the crawler queues generation once under the same request ID.

Generation and review workers renew their leases while an item is in progress. Processing errors retry with bounded backoff and become failed after five claims. The caller owns process start and stop.

Use `ani-menu pipeline-worker --stage <crawl|generate|review>`. For the coordinated three-process launcher and STOP-file behavior see the parent pipeline README.
