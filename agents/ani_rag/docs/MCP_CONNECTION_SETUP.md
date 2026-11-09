# ani_rag MCP connection setup

Updated 2026-11-08 after the project transport connected to both public endpoint inventories, made one domain-restricted `firecrawl_search`, and made one bounded `firecrawl_scrape` call for the Frieren official character index. This does not install the separate ChatGPT Firecrawl app or start background workers.

## Recommended two-server pilot

Use two shared servers for different jobs:

| Project alias in `config/mcp_servers.json` | Streamable HTTP endpoint | Operation mapping for the pilot |
|---|---|---|
| `firecrawl` | `https://mcp.firecrawl.dev/v2/mcp` | `search` → `firecrawl_search`; `read` → `firecrawl_scrape` |
| `wikidata` | `https://wd-mcp.wmcloud.org/mcp/` | structured `search` → `search_items`; structured `read` → `get_statement_values` |

These aliases are now configured project-locally in `config/mcp_servers.json` and were contacted by `src/ani_rag/mcp_transport.py`. Both negotiated protocol `2025-03-26` and returned:

- Firecrawl keyless inventory: `firecrawl_scrape`, `firecrawl_search`, `firecrawl_parse`.
- Wikidata: `search_items`, `search_properties`, `get_statements`, `get_statement_values`, `get_instance_and_subclass_hierarchy`, `execute_sparql`.

The ani_rag contracts use the `search` and `read` operation names, while `tool_bindings` stores exact tool names returned by the live server. `FirecrawlMCPAdapter` supports bounded `firecrawl_search` requests using Firecrawl's `includeDomains` and independently drops results outside exact `allowed_hosts`; reads remain one page at a time. The generic transport can list Wikidata tools, but structured Wikidata calls are not yet implemented. Discover a property with `search_properties` before making a statement-values request; do not assume QIDs/PIDs.

## Example MCP host configuration

`config/mcp_servers.json` is the project's active non-secret Streamable HTTP endpoint configuration. The equivalent host configuration shape is:

```json
{
  "mcpServers": {
    "firecrawl": {
      "type": "streamable_http",
      "url": "https://mcp.firecrawl.dev/v2/mcp"
    },
    "wikidata": {
      "type": "streamable_http",
      "url": "https://wd-mcp.wmcloud.org/mcp/"
    }
  }
}
```

Firecrawl's keyless surface has daily limits. For this Python transport, `mcp_transport.py` reads `ANI_RAG_FIRECRAWL_API_KEY` (or the existing `Firecrawl_API_KEY` name) from the process environment or this project's `.env`, then sends it as `Authorization: Bearer …` only to the `firecrawl` alias. The key is never printed or copied into `mcp_servers.json` or a URL. `OPENAI_API_KEY` and `ANI_MENU_MODEL` can be used with `OpenAIResponsesStructuredModel.from_project_environment()` for the LLM adapters. The Firecrawl ChatGPT app integration is separate from `ani_rag`'s Python transport.

If a project `.env` is not being used, on Windows PowerShell you can enter the Firecrawl key through a hidden prompt in the same terminal session before starting a worker. This keeps it out of command history and limits it to that terminal and its child processes:

```powershell
$secureKey = Read-Host 'Firecrawl API key' -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:ANI_RAG_FIRECRAWL_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    Remove-Variable secureKey, keyPointer
}
```

After that, start the `ani_rag` process from the same PowerShell window. Close that window when finished to discard its environment copy. If Firecrawl's API key is rotated, repeat the prompt with the new key. Do not paste the key into Codex chat or commit it.

## Source and safety boundary

- Start with `character`, using `frieren_official` as a work-scoped original source and Wikidata only as an identity/relationship lead.
- Firecrawl search passes exact source hosts in `includeDomains` and independently filters returned result URLs against the configured host set. A `site:` query is only a search hint, not a security check.
- `firecrawl_scrape` must not fetch a URL outside the approved source host. The concrete adapter must verify redirect targets before following them and attest the final URL; if the tool response cannot prove this, fail closed.
- Do not use `firecrawl_crawl`, `firecrawl_map`, `firecrawl_agent`, or broad crawling for this pilot.
- Wikidata's endpoint host is the MCP service; item URLs are on Wikidata. Keep the source host allowlist for item URLs separate from the MCP endpoint URL.
- Preserve each retrieved Wikidata statement, qualifier, and reference as structured evidence. Verify factual claims against the relevant official source before promotion.
- `sources.evergreen.json` contains one `draft` entry for `frieren_official`; it is non-executable and is not an approval.

## What remains before source activation

1. The project-local aliases `firecrawl` and `wikidata` have both returned their live tool inventories through the new transport. The separate ChatGPT Firecrawl app is not installed and is not required by this project client.
2. The URL adapter supports bounded host-filtered search and one-page reads. Structured Wikidata response parsing remains to be implemented.
3. The Frieren read returned an official-site title and Japanese character index on the allowlisted host. Firecrawl did not return a redirect chain; the adapter therefore sets `redirects_verified=false`, and the collector will reject it for storage.
4. Keep `frieren_official` at `draft` until the configured source read can attest each redirect target and final URL (or a safe, verified URL strategy is implemented), and the resulting evidence passes the project collection contract.

Firecrawl's [Scrape API response schema](https://docs.firecrawl.dev/api-reference/endpoint/scrape) exposes `metadata.sourceURL` and `metadata.url` but does not document a redirect chain. The `lockdown` option is cache-only and never makes an outbound request, so it cannot be used for fresh recurring collection or as redirect attestation. Do not set `redirects_verified` from host equality alone.

## Bounded character-source read (2026-11-08)

- Requested URL: `https://frieren-anime.jp/character/`
- MCP endpoint/tool: `https://mcp.firecrawl.dev/v2/mcp` / `firecrawl_scrape`
- Response: page scrape returned status code 200, language `ja`, and source URL `https://frieren-anime.jp/character/`.
- Source identity evidence: page metadata names the site as the official *Frieren* anime site; the returned character index includes linked character labels and profile URLs on `frieren-anime.jp`.
- Host check: requested and returned source URLs use the exact candidate host `frieren-anime.jp`.
- Limitation: the tool response did not provide a redirect chain or a separately attested final URL. Metadata's canonical `og:url` points to `https://frieren-anime.jp/character/chara_group1/`, so final-path/redirect behavior is not yet verified. Keep the entry at `draft`.
- The read was inspected through the public endpoint and was not persisted as project evidence; no worker, app connector, or project Python adapter ran.
