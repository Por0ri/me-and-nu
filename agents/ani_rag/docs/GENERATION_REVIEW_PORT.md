# ani_rag generation and review port

Updated 2026-11-08 after a read-only comparison with `ani_me;nu`.

## Shared downstream shape

Both lanes use the same product sequence: retrieve/source evidence, prepare carousel copy and a detailed article, save the draft, then obtain an independent review. The generation worker does not review its own draft. In the current RAG pilot, only carousel order and copy readability trigger revision; the seven evergreen types and period types remain separate at collection and evidence verification, while downstream content formats can stay aligned.

The period project implements a larger editorial graph: copy deck, layout/art direction, optional visual repair and image rendering, detail writing, then a separate quality and language/visual review graph. It is coupled to `ani_menu_agent` settings, `AgentRepository`, legacy state, catalog, prompts, and image handling. None of that runtime package or persistence was copied.

`ani_rag` implements the shared downstream contract independently in role-separated `agents/mcp_crawler/`, `agents/content_generation/`, and `agents/reviewer/` packages:

- `EvidenceBoundGenerationService` receives a completed RAG retrieval trace, creates a copy deck and a separate detail article in distinct structured calls, and links claims to supplied evidence/chunks. A separate vision layout call chooses supported background/emphasis for each page when registered local artwork and a vision-capable model are available.
- The generation worker renders one 1308×1632 text-free PNG per card, persists it alongside the card-specific frontend copy and normalized HTML overlay boxes, then queues independent review. It never merges text into the stored PNG.
- `EvidenceBoundIndependentReviewer` receives a temporary copy-overlay preview of every page when available, checks the detail article independently, and returns a structured scorecard plus pass/revise/request-evidence/human-review/archive. The worker performs deterministic checks first and applies the routing policy.
- `OpenAIResponsesStructuredModel` provides the model transport using strict structured output. Its API key is supplied explicitly to the constructor; this module does not load `.env` or start a request during import.
- Each of the three role agents now exposes `run_forever(stop_event=...)`; it repeatedly calls its one-item worker and sleeps while its queue is empty. No worker starts automatically.
- `compose_runtime()` wires the separate collection, content-generation, and reviewer roles over caller-supplied dependencies. `compose_generation_review_runtime()` remains as a generation/review-only compatibility composition. Neither performs SQL/schema setup.

Claims must cite supplied evidence IDs and retrieved chunk IDs. The service rejects invented evidence/chunk/image IDs and creates source-footer URLs from the stored records instead of asking the model to author citations. Numeric `topic_id`, `subtopic_id`, and ERD `entity_id` values are not generated or inferred; RAG `content_type` and resolved `menu_work_id` remain separate identities.

The OpenAI adapter sends supplied source text, draft and (for visual review) temporary page previews when a caller runs it. It sets Responses `store=False`; the local project does not load `.env` or initiate requests by itself. Configure copy/detail generation, layout direction, and review as separate model stages; they may share a client but remain distinct calls and prompts.

The intended composition shape is:

```python
generation_model = OpenAIResponsesStructuredModel(api_key=..., model=...)
review_model = OpenAIResponsesStructuredModel(api_key=..., model=...)
runtime = compose_generation_review_runtime(
    connection, generation_model=generation_model, review_model=review_model
)
# Call runtime.queue_reconciler.run_once(), runtime.generation_worker.run_once(),
# or runtime.review_worker.run_once() only from an authorized execution entrypoint.
```

The two model objects may use the same model deployment, but they are separate calls with separate prompts and roles. This snippet only shows composition; it does not select credentials, open or provision a database, or start work. The caller must open a dedicated RAG database connection with `check_same_thread=False` after applying the approved RAG schema separately.

## Remaining gates before end-to-end use

- No source is approved, and no MCP connection/tool discovery is implemented or invoked.
- The model adapters exist, but no API key/model has been configured or called. The OpenAI adapter requires an explicit API key and model choice from the caller.
- Generation requires at least three registered, work-matched image candidates with documented source and rights fields. This project still has no image search/download or independent rights-verification service.
- Visual review receives temporary page previews with copy overlays when every rendered PNG is present and the model supports image input. If previews are unavailable, the reviewer records visual review as pending without blocking an otherwise passing draft. A human review UI/approval action is still not implemented.
- Retrieval, persistence, source plans, adapters, and models are dependency-injected. There is no bootstrapping command, production DB selection, migration, CLI, or process supervisor. Standalone compose functions let a host run each role with its own DB connection.
- Rendered PNG BLOBs and per-page frontend copy/layout manifests are persisted in the RAG staging schema. A frontend/API still needs to read those fields and paint each HTML overlay; ani_rag does not ship a frontend page.
- The supplied ERD's topic/subtopic/entity lookup masters and crosswalk are still missing. Draft generation can proceed with string content type and `menu_work_id`; final ERD projection cannot populate required numeric foreign keys until authoritative mappings exist.

The generation and review implementations are wired at the provider-injected service boundary, not ready for unattended production execution. No tests, model requests, MCP calls, DB commands, CLI, or workers were run for this port.

## One-item runtime wiring update

`compose_runtime()` wires all role agents for a host that wants them together. `compose_crawler_agent()`, `compose_content_generation_agent()`, and `compose_reviewer_agent()` wire each role separately; each accepts a caller-owned connection to the same separately provisioned RAG database. The recurring crawler plan list is explicit and empty by default.

The intended manual sequence is:

1. Enqueue one bounded request with `enqueue_url_collection()` or `enqueue_structured_collection()`.
2. The crawler's `run_forever()` enqueues each due approved recurring plan and processes collection requests continuously.
3. Collection checks source approval, live tool names, source identity, result URLs, and redirect evidence before it stores source material and the generation backlog row.
4. The generation agent's `run_forever()` continuously reconciles the DB backlog, retrieves stored evidence/chunks, and processes generation jobs. Generation still requires three registered, rights-documented image candidates.
5. The reviewer agent's `run_forever()` continuously processes review jobs. Deterministic evidence/source checks run first; the independent model review follows. Missing image previews are a non-blocking note under the current minimum review policy.
6. The stored dataset itself must meet the ERD contract. No separate projection job is needed; the current staging schema still needs save-time alignment and authoritative topic/subtopic/entity ID sources.

The role loops were not started as part of this code change. The current source registry has one non-executable draft and zero approved sources; bounded exact-host Firecrawl search/read are available, but redirect attestation and structured Wikidata parsing are not implemented. Recurring plans remain empty, and the SQLite schema remains unapplied.
