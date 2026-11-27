"""Bounded Firecrawl MCP read adapter for known, allowlisted source pages."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Sequence
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from ani_rag.adapter import (
    PageReadProvenanceError, ReadRequest, ReadResult, SearchHit, SearchRequest, canonical_source_url,
)
from ani_rag.firecrawl_errors import error_detail, error_status, response_parts, tool_credit_refusal
from ani_rag.mcp_transport import DEFAULT_MCP_CONFIG_PATH, StreamableHTTPMCPClient, load_mcp_endpoints
from ani_rag.models import EvidenceExcerpt
from ani_rag.work_registry import title_key
from ani_rag.image_ingest import WebImageSearchHit, kitsu_image_references
from ani_rag.web_discovery import (
    WebCandidateHit, WebCandidatePage, WebSearchPage, _validate_candidate_url,
)


class MCPToolCallError(RuntimeError):
    """Sanitized MCP tool failure suitable for queue diagnostics."""

    def __init__(self, operation: str, result: dict[str, Any], *, requested_url: str = "") -> None:
        status_code = error_status(result)
        detail = _safe_mcp_error_detail(result)
        rate_limited = status_code == 429 or bool(re.search(
            r"\b(rate limit(?:ed)?|too many requests|quota exceeded)\b", detail, re.IGNORECASE,
        ))
        raw_detail = error_detail(result)
        credits_exhausted = tool_credit_refusal(result)
        auth_error = bool(re.search(
            r"\b(api key|required authorization|authentication required|anonymous keyless access is unavailable)\b",
            raw_detail, re.IGNORECASE,
        ))
        # Firecrawl also returns 403 when a particular website is unsupported.
        # That explicit refusal must not pause reads for every other website.
        self.unsupported_site = bool(
            operation == "read" and result.get("isError") is True and status_code in {None, 400, 403, 422}
            and not (auth_error or credits_exhausted or rate_limited)
            and not _error_reports_redirect(result, requested_url)
            and re.search(r"\bFirecrawl (?:cannot|can['’]t) scrape this (?:web)?site because "
                          r"it is (?:currently )?not supported\b", raw_detail, re.I)
            and not re.search(r"\b(?:redirect\w*|temporar\w*|timed? out|timeout|unauthorized|"
                              r"forbidden|authenticat\w*|captcha|log[ -]?in|sign[ -]?in)\b", raw_detail, re.I)
        )
        operator_action_required = auth_error or (status_code in {401, 403} and not self.unsupported_site)
        unsupported_target = bool(re.search(
            r"(?:can't|cannot|unable to) scrape.{0,100}(?:not supported|unsupported)|"
            r"(?:site|url|domain).{0,50}(?:not supported|unsupported)",
            detail, re.IGNORECASE,
        ))
        category = "credits_exhausted" if credits_exhausted else "rate_limited" if rate_limited else (
            "operator_action_required" if operator_action_required else
            "unsupported_target" if unsupported_target else
            "server_error" if status_code is not None and status_code >= 500 else
            "tool_error"
        )
        summary = f"mcp_tool_error operation={operation} category={category}"
        if status_code is not None:
            summary += f" status={status_code}"
        if detail:
            summary += f" detail={detail}"
        self.rate_limited = rate_limited
        self.credits_exhausted = credits_exhausted
        self.operator_action_required = operator_action_required
        self.unsupported_target = unsupported_target
        self.permanent_failure = credits_exhausted or unsupported_target or operator_action_required
        self.safe_summary = summary[:360]
        super().__init__(self.safe_summary)


class FirecrawlMCPAdapter:
    """Implements source-bound reads and a separate bounded web-candidate search."""

    def __init__(self, *, config_path=DEFAULT_MCP_CONFIG_PATH) -> None:
        self.config_path = config_path
        self._firecrawl_paused_reason: str | None = None
        self._clients: dict[str, StreamableHTTPMCPClient] = {}

    async def list_tools(self, mcp_server_alias: str) -> frozenset[str]:
        self._ensure_firecrawl_active(mcp_server_alias)
        try:
            return await asyncio.to_thread(self._client(mcp_server_alias).list_tools)
        except Exception as error:
            self._pause_firecrawl_if_needed(mcp_server_alias, error)
            raise

    async def search(self, request: SearchRequest) -> Sequence[SearchHit]:
        self._ensure_firecrawl_active(request.mcp_server_alias)
        try:
            return await asyncio.to_thread(self._search_sync, request)
        except Exception as error:
            self._pause_firecrawl_if_needed(request.mcp_server_alias, error)
            raise

    async def read(self, request: ReadRequest) -> ReadResult:
        self._ensure_firecrawl_active(request.mcp_server_alias)
        try:
            return await asyncio.to_thread(self._read_sync, request)
        except Exception as error:
            self._pause_firecrawl_if_needed(request.mcp_server_alias, error)
            raise

    async def search_web(self, query: str, *, limit: int) -> Sequence[WebCandidateHit]:
        """Search across web domains for candidate leads; results are not evidence."""
        self._ensure_firecrawl_active("firecrawl")
        if not query.strip() or not 1 <= limit <= 5:
            raise ValueError("Web discovery requires a query and a 1-5 candidate limit")
        live_tools = await self.list_tools("firecrawl")
        if "firecrawl_search" not in live_tools:
            raise PermissionError("Firecrawl MCP server does not expose firecrawl_search")
        try:
            return await asyncio.to_thread(self._search_web_sync, query.strip(), limit)
        except Exception as error:
            self._pause_firecrawl_if_needed("firecrawl", error)
            raise

    async def search_web_page(
        self, query: str, *, limit: int, cursor: str | None = None,
    ) -> WebSearchPage:
        """Firecrawl Search currently exposes no documented continuation input."""
        if cursor is not None:
            raise ValueError("Firecrawl search does not expose a verified continuation cursor")
        return WebSearchPage(tuple(await self.search_web(query, limit=limit)), next_cursor=None)

    async def search_images(self, query: str, *, limit: int = 8) -> tuple[WebImageSearchHit, ...]:
        """Search image results only; returned URLs remain untrusted until downloaded and visually checked."""
        self._ensure_firecrawl_active("firecrawl")
        if not query.strip() or not 1 <= limit <= 10:
            raise ValueError("Web image search requires a query and a 1-10 result limit")
        live_tools = await self.list_tools("firecrawl")
        if "firecrawl_search" not in live_tools:
            raise PermissionError("Firecrawl MCP server does not expose firecrawl_search")
        try:
            return await asyncio.to_thread(self._search_images_sync, query.strip(), limit)
        except Exception as error:
            self._pause_firecrawl_if_needed("firecrawl", error)
            raise

    def _search_images_sync(self, query: str, limit: int) -> tuple[WebImageSearchHit, ...]:
        result = self._client("firecrawl").call_tool("firecrawl_search", {
            "query": query,
            "sources": ["images"],
            "limit": limit,
            "objective": (
                "Find distinct, clean anime art for the requested subject. For a character feature, show "
                "the character clearly. For a season feature, find artwork identified with that season. "
                "For a series overview, find representative artwork. Exclude multi-character "
                "collages, manga-panel grids, merchandise product photos, framed posters, and images whose "
                "embedded text competes with the subject. Return direct image and original page URLs. "
                "Search only; do not crawl domains."
            ),
        })
        payload = _extract_payload(result, "web_image_search")
        data = payload.get("data")
        rows = data.get("images") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("Firecrawl image search response omitted the images results array")

        hits: list[WebImageSearchHit] = []
        seen: set[tuple[str, str]] = set()
        for item in rows:
            if not isinstance(item, dict):
                continue
            image_value = item.get("imageUrl") or item.get("image_url") or item.get("image")
            if isinstance(image_value, dict):
                image_value = image_value.get("url")
            page_value = item.get("sourceUrl") or item.get("source_url") or item.get("pageUrl")
            page_value = page_value or item.get("url")
            # Some providers use `url` for the image and `link` for its containing page.
            if not isinstance(image_value, str) and isinstance(item.get("url"), str):
                image_value = item.get("url")
                page_value = item.get("sourceUrl") or item.get("source_url") or item.get("link")
            title = item.get("title") or item.get("alt") or item.get("name")
            description = item.get("description") or item.get("snippet") or ""
            if not isinstance(image_value, str) or not isinstance(page_value, str):
                continue
            if not isinstance(title, str) or not title.strip():
                title = str(description or "Web image result")[:300]
            try:
                _validate_candidate_url(image_value)
                _validate_candidate_url(page_value)
                hit = WebImageSearchHit(
                    image_url=image_value,
                    source_page_url=page_value,
                    title=title[:300],
                    description=description[:800] if isinstance(description, str) else "",
                    native_id=str(item.get("id"))[:200] if item.get("id") is not None else None,
                )
            except (TypeError, ValueError):
                continue
            signature = (hit.image_url, hit.source_page_url)
            if signature in seen:
                continue
            seen.add(signature)
            hits.append(hit)
            if len(hits) >= limit:
                break
        return tuple(hits)

    def _search_web_sync(self, query: str, limit: int) -> Sequence[WebCandidateHit]:
        client = self._client("firecrawl")
        result = client.call_tool("firecrawl_search", {
            "query": query,
            "sources": ["web"],
            "limit": limit,
            "objective": (
                "Search the public web for a small set of original pages relevant to this topic. "
                "Return candidate URLs only; this is discovery, not an endorsement of factual accuracy. "
                "Do not crawl domains or follow links."
            ),
        })
        payload = _extract_payload(result, "web_discovery_search")
        data = payload.get("data")
        results = data.get("web") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise RuntimeError("Firecrawl web search response omitted the web results array")
        hits: list[WebCandidateHit] = []
        seen: set[str] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            title = item.get("title")
            if not isinstance(url, str) or not isinstance(title, str) or not title.strip():
                continue
            try:
                _validate_candidate_url(url)
                hit = WebCandidateHit(
                    title=title[:500], url=url,
                    snippet=item.get("description", "")[:1200]
                    if isinstance(item.get("description"), str) else "",
                    published_at=_published_at(item),
                )
            except (TypeError, ValueError):
                continue
            if hit.url in seen:
                continue
            seen.add(hit.url)
            hits.append(hit)
            if len(hits) >= limit:
                break
        return tuple(hits)

    async def read_web_candidate(self, hit: WebCandidateHit) -> WebCandidatePage:
        """Read one result page with the result's exact host as a temporary boundary."""
        self._ensure_firecrawl_active("firecrawl")
        _validate_candidate_url(hit.url)
        live_tools = await self.list_tools("firecrawl")
        if "firecrawl_scrape" not in live_tools:
            raise PermissionError("Firecrawl MCP server does not expose firecrawl_scrape")
        request = ReadRequest(
            source_id="web_discovery_candidate", url=hit.url,
            mcp_server_alias="firecrawl", tool_name="firecrawl_scrape",
            allowed_hosts=frozenset({hit.host}),
        )
        try:
            result = await self.read(request)
        except Exception as error:
            self._pause_firecrawl_if_needed("firecrawl", error)
            raise
        if not result.redirects_verified:
            raise PermissionError("Web candidate read did not verify same-host URL handling")
        body = (result.body or "")[:120_000]
        return WebCandidatePage(
            hit=hit, title=result.title, final_url=result.url, body=body,
            language=result.language, published_at=result.published_at or hit.published_at,
            fetched_at=datetime.now(timezone.utc),
        )

    def _ensure_firecrawl_active(self, alias: str) -> None:
        if alias == "firecrawl" and self._firecrawl_paused_reason:
            raise PermissionError(f"Firecrawl MCP calls paused: {self._firecrawl_paused_reason}")

    def _pause_firecrawl_if_needed(self, alias: str, error: Exception) -> None:
        if alias != "firecrawl":
            return
        if bool(getattr(error, "credits_exhausted", False)):
            self._firecrawl_paused_reason = "provider credits exhausted; restart after replenishing credits"
        elif bool(getattr(error, "rate_limited", False)):
            self._firecrawl_paused_reason = "provider rate limit reached; restart after the limit resets"
        elif bool(getattr(error, "operator_action_required", False)):
            self._firecrawl_paused_reason = "provider authentication or permission needs operator action"

    def _search_sync(self, request: SearchRequest) -> Sequence[SearchHit]:
        if request.source_id == "kitsu_anime_catalog":
            return self._search_kitsu_api(request)
        if request.mcp_server_alias != "firecrawl" or request.tool_name != "firecrawl_search":
            raise PermissionError("This adapter only supports the configured Firecrawl search tool")
        if not request.allowed_hosts:
            raise PermissionError("Search requires at least one exact allowed source host")
        client = self._client(request.mcp_server_alias)
        provider_limit = request.limit
        if request.allowed_path_prefixes:
            # Search is host-scoped by Firecrawl; path scope must be enforced here.
            # Fetch extra host results so out-of-section pages do not starve the source.
            provider_limit = min(100, request.limit * 5)
        result = client.call_tool(request.tool_name, {
            "query": request.query,
            "includeDomains": sorted(request.allowed_hosts),
            "sources": ["web"],
            "limit": provider_limit,
            "objective": (
                "Find relevant pages only on the configured source host and allowed path section "
                "for this work-title query; do not follow off-host links."
            ),
        })
        payload = _extract_payload(result, "search")
        data = payload.get("data")
        results = data.get("web") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise RuntimeError("Firecrawl search response omitted the web results array")
        hits: list[SearchHit] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str):
                continue
            host = _host(url)
            if host not in request.allowed_hosts:
                continue
            if request.allowed_path_prefixes and not _path_in_scope(url, request.allowed_path_prefixes):
                continue
            if not _ann_page_matches_content_type(request.source_id, url, request.content_type):
                continue
            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                continue
            hits.append(SearchHit(
                source_id=request.source_id,
                title=title,
                url=url,
                mcp_server_alias=request.mcp_server_alias,
                tool_name=request.tool_name,
                snippet=item.get("description") if isinstance(item.get("description"), str) else None,
                published_at=_published_at(item),
                source_native_id=(
                    _source_native_id(request.source_id, url)
                    or (str(item["id"]) if item.get("id") is not None else None)
                ),
            ))
            if len(hits) == request.limit:
                break
        return tuple(hits)

    async def lookup_kitsu_title(self, title: str) -> tuple[str, ...]:
        """Look up an exact work identity with one bounded Kitsu query."""
        self._ensure_firecrawl_active("firecrawl")
        request = SearchRequest("kitsu_anime_catalog", title, "firecrawl", "firecrawl_scrape", frozenset({"kitsu.io"}), limit=20)
        try:
            payload = await asyncio.to_thread(self._kitsu_title_page, request)
        except Exception as error:
            self._pause_firecrawl_if_needed("firecrawl", error)
            raise
        rows = payload.get("data")
        links = payload.get("links", {})
        meta = payload.get("meta", {})
        if not isinstance(rows, list) or not isinstance(links, dict) or not isinstance(meta, dict):
            raise RuntimeError("Invalid Kitsu title lookup")
        total = meta.get("count")
        if (payload.get("errors") or links.get("next")
                or (total is not None and (type(total) is not int or total != len(rows)))
                or (len(rows) >= request.limit and total is None and "next" not in links)):
            raise RuntimeError("Incomplete Kitsu title lookup")
        key = title_key(title)
        matched = set()
        for row in rows:
            if not isinstance(row, dict) or row.get("type") != "anime" or not isinstance(row.get("attributes"), dict):
                raise RuntimeError("Invalid Kitsu anime result")
            attributes = row["attributes"]
            titles = attributes.get("titles")
            if not isinstance(titles, dict) or not str(attributes.get("canonicalTitle") or "").strip():
                raise RuntimeError("Kitsu anime result omitted titles")
            aliases = attributes.get("abbreviatedTitles") or []
            if not isinstance(aliases, list):
                raise RuntimeError("Invalid Kitsu alternate titles")
            if any(isinstance(name, str) and title_key(name) == key for name in (attributes["canonicalTitle"], *titles.values(), *aliases)):
                source_id = str(row.get("id", ""))
                if not re.fullmatch(r"[1-9][0-9]*", source_id):
                    raise RuntimeError("Kitsu anime result omitted its ID")
                matched.add(source_id)
        return tuple(sorted(matched))

    def _kitsu_title_page(self, request: SearchRequest) -> dict[str, Any]:
        if request.mcp_server_alias != "firecrawl" or request.tool_name != "firecrawl_scrape":
            raise PermissionError("Kitsu catalog lookup must use the configured Firecrawl scrape tool")
        if request.allowed_hosts != frozenset({"kitsu.io"}):
            raise PermissionError("Kitsu API search requires the exact kitsu.io host allowlist")
        page_limit = min(request.limit, 20)
        query = urlencode({
            "filter[text]": request.query,
            "page[limit]": str(page_limit),
            "fields[anime]": "id,slug,titles,canonicalTitle,abbreviatedTitles,showType,startDate,endDate",
        })
        api_url = f"https://kitsu.io/api/edge/anime?{query}"
        client = self._client(request.mcp_server_alias)
        result = client.call_tool(request.tool_name, {
            "url": api_url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "objective": "Read one bounded Kitsu anime JSON:API title-filter query and preserve record IDs, titles, and pagination data.",
        })
        payload = _extract_payload(result, "kitsu_api_search")
        return _parse_kitsu_api(payload.get("markdown"))

    def _search_kitsu_api(self, request: SearchRequest) -> Sequence[SearchHit]:
        page_limit = min(request.limit, 20)
        api_payload = self._kitsu_title_page(request)
        data = api_payload.get("data")
        rows = data if isinstance(data, list) else [data]
        hits: list[SearchHit] = []
        item_query = urlencode({
            "include": "animeCharacters.character",
            "fields[anime]": "id,slug,titles,canonicalTitle,showType,startDate,endDate,coverImage,posterImage,synopsis,animeCharacters",
            "fields[animeCharacters]": "role,character",
            # Names and artwork are not enough to write character introductions.
            "fields[characters]": "name,description,image",
        })
        for row in rows:
            if not isinstance(row, dict) or row.get("type") != "anime":
                continue
            native_id = str(row.get("id") or "").strip()
            attributes = row.get("attributes")
            title = attributes.get("canonicalTitle") if isinstance(attributes, dict) else None
            if not native_id or not isinstance(title, str) or not title.strip():
                continue
            item_url = f"https://kitsu.io/api/edge/anime/{native_id}?{item_query}"
            hits.append(SearchHit(
                source_id=request.source_id, title=title, url=item_url,
                mcp_server_alias=request.mcp_server_alias, tool_name=request.tool_name,
                snippet=f"Kitsu anime ID {native_id}", source_native_id=native_id,
            ))
            if len(hits) >= page_limit:
                break
        return tuple(hits)

    def _read_sync(self, request: ReadRequest) -> ReadResult:
        if request.mcp_server_alias != "firecrawl" or request.tool_name != "firecrawl_scrape":
            raise PermissionError("This adapter only supports the configured Firecrawl single-page read")
        requested_host = _host(request.url)
        if requested_host not in request.allowed_hosts:
            raise PermissionError("Read URL host must match the exact source allowlist")
        client = self._client(request.mcp_server_alias)
        is_kitsu = request.source_id == "kitsu_anime_catalog"
        result = client.call_tool(request.tool_name, {
            "url": request.url,
            "formats": ["markdown", "images"],
            "onlyMainContent": True,
            "objective": (
                "Read one Kitsu anime JSON:API item and included character images; preserve IDs and image URLs. Do not follow links."
                if is_kitsu else
                "Read only this one known source page, preserve its title and attribution, and do not follow links to other pages."
            ),
        })
        payload = _extract_payload(result, "read", requested_url=request.url)
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise PageReadProvenanceError("redirect_missing_metadata")
        source_url = metadata.get("sourceURL")
        final_url, redirect_chain = _verified_read_route(request, metadata)
        body = payload.get("markdown")
        if not isinstance(body, str) or not body.strip():
            raise RuntimeError("Firecrawl response contained no markdown body")
        title = metadata.get("title") or metadata.get("og:title") or request.url
        source_native_id = _source_native_id(request.source_id, source_url) or _source_native_id(request.source_id, final_url)
        raw_payload: dict[str, Any] = {"metadata": metadata, "response_fields": sorted(payload.keys())}
        if is_kitsu:
            api_payload = _parse_kitsu_api(body)
            data = api_payload.get("data")
            item = data[0] if isinstance(data, list) and data else data
            attributes = item.get("attributes") if isinstance(item, dict) else None
            api_title = attributes.get("canonicalTitle") if isinstance(attributes, dict) else None
            if isinstance(api_title, str) and api_title.strip():
                title = api_title
            if isinstance(item, dict) and item.get("id") is not None:
                source_native_id = str(item["id"])
            body = json.dumps(api_payload, ensure_ascii=False, sort_keys=True)
            raw_payload["kitsu_api"] = api_payload
            raw_payload["image_candidates"] = kitsu_image_references(api_payload)
        else:
            raw_payload["image_candidates"] = _image_candidates(
                payload.get("images"), source_id=request.source_id,
                source_native_id=source_native_id,
            )
        if not isinstance(title, str):
            raise RuntimeError("Firecrawl page title has an invalid type")
        language = metadata.get("language")
        published_at = _published_at(metadata)
        publisher = metadata.get("og:site_name") or metadata.get("ogSiteName")
        excerpts = (EvidenceExcerpt(text=body[:2000], locator="page:main"),)
        image_candidates = _image_candidates(payload.get("images"))
        return ReadResult(
            source_id=request.source_id,
            title=title,
            url=final_url,
            body=body,
            mcp_server_alias=request.mcp_server_alias,
            tool_name=request.tool_name,
            language=language if isinstance(language, str) else None,
            published_at=published_at,
            excerpts=excerpts,
            source_native_id=source_native_id,
            publisher=publisher if isinstance(publisher, str) else None,
            raw_payload=raw_payload,
            redirect_chain=redirect_chain,
            redirects_verified=True,
        )

    def _client(self, alias: str) -> StreamableHTTPMCPClient:
        cached = self._clients.get(alias)
        if cached is not None:
            return cached
        endpoints = load_mcp_endpoints(self.config_path)
        try:
            endpoint = endpoints[alias]
        except KeyError as error:
            raise PermissionError(f"MCP server alias {alias!r} is not configured") from error
        return self._clients.setdefault(alias, StreamableHTTPMCPClient(endpoint))


