# MCP pilot plan — character evidence

Status: project-local Streamable HTTP aliases are configured. Firecrawl live tools, one exact-host search, and a single-page read are verified; no source is approved and the collector still rejects the response because redirects are unattested.

## Why two servers

Use two servers with distinct responsibilities rather than one server per source:

1. **Wikidata MCP** for structured entity discovery and statement/reference inspection.
2. **Firecrawl MCP** for locating a page when needed and reading a known public page.

The project-local aliases `wikidata` and `firecrawl` are configured in `config/mcp_servers.json` and have returned their live tool lists through `StreamableHTTPMCPClient`. This does not install the separate ChatGPT Firecrawl app integration.

## Pilot source candidates

| Existing source ID | Existing source role | Existing URL | Proposed MCP role | Verification state |
|---|---|---|---|---|
| `wikidata_character_catalog` | `reference`, `api`, legacy status `active`, type `character` | `https://query.wikidata.org/sparql` | `wikidata` MCP: `search_items`, then `get_statement_values` to inspect detailed qualifiers/references | Project alias configured; live tools confirmed; no statement query made |
| `frieren_official` | `primary`, `html`, legacy status `active`, types include `character` | `https://frieren-anime.jp/character/` (bounded pilot page; candidate inventory's news URL remains unchanged) | `firecrawl` MCP: exact-host `firecrawl_search` and one-page `firecrawl_scrape` | Project alias/tool verified, one search and one read; redirect chain absent, source remains draft |

The existing `frieren_official` note says it is a work-scoped reference source, not a recurring candidate-discovery feed. Tie a read to a known relevant item. Do not use it for broad site discovery. Wikidata results identify candidates; they do not establish official facts by themselves. Confirm relevant claims in the official source and preserve both sources distinctly.

## Tool scope

- Firecrawl keyless mode currently exposes `firecrawl_search`, `firecrawl_scrape`, and `firecrawl_parse`. The project pilot adapter enables bounded host-filtered `firecrawl_search` and single-page `firecrawl_scrape`. Do not use `firecrawl_crawl`, `firecrawl_map`, `firecrawl_agent`, monitoring, or batch traversal tools.
- Wikidata MCP exposes `search_items`, `get_statements`, and `get_statement_values` among its tools. Use `get_statement_values` when statement qualifiers and references matter. Treat search results as identity leads and keep the item URL/QID with retrieved statements.
- `ani_rag` requires exact `allowed_hosts`, exact `tool_bindings`, and a live tool-inventory match for URL-based search/read. `mcp_transport.py` provides a project-local Streamable HTTP client; `firecrawl_adapter.py` supports bounded `firecrawl_search` with exact-host filtering and one-page reads. Wikidata's entity/property flow remains a separate structured contract (`StructuredMCPAdapter`); do not treat `search_items`/`get_statement_values` as URL scraping. Structured Wikidata parsing is not implemented.
- Start with one character identity and one supporting official page. Keep unresolved/ambiguous work links unlinked. Do not create a work ID.

## Approval gate

Keep source records at `draft` until the source/tool identity, host, redirect targets, final URL, and evidence are confirmed through the project adapter. `config/sources.evergreen.json` currently has one non-executable `frieren_official` draft; do not change it to `approved` while redirect verification is unavailable. The candidate inventory remains non-executable.

## Official setup references

- [Firecrawl MCP — Get Started](https://docs.firecrawl.dev/mcp-server)
- [Firecrawl MCP — Tools and availability](https://docs.firecrawl.dev/mcp-server/tools)
- [Wikidata MCP](https://www.wikidata.org/wiki/Wikidata:MCP)

## 2026-11-08 pilot readiness check (before live endpoint inspection)

**Historical readiness snapshot, superseded by the 2026-11-08 connection update below.** At that point, no runtime aliases were configured and the source registry was empty.

The source project `docs`, `config`, Python source, and reference CSVs were checked without opening databases, logs, generated outputs, or `.env`. No topic master/ID map was found. The local reference catalog supplies `menu_work_id` and Wikidata character IDs, but it does not supply the ERD's `entity_id` or its topic assignment. These IDs must not be substituted for one another.

The remaining runtime gate is an actually configured MCP server in the ani_rag runtime and its source-bound adapter. Do not stop evidence collection or draft generation just because numeric topic/entity foreign keys are not already present: preserve the current `content_type`, free-text topic label, and `menu_work_id` during staging. Resolve them to target ERD foreign keys only when materializing ERD rows. Then register only the verified pilot source, keeping official source evidence distinct from Wikidata identity leads.

## 2026-11-08 live endpoint tool inventory

The two public MCP endpoints were contacted directly for MCP `initialize` and `tools/list`; both negotiated protocol version `2025-03-26`. A later bounded Firecrawl search restricted to `frieren-anime.jp` returned three in-host results, followed by one read of the official character index. No broad search, Wikidata statement call, or crawl was made.

| MCP endpoint | Live tool list | Use in the pilot |
|---|---|---|
| `https://mcp.firecrawl.dev/v2/mcp` (keyless surface) | `firecrawl_search`, `firecrawl_scrape`, `firecrawl_parse` | Bind `search` to `firecrawl_search` and `read` to `firecrawl_scrape`; omit parse/crawl/map from the initial lane. |
| `https://wd-mcp.wmcloud.org/mcp/` | `search_items`, `search_properties`, `get_statements`, `get_statement_values`, `get_instance_and_subclass_hierarchy`, `execute_sparql` | Bind structured `search` to `search_items` and `read` to `get_statement_values`; use `search_properties` to discover a property before requesting it. |

The project now has local aliases and a Streamable HTTP transport. Both servers returned their tools through that transport. A single query restricted by `includeDomains=["frieren-anime.jp"]` returned three character-site results, and the adapter retained only URLs whose exact host matched the source allowlist. The bounded character read is documented below. Firecrawl did not expose redirect hops; the draft cannot yet pass the collector's redirect contract.

## 2026-11-08 bounded official-source read

One `firecrawl_search` query used `includeDomains=["frieren-anime.jp"]` and a result limit of 3. It returned three URLs, all on that exact host; the adapter independently filtered result hosts. One `firecrawl_scrape` call then read `https://frieren-anime.jp/character/`. The returned page metadata identifies the official Frieren anime site; the character index contained Japanese character labels and profile links all on `frieren-anime.jp`. The result's canonical `og:url` was `https://frieren-anime.jp/character/chara_group1/`, and the tool did not expose a redirect chain or an independently attested final URL. This confirms source relevance and host consistency for bounded search/read, but does not satisfy the project's redirect/final-URL contract. The source remains `draft`; no content evidence was persisted by the project and no worker or crawl ran.

## Review of the existing source files

Read-only cross-check completed 2026-11-08:

- `../ani_me;nu/config/sources.json` contains 91 source records. `../ani_me;nu/config/source_type_map.json` maps 17 of them to `character`.
- `config/sources.candidates.json` contains those 17 as RAG review candidates: 9 marked `high_candidate`, 8 marked `review_candidate`. These are priority labels only, not runtime approvals.
- Best split for the pilot remains one structured identity lead plus one official source for independent verification: `wikidata_character_catalog` (reference role; use as identity lead only) and a work-scoped official source. `frieren_official` is explicitly a per-item official reference, not a recurring discovery feed. `one_piece_official` has a prior note that character-index reading was permitted, but its listing was noisy and image reuse is not licensed by that note.
- `kitsu_anime_catalog` is a secondary catalog lead; its character/image relationships do not establish official facts and image rights remain unverified. `onnada` may be community-edited and needs attribution; it should not be used as the sole authority for character facts.
- The current source map resolves content lanes, but neither legacy file supplies the ERD topic/subtopic master or a `menu_work_id` to `entity_id` crosswalk. The bounded search/read confirms page relevance, but missing redirect attestation keeps the draft from approval.

A possible single-work identity example is `menu_work_id=43` (Frieren), with Wikidata IDs present in the reference-only character lookup. Those lookup rows are identity matching aids, not content evidence and not ERD `entity_id` values. Use a matching QID only after a fetched source verifies it, then map to an authoritative entity row.
