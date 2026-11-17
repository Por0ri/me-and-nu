# ani_rag source shortlist

Read-only cross-check completed 2026-11-08 against `../ani_me;nu/config/sources.json`, `source_type_map.json`, and `config/sources.candidates.json`.

## What already exists

- The legacy source registry has 91 records. Its type map covers ten content types; `character`, `guide`, `people`, `music`, `humor`, `curation`, and `review` are already the seven RAG lanes.
- The RAG candidate inventory has 53 records tagged to those seven types. Per-type counts below overlap because one source may support multiple types.
- Candidate priority and the legacy `active` status are not RAG approval. The candidate inventory is not an allowlist. `ann_encyclopedia` and `wikidata_character_catalog` have bounded live checks and narrow approvals described below; the remaining candidates still require source-specific tool and evidence review.
- `sources.evergreen.json` contains seven narrowly approved, non-collecting records (`ann_encyclopedia`, `wikidata_character_catalog`, `kitsu_anime_catalog`, `flyingdog_music`, `anime_trending_polls`, `anime_corner`, `anime_corner_interviews`) and one non-executable `draft` record (`frieren_official`). Project aliases and a Streamable HTTP client are configured in `config/mcp_servers.json` and `src/ani_rag/mcp_transport.py`. ANN is restricted to `www.animenewsnetwork.com`; reads pass only on exact no-redirect URL equality or a complete in-host redirect chain.

## Review order: reusable sources first

This is a manual review queue, not an approval list. The order favors sources that cover multiple works or have a reusable catalog, archive, API, or publisher feed. It does not mean one source can support every evidence type. Existing verification notes below come from the candidate inventory; MCP tool/host binding and approval still need separate verification.

| Order | Candidate(s) | Main RAG types | Why review in this order | Evidence boundary |
|---:|---|---|---|---|
| 1 | `ann_encyclopedia` | guide, people | Multi-work title search and linked entries have been verified. | Reference metadata; confirm important facts against primary sources. |
| 2 | `wikidata_character_catalog` | character | Live structured MCP lookup verified for one work, including detailed references. | Identity/relationship lead only; pilot references were Wikipedia imports, so verify facts elsewhere. Runtime adapter remains unimplemented. |
| 3 | `kitsu_anime_catalog` | guide, character | Live title-filtered JSON:API lookup returned a matching anime ID and character relationships. | Catalog and character-image candidates are linked to matched work IDs; synopsis attribution may point to MAL Rewrite; no curation. Runtime adapter remains unimplemented. |
| 4 | `wikipedia_ja_anime` | guide | Reusable Japanese-language MediaWiki search/read API. | One relevant page at a time; follow and preserve cited original sources. |
| 5 | `wikipedia_en_anime` | guide | Reusable English-language MediaWiki search/read API. | Same boundary as the Japanese edition; secondary reference only. |
| 6 | `prtimes_rss` → `prtimes_release_pages` | people, music, humor | Broad publisher feed plus linked release pages supports a discovery-to-original flow. | PR TIMES is primary only for the issuing organization’s own announcement. |
| 7 | `anime_corner_interviews` | people, humor | Multi-work interview archive with attributable statements. | Approved for attributed people statements and firsthand production anecdotes; exclude community opinions as facts. |
| 8 | `anime_feminist_interviews` | people | Multi-work interview archive. | Treat interview statements as attributed claims, not official facts. |
| 9 | `anime_news_network` | review | Reusable editorial review section; low-rate collection is documented. | Store critic, date, opinion, and citations separately; do not copy review prose. |
| 10 | `anime_feminist_reviews` | review | Reusable review archive; path and article listing have been checked. | Opinion source, not factual authority. |
| 11 | `anime_trending_polls` | curation | Reusable first-party poll results are documented. | Preserve period and sample; results are not general fandom consensus. |
| 12 | `filmarks_anime` | curation | Multi-work catalog pages expose aggregate ratings. | Keep aggregate and timestamp; do not collect user reviews or identities. |
| 13 | `flyingdog_music`, `pony_canyon_music`, `king_records_music` | music, people | Label-wide catalogs and release announcements span multiple works. | Verify anime tie-in and release date; preserve returned images with source/work provenance; do not collect audio or lyrics. FlyingDog passed a bounded Firecrawl search/read check on 2026-11-09 and is approved but remains non-collecting. |
| 14 | `anime_corner` | review | Multi-work editorial review archive. | Narrowly approved for attributed review opinions only; curation/polls remain unapproved. |
| 15 | `anime_corner_news` | people, humor | General anime news archive can lead to multiple works and original announcements. | Held: bounded search returned interview/category pages instead of the configured news archive. |
| 16 | `recal_music` | music | Broad Korean anime music/concert discovery source. | Discovery only for time-sensitive events; confirm details with organizer/ticket source. |

