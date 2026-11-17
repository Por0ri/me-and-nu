# ERD dataset mapping for ani_rag

Updated 2026-11-08 from the ERD screenshots supplied in this conversation. The screenshot is the current contract when it differs from the older `ani_me;nu/docs/ERD_CONTENT_REVIEW.md`. SQL is a temporary relational design here; this review concerns dataset entities, fields, and relationships, not the production database engine.

## Mapping

| ERD table | ani_rag representation | Alignment and remaining gap |
|---|---|---|
| `source_site` | `rag_source` | Carries `source_site_id`, source identity, optional mapped `topic_id`, supported `content_types`, media name, kind, collection method, site/feed URLs, grade, copyright/license basis, collection state/time/error. `source_id` remains the stable local allowlist key. No source is approved or seeded by this change. |
| `raw_document` | `rag_document` plus `rag_excerpt`, `rag_work_link` | Carries raw document ID, both source-site surrogate and stable local source key, optional event link, external ID, original URL/title/text/payload, hash, published/fetched dates, parse status/error and purge time. RAG adds evidence type, content lane, language, canonical URL, claims and temporal context. Publisher is retained in metadata and materialized on the content-source relation. |
| `content` | `rag_content_draft` | Draft is a separate pre-publication lifecycle record; it preserves the legacy free-text `topic` as `topic_label`, alongside nullable ERD `topic_id`, type, title/body/summary/keywords, thumbnail, sponsorship/change flags, judgment basis, optional event, publication/validity dates, status and nullable `embedding_json`. If supplied, the contract checks exactly 1,024 finite values. No embedding is generated. It does not claim to be a published `content_id`; a promotion/ID policy is still needed. |
| `content_source` | `rag_content_source` | Links each draft to raw-document and source-site surrogate IDs, publisher, publication date and primary-source flag. The authoritative evidence remains the linked raw document. |
| `content_tag` | `rag_content_tag` | Stores subtopic, tagger and time. Subtopic IDs must come from the approved topic/subtopic catalog; none are minted here. |
| `content_card` | `rag_content_card` | Stores page number, role, title/body and card/source image/page URLs, rights status and credit. Image asset registration remains a separate provenance record. |
| `entity` | `rag_entity` | ERD-shaped local entity table is present for an independently provisioned RAG dataset. It is not populated from work titles automatically. Existing `menu_work_id` is a work-catalog ID and is not equivalent to ERD `entity_id`. |
| `content_entity_link` | `rag_content_entity_link` | Stores entity ID, link method, creation time and order. Only explicit entity IDs can be written; there is no automatic conversion from the work snapshot. |
| `entity_relation` | `rag_entity_relation` | ERD relationship shape is represented, but no relationship inference/import is implemented. |
| `judgment_log` | `rag_judgment_log` and `rag_review_history` | Review results are recorded with reviewer/agent, review stage, attempt, verdict, result and time. Prompt/model/confidence/token fields remain null until real model execution supplies them. Generation judgments are not yet produced. |
| topic/subtopic catalog | Not supplied in the screenshot or current ani_rag snapshot | `topic_id`/`subtopic_id` are external dataset identities. No IDs are guessed. Evidence and draft generation retain the existing `content_type`; numeric topic/subtopic values are needed at the ERD projection boundary where those foreign keys are required. |

## Seven RAG lanes and evidence handling

`character`, `guide`, `people`, `music`, `humor`, `curation`, and `review` remain the RAG collection lanes. Evidence documents preserve original title and text or excerpt, original URL, source ID, fetched time, optional publication time, evidence type, work-link status, collection method, original language, raw payload, parse state, and content hash. Opinion and community signals keep their evidence type and attribution; curation context keeps its survey window, sample size, aggregation method and source context. Music/people publication dates remain separate temporal fields.

The local work snapshot is referenced in place. The latest read-only inventory contained 2,351 works, 3,889 work entries, 72 external entries, 4 verified aliases and 47,611 character rows; all entry/external-entry/verified-alias work IDs resolved. Twelve normalized title collisions remain ambiguous by design. The snapshot manifest hashes matched. An older review document's 2,322 works and 3,887 entries are stale counts.

## Deliberate non-mappings and next data decisions

- `menu_work_id` stays in RAG work-link/retrieval fields for the existing work catalog. It must not be stored as `entity_id` until the dataset defines a checked mapping.
- `topic_id` and `subtopic_id` are ERD lookup values. Staging can preserve topic labels and content types without them; resolve the numeric foreign keys only at ERD materialization when required.
- Evergreen content leaves `event_detail_id` null unless a real event relation is provided. Event dates, when present in source material, remain on the evidence document's temporal fields.
- The ERD's 1024-dimension content vector is not generated or indexed. The temporary schema only reserves nullable `embedding_json`; its representation, dimension validation and retrieval lifecycle need a later implementation decision.
- Source candidates are not source approvals. The evergreen registry has one non-executable `frieren_official` draft and zero approved sources; source grades, licensing and host/tool permissions are not inferred from candidate URLs.
- `raw_document_id`, `source_site_id`, `content_id` and `entity_id` have distinct roles. Local draft/document IDs cannot be silently treated as published ERD IDs.

