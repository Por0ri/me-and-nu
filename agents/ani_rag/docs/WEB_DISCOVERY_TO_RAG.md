# Web discovery and trusted-reference RAG gate

Broad-web discovery is a candidate-finding lane, not a factual source registry and not a site crawler.

```text
bounded web search (up to 5 results)
  → read one result page on that exact HTTPS host
  → confirm the candidate page matches the search work from the official or temporary catalog
  → retrieve `ann_encyclopedia` and imported `anilist` documents for the work/profile
  → require at least one RAG chunk at or above the configured relevance floor
  → enqueue ordinary content generation with the same ANN/AniList retrieval scope
  → independently review the generated draft against its stored evidence and images
```

The search result and candidate page are untrusted discovery context. They are not saved as factual evidence, are not passed to the writer as corroboration, and do not approve their own claims. Candidate title/body text is used only to form a retrieval query. Generation receives the RAG evidence selected by the normal retriever. The candidate URL, host, fetch time, relevance score, and corroborating RAG document/chunk IDs are retained as generation audit metadata.

By default the pre-generation threshold uses lexical overlap. If runtime composition receives the completed vector sidecar and a matching query embedder, it uses cosine similarity instead. Neither score is a truth probability or quality grade, and their scales are not calibrated as interchangeable. Passing only means the selected ANN/AniList RAG corpus produced at least one sufficiently relevant chunk. The independent post-generation reviewer remains responsible for checking the draft against its cited stored evidence and image/copy fit.

`RetrievalRequest.source_ids` is an optional source filter. Empty means the existing general RAG behavior (all eligible sources); the web-candidate path explicitly sets `("ann_encyclopedia", "anilist")` and carries that constraint through review-triggered revisions. AniList documents are the imported, policy-approved snapshot facts with original source URLs and work/entity provenance; their MCP collection flag remains disabled. If neither source has eligible indexed evidence for the requested work/content profile, the candidate is blocked rather than silently falling back to other sources.

The path is wired but was not run as a worker here. The separate RAG DB contains imported AniList snapshot facts plus the approved ANN evidence; both can support this pre-generation gate. Vector mode is opt-in at runtime composition and has not been configured for a live worker. Firecrawl credentials/live tools and worker supervision remain runtime prerequisites. Existing source collection flags remain unchanged.