### Later: work-specific official sites

Review these when the relevant work is being researched, after the reusable sources above. They are valuable primary sources but cover a limited title set and should not drive broad recurring discovery.

`frieren_official`, `one_piece_official`, `jujutsu_kaisen_official`, `natsume_official`, `tv_tokyo_anime_official`, `haikyu_official`, `spy_family_official`, `the_fable_anime_official`, `my_hero_academia_official`, `kimetsu_official`, `bn_pictures_keroro_official`, `naruto_official`, `bleach_anime_official`, `attack_on_titan_official`.

For any selected work-specific source, use a direct work page to confirm claims already discovered elsewhere. Do not treat it as a feed for other works. `tv_tokyo_anime_official` is a program directory, not a latest-news feed.

### Separate review queues

- `onnada` may be community-edited; assess attribution and corroboration before using profile facts.
- `ruliweb_anime_info`, `reddit_anime`, `theqoo_anime_trend`, `arca_live_anime_trend`, `fmkorea_anime_trend`, and the three Mastodon candidates are community signals only, not factual evidence. Review access, retention, and policy constraints separately.
- Keep the AniList GraphQL API, `crunchyroll_announcements`, and `myanimelist_news` on hold as MCP sources until tool scope and collection use are reviewed. The team's permission for the local menu_dict snapshot import is separate and does not approve ongoing AniList API collection.
- Keep `shueisha_catalog` and `kodansha_catalog` on hold until a relevant multi-work search path is identified; current root listings were not useful anime catalogs.
- Keep `muse_asia_youtube` and `ani_one_asia_youtube` on hold until channel identity and regional scope are confirmed; `kadokawa_group_official` needs an image-host alternative; `dcinside_anime_trend` requires prior written permission; `animenomics` is later-phase.

## Per-type evidence boundaries

| RAG type | Candidate count | Preferred reusable starting points | Boundary to keep |
|---|---:|---|---|
| `character` | 17 | `wikidata_character_catalog`, `kitsu_anime_catalog`, `ann_encyclopedia` | Resolve identity with more than one source; use title-specific official pages only for final confirmation. |
| `guide` | 12 | `ann_encyclopedia`, `kitsu_anime_catalog`, `wikipedia_ja_anime`, `wikipedia_en_anime` | Catalogs and encyclopedias are references; follow citations to primary sources for important claims. |
| `people` | 20 | `anime_corner_interviews`, `anime_feminist_interviews`, `prtimes_rss` → `prtimes_release_pages` | Separate attributed interview statements from announcements and verified facts. |
| `music` | 19 | `flyingdog_music`, `pony_canyon_music`, `king_records_music` | Preserve artist, release date, label, anime tie-in, and source artwork provenance; no audio or lyrics. |
| `humor` | 23 | `anime_corner_news`, interviews, and release announcements | Keep sourced production anecdotes distinct from community signals; do not turn discussion into fact or consensus. |
| `curation` | 5 | `anime_trending_polls`, `filmarks_anime`, `anime_corner` | Preserve period, sample/population, aggregation method, and original context. |
| `review` | 3 | `anime_news_network`, `anime_feminist_reviews`, `anime_corner` | Keep critic, publication date, opinion, and supporting citation distinct. |

## Bounded MCP pilot: `ann_encyclopedia` (2026-11-08)