## Existing recovery dataset review (read-only, 2026-11-08)

The user-designated `ani_me;nu/data/generation_continuous_recovery_20261101.sqlite3` was opened with SQLite `mode=ro` and `PRAGMA query_only=ON` solely to inspect schema and aggregate structure. JSON payload strings were parsed in memory to extract field names and aggregate distributions. Body/generated-copy values were not printed or semantically reviewed; image blob bytes, log contents, and `.env` contents were not read. No write statement, schema change, or migration was run.

| Existing table | Rows | ERD/RAG interpretation |
|---|---:|---|
| `content_types` | 10 | String `type_code` lookup. It includes all seven RAG types plus `news`, `event`, and `goods`. |
| `content_drafts` | 146 | Draft lifecycle records with `topic` text, `content_type`, `content_kind`, status, dates, event fields and a JSON package. RAG content types include 5 character drafts (3 ready for feed, 2 needing crawler updates). |
| `content_cards` | 717 | Card/page data keyed by request and page; fields include role, title/body JSON, fact IDs and image ID. |
| `content_work_links` | 146 | Existing `menu_work_id`, order and primary marker. Its 98 distinct IDs include 96 present in the local snapshot and 2 unresolved IDs (`2403`, `2416`). Preserve unresolved state; do not remap by title. |
| `agent_records` | 28,433 | Generic JSON records for facts, source metadata, official-page captures, candidate attempts, character birthday references and image metadata. This is not a normalized `raw_document` table. |
| `feed_queue` | 77 | Downstream-ready records; not the evidence or content source of truth. |
| `rejected_archive` | 24 | Archived rejections and reasons; not an independent `judgment_log` table. |
| `image_blobs` | 5,165 | Binary image storage. Bytes were not inspected. |

The draft JSON package consistently contains `topic`, `content_type`, `facts`, `sources`, `images`, `cards`, `menu_work_ids`, `work_match_evidence`, `detail_body` and related lifecycle/context fields. Those nested structures provide source, claim, work-link and image provenance that should be normalized into the ERD-shaped RAG tables when materialized. The legacy DB has no tables named `source_site`, `raw_document`, `content_source`, `entity`, `content_entity_link`, `entity_relation`, `content_tag` or `judgment_log`.

All draft-to-card, draft-to-work-link and feed-to-draft request IDs joined in this inspection; all non-null draft `content_type` values were present in `content_types`. One request had a card count outside the usual 3?10 range. The schema declares no foreign keys on these eight tables, so `foreign_key_check` returning zero violations is not proof of enforced referential integrity.

### What the ID fields mean in this dataset

- The current collected relationship key is `menu_work_id`; keep it as-is. Its 96-of-98 snapshot coverage is measurable, with two unresolved values retained as unresolved.
- Legacy `topic` is text, not `topic_id`. The database has no topic/subtopic lookup tables. RAG can preserve the label and `content_type` without inventing numeric IDs.
- The legacy database has no `entity_id`. On ERD projection, a work row can be represented in `entity.external_id` using its existing `menu_work_id`, then connected through the dataset's entity record. Do not copy the integer into `entity_id` unless the target catalog explicitly defines those IDs as the same namespace.
- No numeric topic/subtopic IDs need to be typed into collected evidence. Lookup resolution belongs at the ERD materialization boundary if that target master data exists.

## Static verification boundary

In addition to code/schema and local CSV inspection, the user-designated database above was read only for table metadata, key counts, join coverage, and JSON field names. No database contents were changed, and no schema was applied. JSON field-shape inspection read payload strings in memory but did not display or evaluate generated prose. No MCP call, worker, CLI, migration, or test was run. PostgreSQL choice and temporary SQLite adapter lifecycle remain outside this dataset mapping.

2026-11-08 follow-up verification: the current runtime tool inventory did not include Firecrawl/Wikidata MCP tools. A read-only scan of the period project's docs, config, Python source, and reference CSVs found no topic master or crosswalk from `menu_work_id`/Wikidata character IDs to ERD `entity_id`. Therefore no character source was added to the allowlist; see `MCP_PILOT_PLAN.md` for prerequisites.

## Dataset alignment boundary

No separate ERD conversion stage is required if the dataset saved by the RAG lane already has the ERD's fields and relationships. The current `rag_*` SQLite schema is still a workflow/staging design: for example, it stores `draft_id` rather than the ERD's canonical `content_id`, and its topic, tags, and entity links are nullable or optional. It therefore preserves many ERD fields but does not yet guarantee a complete ERD-shaped content record at save time.

The next data-model change should align the save contract itself with the ERD tables and required IDs. `topic_id`, `subtopic_id`, and `entity_id` must come from authoritative catalogs; `menu_work_id` must not be cast to `entity_id`. The local project currently has no topic/subtopic/entity crosswalk or canonical content ID allocation policy, so these must be resolved before claiming generated rows fully match the ERD. This is a dataset contract decision, not a later conversion job.

