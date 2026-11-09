# Worker runtime

`runtime.py` contains three worker loops that run in separate OS processes:

- `run_crawler_worker` discovers `news`, `event`, and `goods`, persists source/evidence/image records, and enqueues an idempotent `generate` job. It never calls the content generation graph when using `crawl-type --enqueue-only`.
- If deterministic work matching fails, the crawler passes the captured original page and publisher metadata to the work-matching LLM. A clear primary anime title can reuse an existing ID or receive the next ID after exact-title/alias duplicate checks. Before generation is enqueued, the same capture transaction stores the `source_site`, `raw_document`, and catalog-backed ERD `entity`; an assigned ID missing from the canonical catalog aborts the handoff.
- `run_queue_worker(stage=generate)` claims only generation jobs, validates rights/work mapping, invokes only `build_content_generation_agent`, stores the draft, and enqueues review.
- `run_queue_worker(stage=review)` claims only review jobs, invokes only `build_reviewer_agent`, then enqueues a revision, requests crawler repair, archives rejection, or feeds an approved draft. The time-sensitive worker uses `minimal_review`: it checks carousel size/nonempty copy/repeated images and asks the reviewer model to assess readability and flow. Fact-checking and copyright/image-rights review are skipped in this pilot mode.

Queue workers renew leases while an item is in progress. Errors retry with bounded backoff and become failed after five claims. The caller owns process start/stop; importing this module starts nothing.

The automatic new-title registration above is the current period-agent decision and supersedes older reference text that required Kitsu confirmation/team review before assigning an ID. Matching still requires source-grounded primary-title evidence and exact duplicate checks.

Use `ani-menu pipeline-worker --stage <crawl|generate|review>`. For the coordinated three-process launcher and STOP-file behavior see the parent pipeline README.
