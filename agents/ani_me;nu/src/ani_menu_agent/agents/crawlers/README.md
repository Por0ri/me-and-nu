# Type crawler roles

The eleven files beside `base.py` are intentionally small role declarations, not eleven duplicated crawling engines. Each class defines its content type, allowed source roles, purpose, image-capture policy, and multilingual lexical signals. `base.py` owns the shared LangGraph workflow: source-registry allowlisting, adapter checks, discovery collection, candidate validation, deduplication, persistence, and optional same-page image capture.

## End-to-end route

`crawl-type --content-type <type>` runs the matching crawler against only sources mapped to that type. The crawler persists discovery candidates. The CLI then sends each candidate through `crawl-generate`, which rechecks source eligibility and work matching. If the candidate URL is already on a registered active primary source, it captures that page directly. If it is on a registered active HTML discovery publisher, it captures the article only as discovery context, resolves a uniquely identifiable type-mapped active primary link from the article body, and captures that original page as the factual evidence. It never treats discovery copy as primary facts. If no unique approved original page can be resolved, the candidate is retained and reported as `original_source_unresolved`; it does not reach generation. The final source record keeps both the evidence URL and `discovered_via_source_id`/`discovery_url` provenance. Community humor uses the separate trend-only path and records discussion signals as unverified opinion/context, never as established facts. Both paths continue to use the existing generation and independent-review agents.

## Signal policy

`required_signals` are lexical hints, not fact verification and not a substitute for the independent reviewer. A zero-match page with a captured source and resolved work may proceed to draft generation; the result is marked `needs_semantic_review` and must still pass the independent content-type, evidence, image, translation, and editorial-quality checks before entering the feed. Missing source text, unresolved work identity, safety failures, and type-specific factual requirements remain hard stops. Humor keeps its trend-source, work-match, and completeness requirements.

Signals are deliberately broad and multilingual. A zero-match result can indicate either a genuinely mismatched page or a missing vocabulary variant. The raw capture, source URL, requested type, and per-signal booleans are preserved for diagnosis. Discovery results are ranked by source and, where the headline resolves unambiguously, interleaved by work; this is a soft ordering preference, not an equal-allocation quota.

## Maintenance and tests

- Keep the eleven role declarations aligned with `CONTENT_TYPE_LABELS` and `config/source_type_map.json`.
- Do not add a source to a role from this package; source approval and type mapping live in the source registry/configuration.
- Keep community-trend content unverified and separate from official-fact types.
- Test each profile's multilingual signals and the complete CLI path, including zero-signal advisory routing and raw-capture persistence.
- Changes to source/work eligibility or reviewer thresholds belong in their existing shared modules and must not be duplicated here.