- Firecrawl MCP endpoint responded with `firecrawl_search`, `firecrawl_scrape`, and `firecrawl_parse` in its tool list.
- One search used the query `Fullmetal Alchemist Anime News Network Encyclopedia`, exact host `www.animenewsnetwork.com`, and result limit 3. It returned two encyclopedia entries and one unrelated feature article. Search results included very large description/body text, so result/snippet size needs a bound before recurring use.
- One encyclopedia entry was read from the returned original URL. The title and English body were extracted, but the body was 405,882 characters; publisher metadata and publication date were absent.
- The linked episode-title page `...?id=10216&page=25` was read directly and returned 14,597 characters with an episode-title heading. This confirms that a paginated URL is readable, but does not yet verify traversal between pages or reliable next/previous-page detection.
- The initial reads returned no redirect chain. Firecrawl's documented scrape response exposes `metadata.sourceURL` and `metadata.url`, but does not document a redirect-chain field ([Scrape API schema](https://docs.firecrawl.dev/api-reference/endpoint/scrape)). The adapter therefore treats exact `sourceURL == url == requested URL` as evidence of a no-redirect read, and accepts a redirect only when a complete in-host chain is supplied. It does not claim to verify undocumented intermediate hops.
- A follow-up direct read of ANN entry `id=14950` returned `sourceURL` and `url` exactly equal to the requested URL; the adapter marked that no-redirect case verified. This is a bounded single-page check, not validation of all ANN URLs. No 429 was observed in the small sample; this does not establish Firecrawl plan limits or a safe recurring rate.
- **Decision: approved with a narrow scope** in `sources.evergreen.json`: `guide` and `people`, host `www.animenewsnetwork.com`, work-ID-seeded lookup, separate raw document per ANN entry, no domain-wide crawl and no image reuse. `is_collecting` remains false and no recurring plan or worker is enabled. Do not use ANN as the sole source of factual claims or treat its images as official assets.

### Work-ID-seeded lookup pilot

- The first local reference record (`menu_work_id=1`) is `진격의 거인` / `Shingeki no Kyojin` / `Attack on Titan`; its snapshot has 16 entries across formats/years.
- One Firecrawl search used `Attack on Titan Shingeki no Kyojin Anime News Network Encyclopedia`, exact host `www.animenewsnetwork.com`, and limit 3. It returned three related ANN encyclopedia pages: `Attack on Titan (TV)`, `Attack on Titan The Final Season (TV)`, and `Attack on Titan (TV 3/2018)`.
- Three returned original pages were read separately: `https://www.animenewsnetwork.com/encyclopedia/anime.php?id=14950` (`Attack on Titan (TV)`, 424,511 characters), `...?id=22277` (`Attack on Titan The Final Season (TV)`, 249,457 characters), and `...?id=19819` (`Attack on Titan (TV 3/2018)`, 165,085 characters). Each contained a distinct page title/body. The initial reads had no redirect chain; a later direct read verified the exact no-redirect metadata equality for id 14950 only.
- The local ID 1 snapshot has separate entries for `Attack on Titan` TV (2013), `Attack on Titan Season 3` TV (2018), and `Attack on Titan Final Season` TV (2020), plus later Final Season parts/specials. That supports linking all three ANN source documents to the same umbrella `menu_work_id=1`, while preserving each ANN page as a distinct raw document and retaining its ANN `id` as `source_native_id` (14950, 22277, 19819). The Final Season ANN page is not being asserted as an exact match to one local sub-entry in this pilot.
- The local exact resolver maps `Attack on Titan` and `Shingeki no Kyojin` to work ID 1, but does **not** exact-match ANN titles with suffixes such as `(TV)` or `The Final Season`. Seeded query context can propose work ID 1, but suffix/season/format variants need candidate resolution and ambiguity handling before an `entity_id` or work link is written.
- This pilot supports trying one bounded query per local work ID using its canonical titles and verified aliases, then reading matching ANN entry pages. It does not prove full coverage of the 16 local entries, ANN search pagination, or coverage of works absent from the local snapshot. The search used `limit=3`; this is a chosen MCP result bound, not evidence of an ANN 3- or 10-result site cap.
- Code now exposes numeric-order work search seeds, carries an optional seed ID into collection identity resolution, asks the LLM fallback to classify entry format/relation separately from the umbrella work ID, and extracts ANN's URL `id` into `source_native_id`. This is a code contract only: no LLM call, repository write, or worker run was performed. ANN approval is now recorded with `is_collecting=false`; it does not authorize unattended or domain-wide collection.

## Readiness and activation gate

These are shortlist entries, not approvals. Before broadening an approved source's scope or enabling recurring collection, verify server/tool identity, exact source host, redirect behavior and final URL, allowed content types, evidence boundaries, rate limits, copyright status, and license basis. For ANN, the present approval is restricted to work-ID-seeded reference lookups; exact request/sourceURL/url equality is accepted only for a no-redirect response. Inspect representative evidence before any scope expansion.

Recurring plans also need a bounded query per source and work/content type, original language, accepted evidence type, interval, and (for structured MCP) verified property IDs. The shortlist alone cannot safely generate these values. In particular, work-scoped official pages must not be turned into broad recurring discovery feeds.

The ANN bounded pilot supports `guide` and `people` reference lookup only. Wikidata remains an identity lead for `character`; the work-scoped `frieren_official` source remains draft. No recurring plans are configured and no collection worker is enabled.

