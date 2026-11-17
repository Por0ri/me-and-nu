"""MCP search/read boundary. This module defines interfaces and performs no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence
from urllib.parse import unquote, urlsplit, urlunsplit

from ani_rag.models import EvidenceExcerpt


def canonical_source_url(url: str) -> str:
    """Normalize URL syntax only, preserving query parameters and original URL separately."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Source URL must be a credential-free absolute HTTP(S) URL")
    host = parsed.hostname.casefold().rstrip(".")
    port = parsed.port
    netloc = host if port is None or (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80) else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.casefold(), netloc, path, parsed.query, ""))


@dataclass(frozen=True)
class SearchRequest:
    """A bounded query scoped to one explicitly configured source."""

    source_id: str
    query: str
    mcp_server_alias: str
    tool_name: str
    allowed_hosts: frozenset[str]
    limit: int = 10

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.source_id, self.query, self.mcp_server_alias, self.tool_name)) or not self.allowed_hosts:
            raise ValueError("Search requires source, server alias, exact MCP tool name, and query")
        if not 1 <= self.limit <= 100:
            raise ValueError("Search limit must be between 1 and 100")


@dataclass(frozen=True)
class ReadRequest:
    """Read one result URL through the MCP server registered for source_id."""

    source_id: str
    url: str
    mcp_server_alias: str
    tool_name: str
    allowed_hosts: frozenset[str]
    allowed_path_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.source_id, self.url, self.mcp_server_alias, self.tool_name)) or not self.allowed_hosts:
            raise ValueError("URL read requires source, URL, server alias, and exact MCP tool name")
        parsed_url = urlsplit(self.url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.username or parsed_url.password:
            raise ValueError("Read URL must be a credential-free absolute HTTP(S) URL")
        host = (parsed_url.hostname or "").casefold().rstrip(".")
        if not self.allowed_hosts or host not in self.allowed_hosts:
            raise ValueError("Read URL host must match the exact source allowlist")
        if self.allowed_path_prefixes:
            path = unquote(parsed_url.path)
            if any(part == ".." for part in path.split("/")) or not any(
                path == prefix.rstrip("/") or path.startswith(prefix)
                for prefix in self.allowed_path_prefixes
            ):
                raise ValueError("Read URL path is outside the exact source path scope")
        if parsed_url.port is not None and parsed_url.port not in ({80} if parsed_url.scheme == "http" else {443}):
            raise ValueError("Read URL must not use a non-standard port")


@dataclass(frozen=True)
class SearchHit:
    source_id: str
    title: str
    url: str
    mcp_server_alias: str
    tool_name: str
    snippet: str | None = None
    published_at: datetime | None = None
    language: str | None = None
    source_native_id: str | None = None

    def __post_init__(self) -> None:
        _validate_result(self.source_id, self.title, self.url, self.published_at)
        if not self.mcp_server_alias.strip() or not self.tool_name.strip():
            raise ValueError("Search result must identify the MCP server alias and tool used")


@dataclass(frozen=True)
class ReadResult:
    source_id: str
    title: str
    url: str
    body: str | None
    mcp_server_alias: str
    tool_name: str
    language: str | None = None
    published_at: datetime | None = None
    excerpts: tuple[EvidenceExcerpt, ...] = ()
    source_native_id: str | None = None
    publisher: str | None = None
    raw_payload: dict[str, object] | None = None
    redirect_chain: tuple[str, ...] = ()
    redirects_verified: bool = False

    def __post_init__(self) -> None:
        _validate_result(self.source_id, self.title, self.url, self.published_at)
        if not self.mcp_server_alias.strip() or not self.tool_name.strip():
            raise ValueError("Read result must identify the MCP server alias and tool used")
        if not (self.body and self.body.strip()) and not self.excerpts:
            raise ValueError("Read result needs body text or at least one evidence excerpt")


def _validate_result(
    source_id: str, title: str, url: str, published_at: datetime | None
) -> None:
    if not source_id.strip() or not title.strip():
        raise ValueError("MCP result must preserve source_id and title")
    parsed_url = urlsplit(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.username or parsed_url.password:
        raise ValueError("MCP result URL must be a credential-free absolute HTTP(S) URL")
    if published_at is not None and (
        published_at.tzinfo is None or published_at.utcoffset() is None
    ):
        raise ValueError("published_at must be timezone-aware")


class MCPReadSearchAdapter(Protocol):
    """Port only. A concrete transport must check the live server tool list and
    enforce allowed_hosts before following every redirect and set redirects_verified
    only when it did so; post-read checks alone are too late to prevent an out-of-scope request."""

    async def list_tools(self, mcp_server_alias: str) -> frozenset[str]:
        """Return tool names from the live server connection for explicit verification."""

    async def search(self, request: SearchRequest) -> Sequence[SearchHit]:
        """Search the registered source; must not expand to unapproved sources."""

    async def read(self, request: ReadRequest) -> ReadResult:
        """Read a URL surfaced by an approved source search."""
