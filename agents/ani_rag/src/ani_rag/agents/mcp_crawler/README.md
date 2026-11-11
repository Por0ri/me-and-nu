# MCP crawler agent

This folder owns the collection role. It routes one bounded request through either the URL search/read adapter or the structured entity/property adapter. It never performs broad web crawling or writes generated content.

- `agent.py`: one-item URL and structured collection entry points.
- `ani_rag.collector` / `ani_rag.structured_mcp`: RAG evidence normalization and source/tool/host checks.
- Approved sources and exact MCP tool bindings are required before collection. No source is currently approved and concrete runtime adapters are still required.
- `MCPCrawlerAgent.run_forever()` polls the collection queue and can periodically enqueue operator-supplied `RecurringCollectionPlan` queries. Plans are rejected unless their source is approved for search/read and the requested content type.
- The recurring plan list is empty by default. No general web crawl or source/query plan is inferred from the candidate inventory.