### Anime Trending curation source (2026-11-09)

The Firecrawl MCP inventory returned `firecrawl_search` and `firecrawl_scrape`. One exact-host search returned three chart candidates; a read of `https://www.anitrendz.com/charts/top-anime` returned a 73,060-character body, `sourceURL` and `url` equal to the requested URL, and no redirect. The body displayed `NOVEMBER 7, 2026`, `WEEK 5`, season, ranks, titles, and movements. A separate web rendering displayed November 8 for that chart; retain the raw source date and flag this mismatch rather than normalizing it. The site's charter says charts are released weekly and describes public voter participation. These records are a self-selected community signal; they do not establish general fandom preference or objective quality. The checked chart did not expose respondent count or sample composition, so those fields must stay unknown.

The source is narrowly approved in `config/sources.evergreen.json` for poll/chart content, with `is_collecting=false` and exact content host `www.anitrendz.com`. Page image candidates included `www.anitrendz.com`, `firebasestorage.googleapis.com`, `anitrendz.net`, and `cdn.discordapp.com`; the first three are allowlisted as media hosts, while unrelated Discord widget avatars are excluded. This check does not establish recurring request limits or enable unattended collection.

### Anime Corner review source (2026-11-09)

Firecrawl MCP `tools/list` confirmed the configured search/read tools. A search for `Cyberpunk Edgerunners 2 review Anime Corner Marko Jovanovic` returned the article among three results. The review archive page at `https://animecorner.me/category/review/` returned 4,926 characters and listed the article. Reading `https://animecorner.me/cyberpunk-edgerunners-2-review-great-anime-impossible-expectations/` returned 21,258 characters; requested/source/final URLs were equal with no redirect. The page identifies Marko Jovanovic and October 21, 2026; its first-person judgments and citations can be kept as attributed opinion evidence. It supplied 32 image candidates, all from `static.animecorner.me`.

Only `review` is approved. Search results also included an unrelated announcement and an author archive, so the collector must restrict use to review-category articles before recurring collection is enabled. Curation/poll pages are not approved under this record. Preserve the review author, date, exact article URL, claims, cited supporting material, and image provenance; do not treat the review as factual authority or copy its prose. No recurring request rate was tested, and `is_collecting=false` remains in effect.

### Anime Corner interview source (2026-11-09)

Firecrawl MCP search returned `Interview: Corey Wilder at Anime Central` among three exact-host results. The interview archive at `https://animecorner.me/category/anime-corner/interview/` returned 5,535 characters and listed linked interviews. Reading the exact article `https://animecorner.me/interview-corey-wilder-at-anime-central/` returned 13,463 characters, exact requested/source/final URL equality, author Jay Gibbs, and publication date October 26, 2026. The interviewee describes approved improvisation while recording *New Panty & Stocking with Garterbelt*; use this only as a firsthand attributed production anecdote. The article returned four images, all hosted on `static.animecorner.me`.

The source is narrowly approved for `people` interview statements and `humor` only when the evidence is a firsthand production anecdote. Speaker, publisher, publication date, exact URL, claim/excerpt, and linked work must remain distinct. Do not turn jokes from the community or editorial framing into verified facts. Search results can include author/category pages, so future collection must only read article URLs listed by the interview archive. The rate was not tested; `is_collecting=false` remains in effect.

`anime_corner_news` remains on hold: bounded queries for production anecdotes returned interviews, author pages, and unrelated article categories rather than a news-archive result. Do not use that search behavior for news collection until source-path filtering is verified.

### Mastodon community-humor candidate (2026-11-09)

A bounded Firecrawl MCP read of `https://mstdn.jp/api/v1/timelines/tag/anime?limit=3` returned three status objects. The response schema includes account identity, individual status URLs, text, replies/boosts, sensitivity/spoiler flags, and media attachments. All three sample records contained account fields, account-path status URLs, and one media attachment. The sample had no reply/boost/sensitive/spoiler flags, but three records are not enough to generalize. Post text and account values were not retained; no media was fetched.

The source remains unapproved. Keeping the individual URL would retain account-path identity, while keeping only the timeline endpoint would lose per-post traceability. Do not ingest raw social posts until a privacy-safe aggregation, retention window, sampling method, and auditable source-reference design are settled. Any resulting record remains a community signal, never a fact.


### Live adapter acceptance check (2026-11-08)