def _extract_payload(result: dict[str, Any], operation: str, *, requested_url: str = "") -> dict[str, Any]:
    if result.get("isError") is True:
        raise MCPToolCallError(operation, result, requested_url=requested_url)
    for data in response_parts(result):
        if isinstance(data, dict):
            if data.get("success") is False:
                raise MCPToolCallError(operation, result, requested_url=requested_url)
            return data
    raise RuntimeError("Firecrawl response did not contain a structured page result")


def _error_reports_redirect(result: dict[str, Any], requested_url: str) -> bool:
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        return False
    for metadata in (structured, structured.get("metadata")):
        if not isinstance(metadata, dict):
            continue
        chain = _redirect_chain(metadata)
        final = metadata.get("finalURL") or metadata.get("finalUrl") or metadata.get("url")
        if (len(chain) > 1 or metadata.get("redirected") or metadata.get("location")
                or (isinstance(final, str) and final != requested_url)):
            return True
    return False


def _safe_mcp_error_detail(result: dict[str, Any]) -> str:
    detail = error_detail(result)
    detail = re.sub(r"(?i)\bfc-[A-Za-z0-9_-]+", "[redacted]", detail)
    detail = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", detail)
    detail = re.sub(
        r"(?i)\b(authorization|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|secret|password)\b\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[redacted]", detail,
    )
    detail = re.sub(r"https?://[^\s<>\]\[\"']+", "[url]", detail)
    detail = re.sub(r"\s+", " ", detail).strip()
    return detail[:220]


