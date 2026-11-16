"""Bounded Firecrawl MCP read adapter for known, allowlisted source pages."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Sequence
from urllib.parse import parse_qs, urlencode, urlsplit

from ani_rag.adapter import ReadRequest, ReadResult, SearchHit, SearchRequest
from ani_rag.mcp_transport import DEFAULT_MCP_CONFIG_PATH, StreamableHTTPMCPClient, load_mcp_endpoints
from ani_rag.models import EvidenceExcerpt
from ani_rag.image_ingest import WebImageSearchHit, kitsu_image_references
from ani_rag.web_discovery import WebCandidateHit, WebCandidatePage, _validate_candidate_url


class MCPToolCallError(RuntimeError):
    """Sanitized MCP tool failure suitable for queue diagnostics."""

    def __init__(self, operation: str, result: dict[str, Any]) -> None:
        status_code = _mcp_error_status(result)
        detail = _safe_mcp_error_detail(result)
        rate_limited = status_code == 429 or bool(re.search(
            r"\b(rate limit(?:ed)?|too many requests|quota exceeded)\b", detail, re.IGNORECASE,
        ))
        operator_action_required = status_code in {401, 403} or bool(re.search(
            r"\b(api key|required authorization|authentication required|anonymous keyless access is unavailable)\b",
            detail, re.IGNORECASE,
        ))
        category = "rate_limited" if rate_limited else (
            "operator_action_required" if operator_action_required else
            "server_error" if status_code is not None and status_code >= 500 else
            "tool_error"
        )
        summary = f"mcp_tool_error operation={operation} category={category}"
        if status_code is not None:
            summary += f" status={status_code}"
        if detail:
            summary += f" detail={detail}"
        self.rate_limited = rate_limited
        self.operator_action_required = operator_action_required
        self.safe_summary = summary[:360]
        super().__init__(self.safe_summary)


class FirecrawlMCPAdapter:
    """Implements source-bound reads and a separate bounded web-candidate search."""

    def __init__(self, *, config_path=DEFAULT_MCP_CONFIG_PATH) -> None:
        self.config_path = config_path
        self._web_discovery_paused = False
        self._clients: dict[str, StreamableHTTPMCPClient] = {}

    async def list_tools(self, mcp_server_alias: str) -> frozenset[str]:
        return await asyncio.to_thread(self._client(mcp_server_alias).list_tools)

    async def search(self, request: SearchRequest) -> Sequence[SearchHit]:
        return await asyncio.to_thread(self._search_sync, request)

    async def read(self, request: ReadRequest) -> ReadResult:
        return await asyncio.to_thread(self._read_sync, request)

    async def search_web(self, query: str, *, limit: int) -> Sequence[WebCandidateHit]:
        """Search across web domains for candidate leads; results are not evidence."""
        self._ensure_web_discovery_active()
        if not query.strip() or not 1 <= limit <= 5:
            raise ValueError("Web discovery requires a query and a 1-5 candidate limit")
        live_tools = await self.list_tools("firecrawl")
        if "firecrawl_search" not in live_tools:
            raise PermissionError("Firecrawl MCP server does not expose firecrawl_search")
        try:
            return await asyncio.to_thread(self._search_web_sync, query.strip(), limit)
        except Exception as error:
            self._pause_web_discovery_if_needed(error)
            raise

    async def search_images(self, query: str, *, limit: int = 8) -> tuple[WebImageSearchHit, ...]:
        """Search image results only; returned URLs remain untrusted until downloaded and visually checked."""
        self._ensure_web_discovery_active()
        if not query.strip() or not 1 <= limit <= 10:
            raise ValueError("Web image search requires a query and a 1-10 result limit")
        live_tools = await self.list_tools("firecrawl")
        if "firecrawl_search" not in live_tools:
            raise PermissionError("Firecrawl MCP server does not expose firecrawl_search")
        try:
            return await asyncio.to_thread(self._search_images_sync, query.strip(), limit)
        except Exception as error:
            self._pause_web_discovery_if_needed(error)
            raise

    def _search_images_sync(self, query: str, limit: int) -> tuple[WebImageSearchHit, ...]:
        result = self._client("firecrawl").call_tool("firecrawl_search", {
            "query": query,
            "sources": ["images"],
            "limit": limit,
            "objective": (
                "Find distinct, clean anime art that visibly depicts the exact named character or scene. "
                "For a character portrait, show that one character clearly and large. Exclude multi-character "
                "collages, manga-panel grids, merchandise product photos, framed posters, and images whose "
                "embedded text competes with the subject. Return direct image and original page URLs. "
                "Search only; do not crawl domains."
            ),
        })
        if result.get("isError") is True:
            raise MCPToolCallError("web_image_search", result)
        payload = _extract_payload(result)
        if payload.get("success") is False:
            raise RuntimeError("Firecrawl image search response reported failure")
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
        if result.get("isError") is True:
            raise MCPToolCallError("web_discovery_search", result)
        payload = _extract_payload(result)
        if payload.get("success") is False:
            raise RuntimeError("Firecrawl web search response reported failure")
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
        self._ensure_web_discovery_active()
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
            self._pause_web_discovery_if_needed(error)
            raise
        if not result.redirects_verified:
            raise PermissionError("Web candidate read did not verify same-host URL handling")
        body = (result.body or "")[:120_000]
        return WebCandidatePage(
            hit=hit, title=result.title, final_url=result.url, body=body,
            language=result.language, published_at=result.published_at or hit.published_at,
            fetched_at=datetime.now(timezone.utc),
        )

    def _ensure_web_discovery_active(self) -> None:
        if self._web_discovery_paused:
            raise PermissionError("Broad-web discovery is paused after an MCP rate-limit/auth failure")

    def _pause_web_discovery_if_needed(self, error: Exception) -> None:
        if bool(getattr(error, "rate_limited", False)) or bool(
            getattr(error, "operator_action_required", False)
        ):
            self._web_discovery_paused = True

    def _search_sync(self, request: SearchRequest) -> Sequence[SearchHit]:
        if request.source_id == "kitsu_anime_catalog":
            return self._search_kitsu_api(request)
        if request.mcp_server_alias != "firecrawl" or request.tool_name != "firecrawl_search":
            raise PermissionError("This adapter only supports the configured Firecrawl search tool")
        if not request.allowed_hosts:
            raise PermissionError("Search requires at least one exact allowed source host")
        client = self._client(request.mcp_server_alias)
        result = client.call_tool(request.tool_name, {
            "query": request.query,
            "includeDomains": sorted(request.allowed_hosts),
            "sources": ["web"],
            "limit": request.limit,
            "objective": "Find relevant pages only on the configured source host for this work-title query; do not follow off-host links.",
        })
        if result.get("isError") is True:
            raise MCPToolCallError("search", result)
        payload = _extract_payload(result)
        if payload.get("success") is False:
            raise RuntimeError("Firecrawl search response reported failure")
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

    def _search_kitsu_api(self, request: SearchRequest) -> Sequence[SearchHit]:
        if request.mcp_server_alias != "firecrawl" or request.tool_name != "firecrawl_scrape":
            raise PermissionError("Kitsu catalog lookup must use the configured Firecrawl scrape tool")
        if request.allowed_hosts != frozenset({"kitsu.io"}):
            raise PermissionError("Kitsu API search requires the exact kitsu.io host allowlist")
        page_limit = min(request.limit, 20)
        query = urlencode({
            "filter[text]": request.query,
            "page[limit]": str(page_limit),
            "fields[anime]": "id,slug,titles,canonicalTitle,showType,startDate,endDate",
        })
        api_url = f"https://kitsu.io/api/edge/anime?{query}"
        client = self._client(request.mcp_server_alias)
        result = client.call_tool(request.tool_name, {
            "url": api_url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "objective": "Read one bounded Kitsu anime JSON:API title-filter query and preserve record IDs, titles, and pagination data.",
        })
        if result.get("isError") is True:
            raise MCPToolCallError("kitsu_api_search", result)
        payload = _extract_payload(result)
        api_payload = _parse_kitsu_api(payload.get("markdown"))
        data = api_payload.get("data")
        rows = data if isinstance(data, list) else [data]
        hits: list[SearchHit] = []
        item_query = urlencode({
            "include": "animeCharacters.character",
            "fields[anime]": "id,slug,titles,canonicalTitle,showType,startDate,endDate,coverImage,synopsis",
            "fields[animeCharacters]": "role",
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
        if result.get("isError") is True:
            raise MCPToolCallError("read", result)
        payload = _extract_payload(result)
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise RuntimeError("Firecrawl response omitted page metadata")
        source_url = metadata.get("sourceURL")
        final_url = metadata.get("url")
        if not isinstance(final_url, str):
            final_url = source_url if isinstance(source_url, str) else None
        if not isinstance(final_url, str) or _host(final_url) not in request.allowed_hosts:
            raise PermissionError("Firecrawl source URL is missing or outside the exact host allowlist")
        returned_urls = [value for value in (source_url, final_url) if isinstance(value, str)]
        redirect_chain = _redirect_chain(metadata)
        if any(_host(value) not in request.allowed_hosts for value in returned_urls + list(redirect_chain)):
            raise PermissionError("Firecrawl response contains a source URL outside the exact host allowlist")
        if redirect_chain:
            redirects_verified = (
                len(redirect_chain) >= 2
                and redirect_chain[0] == request.url
                and redirect_chain[-1] == final_url
            )
        else:
            # Accept only an unchanged URL as a no-redirect read. Firecrawl's
            # response has original/final URL fields but no documented chain.
            no_redirect_attested = source_url == request.url and final_url == request.url
            redirects_verified = no_redirect_attested
            if no_redirect_attested:
                redirect_chain = (request.url,)
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
            redirects_verified=redirects_verified,
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
        client = StreamableHTTPMCPClient(endpoint)
        self._clients[alias] = client
        return client


def _extract_payload(result: dict[str, Any]) -> dict[str, Any]:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for block in result.get("content", []):
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    raise RuntimeError("Firecrawl response did not contain a structured page result")


def _mcp_error_status(result: dict[str, Any]) -> int | None:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        for key in ("status", "statusCode", "http_status", "httpStatus"):
            value = structured.get(key)
            if isinstance(value, int) and 100 <= value <= 599:
                return value
            if isinstance(value, str) and value.isdigit() and 100 <= int(value) <= 599:
                return int(value)
    detail = _raw_mcp_error_detail(result)
    match = re.search(r"\b(?:HTTP|status(?:\s+code)?)\s*[:=]?\s*(\d{3})\b", detail, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _raw_mcp_error_detail(result: dict[str, Any]) -> str:
    parts: list[str] = []
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        for key in ("error", "message", "detail", "code", "status", "statusCode"):
            value = structured.get(key)
            if isinstance(value, (str, int)):
                parts.append(str(value))
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
    return " ".join(parts)[:2_000]


def _safe_mcp_error_detail(result: dict[str, Any]) -> str:
    detail = _raw_mcp_error_detail(result)
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
    value = metadata.get("publishedTime") or metadata.get("publishedAt")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