A bounded live call through `FirecrawlMCPAdapter` used local catalog work ID 1 and the catalog title `Attack on Titan`, with `includeDomains` restricted to `www.animenewsnetwork.com` and a result limit of 3. The live tool inventory contained both configured tools. Search returned three ANN encyclopedia entries with distinct native IDs: 14950 (`Attack on Titan (TV)`), 22277 (`Attack on Titan The Final Season (TV)`), and 19819 (`Attack on Titan (TV 3/2018)`). One returned page (id 14950) was read through the adapter: title and body were present, the host remained the exact allowlisted host, `source_native_id=14950`, the body was 424,511 characters, an evidence excerpt and attribution metadata were present, and exact no-redirect URL equality passed (`redirects_verified=true`, one URL in the chain).

**Acceptance result: keep ANN approved at the existing narrow scope.** This validates one seeded search and one page read only. It does not test every result URL, recurring request rates, LLM work matching, database persistence, or unattended operation. The large body size should be bounded or chunked by the downstream evidence store before sustained use. No evidence was persisted and no worker was started.


## Bounded Wikidata MCP pilot (2026-11-08)

The project-local `wikidata` endpoint initialized and exposed `search_items`, `search_properties`, `get_statements`, and `get_statement_values` among its live tools. A query seeded from local catalog work ID 1 (`Attack on Titan`) returned anime series item Q22126305. Property discovery identified P674 (`characters`); the series statements included character QIDs. A detailed `get_statement_values(Q22126305, P674)` call returned per-character values, qualifiers, and references. The inspected references pointed to an imported Wikipedia cast-and-characters page, so Wikidata is approved only as an identity/relationship lead and must not be treated as primary confirmation.

**Decision: approved narrowly** for `character` discovery with explicit property IDs, preserving QIDs/item URLs and statement references. The source registry maps `search_items` and `get_statement_values`, but `StructuredMCPAdapter` is still a Protocol: no concrete Wikidata response parser or collection runtime is implemented. `get_statements`/`search_properties` were used for this bounded review only. No recurring plan or worker is enabled.


## Bounded Kitsu API pilot (2026-11-09)

A Firecrawl web search restricted to `kitsu.io` returned an unrelated result, so it is not a suitable discovery operation for this API. A direct Kitsu JSON:API query through `firecrawl_scrape`, seeded by local work ID 1 title `Attack on Titan`, returned a matching anime record, Kitsu ID `7442`, and exact-host API pagination links. With `include=animeCharacters.character`, one filtered anime record returned 72 included character resources and role relationships. The sparse-field response was about 69 KB; a broader include response exceeded 220 KB. No image was downloaded. The returned synopsis included `(Source: MAL Rewrite)`, so that text is not treated as Kitsu-original or as primary evidence.

Official Kitsu JSON:API documentation describes `filter[text]`, related-resource inclusion, pagination links, and a maximum page limit of 20; it says most public-facing GET endpoints do not require authentication ([Kitsu JSON:API documentation](https://hummingbird-me.github.io/api-docs/)). The documentation does not establish a safe recurring rate in this review, so no recurring plan is configured. Its listed Apache 2.0 license applies to the API documentation and is not treated as a license for catalog records or artwork.

**Decision: approved narrowly** for `guide` and `character` catalog identity/reference lookup plus collection of all available work-linked cover and character image variants. Excluded: `curation`, ratings, and synopsis reproduction. The source uses Firecrawl scrape to fetch the filtered API endpoint; Firecrawl's generic web search is not approved for Kitsu lookup. The `media.kitsu.app` media host is separately allowlisted. A concrete Kitsu JSON:API image parser/adapter and actual binary-download integration are still required before runtime collection. No recurring plan or worker is enabled.


## Image collection contract (2026-11-09)

For every approved source that exposes images, the crawler should retain every distinct image candidate that can be associated with a confirmed local `menu_work_id`, download its file from a separately configured exact media-host allowlist, and store source page/native ID, image role, URL, content hash, byte size, dimensions, and local asset path. Duplicate URLs are collapsed; Kitsu size variants remain distinct candidates so generation can choose among them. Rights fields remain available for provenance but are not a blocking gate in this pilot. The generation worker already queries registered local image candidates by the evidence work IDs and passes them to the model/layout/rendering path; the missing work is source-specific image extraction and registration.

The approved Kitsu API source now allows `media.kitsu.app` for image retrieval. Other approved sources need their own image fields and media hosts verified before download; the allowlist does not widen automatically. No bulk image download, worker, or database write was run.
