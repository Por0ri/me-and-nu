# ANN Encyclopedia as the core RAG reference source

## Decision

Use Anime News Network Encyclopedia as the first reusable reference source for work-scoped RAG collection. Search one locally identified work at a time and preserve each encyclopedia page as its own raw evidence document. The current allowlist scope remains exact-host and `/encyclopedia/` path only.

Use encyclopedia facts for `character`, `guide`, `people`, and `music` only when the specific entry directly supports the requested format. Useful entry families include cast/staff credits, episode and season records, work details, and theme-song records. A page's record counts, ratings, and popularity metadata are not review or community-consensus evidence.

## Supplement and verification

Use a work's official source when the ANN entry lacks a needed fact, a date or identity is disputed, or a consequential claim needs primary confirmation. Treat ANN as a strong secondary reference, not as a universal truth oracle. Preserve both records and attribute each claim to the document that supports it; do not silently replace the ANN fact with an inferred value.

This is a source-selection policy, not an automatic fallback router. Collection requests still name an approved `source_id`; workers do not automatically switch from ANN to an official source. Each official domain must be separately reviewed and allowlisted before it can be read.

## Exclusions and evidence handling

- This approval does not cover ANN news, reviews, interviews, features, interest feeds, page sidebars, or ANN-hosted images.
- Keep opinions, polls, and community reactions in their own evidence types; do not relabel them as encyclopedia facts.
- Store the original title/body or bounded source text, exact URL and ANN native entry ID, source ID, fetch time, available publication/update dates, original language, evidence type, and resolved local work link.
- The current lexical RAG score threshold (`0.20`) measures query/chunk word overlap only. It does not measure source trust or establish factual correctness.

## Runtime state

`ann_encyclopedia` is approved in `config/sources.evergreen.json` for `character`, `guide`, `people`, and `music`. Its `is_collecting` flag remains false; this decision does not start recurring collection, a worker, or an MCP request.