## Source inventory cross-check (2026-11-08)

The existing period-project source files were reviewed read-only: `../ani_me;nu/config/sources.json` has 91 records and `source_type_map.json` assigns 17 of them to `character`. The RAG review inventory contains those 17 as candidates (9 `high_candidate`, 8 `review_candidate`). Priority is not approval. Candidate notes distinguish official primary sources, reference catalogs, recurring discovery suitability, source terms, and image-rights limitations.

The local reference has a Frieren work row (`menu_work_id=43`) and Wikidata character identifiers, but those rows are identity matching aids only. They do not supply ERD `entity_id`, topic IDs, or evidence for generated claims. Source files help shortlist a structured identity lead and a work-scoped official verifier; they do not resolve the ERD ID gap or confirm live MCP connectivity. See `MCP_PILOT_PLAN.md` for the candidate-by-candidate pilot constraints.

### Detailed source-table and character data mapping

The legacy `agent_records` composite key is `(agent_type, record_id)` and each row carries `record_kind`, `request_id`, free-text `topic`, `region`, lifecycle dates/status, and `payload_json`. It is an operational envelope, not an ERD entity table. Its current content-relevant records include 9,636 `information/fact` records, 1,810 `information/official_page_capture` records, 980 `information/source` records, 3,727 discovery candidates, and 366 `character/character_birthday_reference` records (164 marked matched, 202 unresolved). These are not interchangeable:

- `official_page_capture` is a candidate raw-document input with title, URL, text, fetched/published times, and source IDs; its capture status must be preserved, and an unverified capture is not a verified fact.
- `information/fact` has `statement`, `details`, `fact_type`, `source_ids`, `verified`, and `time_sensitive`. Map the source passages into raw documents/excerpts and the fact statements into claim/fact records linked to those sources; never make an unsupported statement its own source.
- `character_birthday_reference` has character name, birthday, source URL/ID, and work match status/IDs. Keep unresolved entries unlinked until verified; use the source record as evidence and the name/QID only for identity matching.
- `content_drafts.package_json.sources` is the current content-to-source association envelope. Normalize each citation into `content_source` and preserve the referenced raw document/source IDs; do not flatten all source text into the content body.
- `content_work_links` already provides a relational work link with order/primary flag. Its `menu_work_id` values remain the authoritative current work IDs. Populate `content_entity_link` only through the matching entity row/crosswalk; otherwise retain the work link as unresolved staging data.
- Image metadata in `agent_records` contains rights status/basis/scope, evidence URL, source page and attribution. Match those fields into `content_card`/image provenance; `image_blobs` stores bytes only and was not read.

The source record list/config supplies stable source IDs and collection/role policy; the DB's `information/source` records are observed source data and should not replace the configured source master. `event_start`, `event_end`, location/country/region currently live on content drafts; map them to `event_detail` only when an event relationship exists, otherwise keep them as source metadata during staging.

### Legacy-to-ERD field crosswalk

| Recovery DB field | ERD-shaped RAG target | Mapping rule |
|---|---|---|
| `content_drafts.content_type` | `content.content_type` | Keep the existing string code (`character`, `guide`, `people`, `music`, `humor`, `curation`, `review`, or period type). Never replace it with a guessed numeric ID. |
| `content_drafts.topic` | staging `topic_label`; eventual `content.topic_id` lookup | Preserve the text exactly as context. Resolve through the topic master only at target materialization, if that master is available. |
| `content_drafts.package_json.detail_body` | `content.body` | Copy as the detail text while retaining claim-to-source links separately. |
| `package_json.facts[]` | factual claim records / `ai_judgment_basis` | Keep fact type, statement, detail, verified flag and time sensitivity; require supporting source IDs/excerpts. |
| `package_json.sources[]` | `content_source` | Keep source ID, URL, publisher, role, publication date and primary marker; connect each to the stored raw document. |
| `package_json.cards[]` / `content_cards` | `content_card` | `page`→`page_no`, `role`→`card_role`, title/body arrays→readable card text, `image_id`→registered image provenance. Preserve original arrays if exact rendering reconstruction is needed. |
| `package_json.images[]` / image metadata records | card image provenance fields | Map source/page URL, credit, rights status/basis/scope and evidence URL. A stored image blob alone does not prove reuse rights. |
| `content_work_links.menu_work_id` / `package_json.menu_work_ids[]` | entity external-ID + `content_entity_link` after entity resolution | Preserve existing work ID and order/primary data. Resolve to an entity row via the work catalog; do not cast the value to `entity_id`. |
| `content_drafts.event_*` | `event_detail` | Materialize only for an actual event-linked record; evergreen content keeps no event relation. |
| `quality_report_json`, review status, `rejected_archive.reasons_json` | `judgment_log` / review archive | Preserve existing report/status. Do not invent per-attempt judge logs when the legacy data has only an aggregate quality report. |

`request_id` identifies the legacy generation request, not necessarily an ERD `content_id`; keep it as provenance during any import. The current `feed_queue` is a downstream queue and should not be imported as source evidence.
