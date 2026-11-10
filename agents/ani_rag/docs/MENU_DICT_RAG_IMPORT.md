# menu_dict snapshot in RAG

## What was inspected

`C:\Users\yunju\Downloads\menu_dict.dump` is a PostgreSQL 17 custom-format archive, not an SQLite file. It was inspected read-only with a temporary archive reader; it was not restored to PostgreSQL or copied into the RAG database.

- Archive SHA-256: `e2219a0ca8e1e7bcf52def135228eb1f421d8aa0aa885bc46e9ecdae775e66a5`
- Archive reader: optional `pgdumplib` 4.x dependency, installed only under the OS temporary directory for inspection. The library supports reading PostgreSQL custom-format archives ([project package page](https://pypi.org/project/pgdumplib/4.0.0/)).

The archive contains 94,481 entities and 988,883 fact rows across `dict.entity`, `dict.alias`, `dict.external_id`, `dict.source`, and `dict.fact`. Entity kinds are work (2,322), entry (3,887), character (53,411), person (24,360), song (9,425), and studio (1,076). Entry facts can be linked to a work through the explicit entry parent. Character/person/song/studio links can be derived only from explicit `value_entity_id` relationships on entry facts. The archive work IDs cover 1–2,322; they must not overwrite the newer 2,351-row local RAG work catalog.

The teammate CSV ZIP is only a convenience export: it includes 754 sampled facts, not the 988,883-row fact corpus. The full dump has 4,664 fact values longer than 1,200 characters and 477 longer than 10,000 characters, so source-unit chunking and long-text bounds are required.

## Import policy

`config/menu_dict_import_policy.json` is separate from the MCP source allowlist. It enables snapshot import for AniList, Wikidata, KMRB, MADB, English Wikipedia, and Korean Wikipedia. The user confirmed that the team obtained AniList permission for bulk storage and AI/RAG use of this shared dataset; that confirmation is recorded in the import policy and each imported document's metadata. The original `source.csv` license note is also preserved, so the permission record does not erase provenance. The importer preserves each source ID, URL, fetch time, source terms, archive hash, fact ID, and entity ID. Wikipedia text remains attributed and paraphrased; its share-alike terms are recorded. Imported source rows are marked `snapshot_import_only` with MCP collection disabled.

AnimeThemes, Syoboi, MusicBrainz, MAL, Kitsu, LiveChart, Onnada, and other restricted or unverified sources remain excluded by default according to the supplied `source.csv` notes. `recommendation`, ranking, popularity, favourites, social URLs, and adult-content fields are excluded from factual prose. These fields do not currently carry enough survey/sample context to become `curation` or `humor` evidence. The archive does not provide review authors/claims, curation windows/sample methodology, or community-humor context, so it cannot supply `review`, `curation`, or `humor` evidence by itself.

Expected useful lanes are `character`, `guide`, and `people`, with some `music` facts. AniList contains rich character descriptions and work synopsis text; those can enter RAG under the team's confirmed permission, while factual claims still retain source/chunk links. Music, humor, curation, and review coverage should be judged separately from actual eligible field counts; this dump has no substantiated review corpus or poll/community context.

## Code and run boundary

- `src/ani_rag/menu_dict_archive.py` reads selected source facts as evidence documents and preserves one fact locator per document.
- `src/ani_rag/storage/menu_dict_import.py` writes source/document/excerpt/work-link rows into an already-provisioned RAG DB, without applying schema or creating generation jobs.
- `scripts/import_menu_dict_archive.py` requires an existing RAG SQLite file and an explicit `--confirm-import` flag. It is idempotent and must be run only while workers using that DB are stopped.
- Imported evidence is searchable by the existing RAG retrieval path after persistence. Content generation remains a separate explicit queue action; bulk import does not queue hundreds of thousands of drafts.

No import has been run. The current crawler/generator/reviewer processes are still running, so the live RAG database was deliberately left untouched. The next application step is to stop those workers, confirm the selected RAG DB path, and invoke the importer against that already-provisioned database. No tests, worker, MCP call, schema migration, or generation job was run for this import preparation.
