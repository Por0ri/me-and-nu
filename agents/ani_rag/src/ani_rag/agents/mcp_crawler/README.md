# MCP crawler agent

This folder owns the collection role. It routes one bounded request through either the URL search/read adapter or the structured entity/property adapter. It never performs broad web crawling or writes generated content.

- `agent.py`: one-item URL and structured collection entry points.
- `ani_rag.collector` / `ani_rag.structured_mcp`: RAG evidence normalization and source/tool/host checks.
- Approved sources and exact MCP tool bindings are required before collection. No source is currently approved and concrete runtime adapters are still required.
- `MCPCrawlerAgent.run_forever()` polls the collection queue and can periodically enqueue operator-supplied source plans and persisted broad-web plans. Broad-web plans are registered explicitly with `register_recurring_web_discovery_plan()`; runtime composition does not add or enable a plan.
- The plan tables are empty by default. No general web crawl or source/query plan is inferred from the candidate inventory. Broad-web plans need `migrations/0005_recurring_web_discovery_plan.sql` applied before registration or scheduling.
- A broad-web plan stores its interval and next run time in the RAG database. Due runs create idempotent queue jobs; missed intervals are skipped rather than emitted as a burst after downtime. Disable a plan with `disable_recurring_web_discovery_plan()`.