def _host(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("URL must be an absolute credential-free HTTP(S) URL")
    if parsed.port is not None and parsed.port not in ({80} if parsed.scheme == "http" else {443}):
        raise ValueError("URL must not use a non-standard port")
    return host


def _path_in_scope(url: str, prefixes: tuple[str, ...]) -> bool:
    """Match the same decoded path rules used by source URL validation."""
    path = unquote(urlsplit(url).path)
    if any(part == ".." for part in path.split("/")):
        return False
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def _ann_page_matches_content_type(
    source_id: str, url: str, content_type: object,
) -> bool:
    """Keep ANN entity page types aligned with the content lane being collected."""
    if source_id != "ann_encyclopedia":
        return True
    path = urlsplit(url).path
    allowed = {
        "character": {"/encyclopedia/anime.php", "/encyclopedia/manga.php", "/encyclopedia/people.php"},
        "guide": {"/encyclopedia/anime.php", "/encyclopedia/manga.php"},
        "people": {"/encyclopedia/anime.php", "/encyclopedia/manga.php", "/encyclopedia/people.php", "/encyclopedia/company.php"},
        "music": {"/encyclopedia/anime.php", "/encyclopedia/manga.php"},
    }
    lane = getattr(content_type, "value", content_type)
    return lane in allowed and path in allowed[lane]


def _source_native_id(source_id: str, url: str | None) -> str | None:
    if not url:
        return None
    if source_id == "ann_encyclopedia":
        return _ann_native_id(source_id, url)
    if source_id == "flyingdog_music":
        path = urlsplit(url).path
        match = re.search(r"/Discography/[^/]+/([A-Za-z0-9_-]+)\.html$", path)
        if not match:
            match = re.search(r"/Discographylist2/([A-Za-z0-9_-]+)\.html$", path)
        if match:
            return match.group(1)
    return None


def _ann_native_id(source_id: str, url: str) -> str | None:
    """Preserve ANN encyclopedia entry IDs as external IDs, not generated local work IDs."""
    if source_id != "ann_encyclopedia":
        return None
    parsed = urlsplit(url)
    if parsed.path not in {
        "/encyclopedia/anime.php", "/encyclopedia/manga.php",
        "/encyclopedia/people.php", "/encyclopedia/company.php",
    }:
        return None
    values = parse_qs(parsed.query).get("id", ())
    if len(values) != 1 or not values[0].isdigit():
        return None
    return values[0]


def _redirect_chain(metadata: dict[str, Any]) -> tuple[str, ...]:
    value = metadata.get("redirectChain") or metadata.get("redirect_chain")
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return ()
    return tuple(value)


def _verified_read_route(request: ReadRequest, metadata: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Compare actual resource URLs, never the more permissive candidate dedupe keys."""
    def address(value: object) -> tuple[str, str]:
        if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
            raise PageReadProvenanceError("redirect_invalid_url")
        try:
            host = _host(value)
            # A fragment can select a different article on a hash-routed site.
            key = canonical_source_url(value), urlsplit(value).fragment
        except ValueError:
            raise PageReadProvenanceError("redirect_invalid_url") from None
        if host not in request.allowed_hosts:
            raise PageReadProvenanceError("redirect_outside_host")
        if request.allowed_path_prefixes and not _path_in_scope(value, request.allowed_path_prefixes):
            raise PageReadProvenanceError("redirect_outside_path")
        return key

    requested = address(request.url)
    finals = [metadata[k] for k in ("url", "finalURL", "finalUrl") if metadata.get(k) is not None]
    if not finals:
        raise PageReadProvenanceError("redirect_missing_final_url")
    final = address(finals[0])
    if any(address(value) != final for value in finals[1:]):
        raise PageReadProvenanceError("redirect_conflicting_metadata")
    source = address(metadata["sourceURL"]) if metadata.get("sourceURL") is not None else None
    chains = [metadata[k] for k in ("redirectChain", "redirect_chain") if k in metadata]
    if any(not isinstance(chain, list) for chain in chains):
        raise PageReadProvenanceError("redirect_unverified_chain")
    chain_keys = [[address(url) for url in chain] for chain in chains]
    if len(chain_keys) == 2 and chain_keys[0] != chain_keys[1]:
        raise PageReadProvenanceError("redirect_conflicting_metadata")
    route = chain_keys[0] if chain_keys else []
    if route:
        if route[0] != requested or route[-1] != final:
            raise PageReadProvenanceError("redirect_unverified_chain")
        if source is not None and source not in (requested, final):
            raise PageReadProvenanceError("redirect_conflicting_metadata")
    else:
        if source is None:
            raise PageReadProvenanceError("redirect_missing_source_url")
        if source != requested or final != requested:
            raise PageReadProvenanceError("redirect_unverified_chain")
    if len(route) < 2 and (metadata.get("redirected") or metadata.get("location")):
        raise PageReadProvenanceError("redirect_conflicting_metadata")
    final_url = finals[0]
    # Keep the exact reported final URL for citations and downstream path checks.
    hops = tuple(chains[0][:-1]) if route else ()
    return final_url, (*hops, final_url)


def _image_candidates(
    value: object, *, source_id: str = "", source_native_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, str]]:
    """Keep a bounded image URL list; binary downloads are separately host-gated."""
    if not isinstance(value, list):
        return []
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        url = item if isinstance(item, str) else item.get("url") if isinstance(item, dict) else None
        if not isinstance(url, str) or not url.startswith("https://") or url in seen:
            continue
        if source_id == "ann_encyclopedia":
            parsed = urlsplit(url)
            if (parsed.hostname != "cdn.animenewsnetwork.com" or not source_native_id
                    or f"/encyc/A{source_native_id}-" not in parsed.path):
                continue
        seen.add(url)
        output.append({"url": url, "role": "source_artwork", **({"source_native_id": source_native_id} if source_native_id else {})})
        if len(output) >= limit:
            break
    return output


def _parse_kitsu_api(markdown: object) -> dict[str, Any]:
    if not isinstance(markdown, str):
        raise RuntimeError("Kitsu API response did not contain a JSON-formatted body")
    # Firecrawl's Markdown format escapes punctuation in JSON responses.
    cleaned = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~])", r"\1", markdown)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("Kitsu API response contained no JSON object")
    try:
        payload = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as error:
        raise RuntimeError("Could not parse the preserved Kitsu JSON:API response") from error
    if not isinstance(payload, dict) or "data" not in payload:
        raise RuntimeError("Kitsu API JSON omitted its data member")
    return payload


def _published_at(metadata: dict[str, Any]) -> datetime | None:
    value = metadata.get("publishedTime") or metadata.get("publishedAt") or metadata.get("article:published_time")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
