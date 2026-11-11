"""Explicit loader for the RAG-only source allowlist; it never connects to MCP."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ani_rag.models import RagContentType

DEFAULT_SOURCE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "sources.evergreen.json"
_SOURCE_ID = re.compile(r"^[a-z0-9_]+$")


class SourceStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    DISABLED = "disabled"


@dataclass(frozen=True)
class EvergreenSource:
    source_id: str
    name: str
    mcp_server: str
    content_types: tuple[RagContentType, ...]
    allowed_tools: frozenset[str]
    status: SourceStatus
    usage_notes: str
    allowed_hosts: frozenset[str] = frozenset()
    allowed_path_prefixes: tuple[str, ...] = ()
    allowed_media_hosts: frozenset[str] = frozenset()
    tool_bindings: tuple[tuple[str, str], ...] = ()
    topic_id: int | None = None
    source_kind: str | None = None
    collect_method: str = "mcp"
    site_url: str | None = None
    feed_url: str | None = None
    source_grade: str | None = None
    copyright_status: str | None = None
    license_basis: str | None = None
    is_collecting: bool = False

    def __post_init__(self) -> None:
        if not _SOURCE_ID.fullmatch(self.source_id):
            raise ValueError(f"Invalid source_id: {self.source_id!r}")
        if not self.name.strip() or not self.mcp_server.strip():
            raise ValueError("Source name and MCP server alias are required")
        if not self.content_types:
            raise ValueError(f"Source {self.source_id} must declare content_types")
        if self.topic_id is not None and self.topic_id <= 0:
            raise ValueError("topic_id must be a positive ERD topic identifier")
        if not self.collect_method.strip():
            raise ValueError("collect_method is required")
        for field_name in ("source_kind", "site_url", "feed_url", "source_grade", "copyright_status", "license_basis"):
            value = getattr(self, field_name)
            if value is not None and not value.strip():
                raise ValueError(f"{field_name} cannot be blank when supplied")
        if not self.allowed_tools or not self.allowed_tools <= {"search", "read"}:
            raise ValueError("allowed_tools may contain only search/read and cannot be empty")
        for host in self.allowed_hosts:
            parsed = urlsplit(f"https://{host}")
            if parsed.hostname != host or parsed.path or parsed.username or parsed.password:
                raise ValueError(f"allowed_hosts must contain bare exact host names: {host!r}")
        for host in self.allowed_media_hosts:
            parsed = urlsplit(f"https://{host}")
            if parsed.hostname != host or parsed.path or parsed.username or parsed.password:
                raise ValueError(f"allowed_media_hosts must contain bare exact host names: {host!r}")
        for prefix in self.allowed_path_prefixes:
            if not prefix.startswith("/") or "?" in prefix or "#" in prefix or ".." in prefix.split("/"):
                raise ValueError(f"allowed_path_prefixes must be safe absolute URL paths: {prefix!r}")
        bindings = dict(self.tool_bindings)
        if len(bindings) != len(self.tool_bindings) or not set(bindings) <= {"search", "read"}:
            raise ValueError("tool_bindings must map unique search/read operations to MCP tool names")
        if any(not tool_name.strip() for tool_name in bindings.values()):
            raise ValueError("MCP tool names in tool_bindings cannot be empty")
        if self.status is SourceStatus.APPROVED and (
            not self.allowed_hosts or set(bindings) != set(self.allowed_tools)
        ):
            raise ValueError("Approved sources need exact hosts and a concrete MCP tool name for every allowed operation")
        if self.status is SourceStatus.APPROVED and not all((
            self.source_kind, self.site_url, self.source_grade,
            self.copyright_status, self.license_basis,
        )):
            raise ValueError("Approved sources must include source kind, site URL, grade, copyright, and license fields")
        if self.site_url:
            parsed_site = urlsplit(self.site_url)
            site_host = (parsed_site.hostname or "").casefold().rstrip(".")
            if parsed_site.scheme not in {"http", "https"} or not site_host or parsed_site.username or parsed_site.password:
                raise ValueError("site_url must be a credential-free absolute HTTP(S) URL")
            if self.allowed_hosts and site_host not in {host.casefold().rstrip(".") for host in self.allowed_hosts}:
                raise ValueError("site_url host must be present in the exact source host allowlist")


@dataclass(frozen=True)
class EvergreenSourceRegistry:
    version: int
    description: str
    sources: tuple[EvergreenSource, ...]

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ValueError(f"Unsupported evergreen source config version: {self.version}")
        ids = [source.source_id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("Evergreen source IDs must be unique")

    @property
    def approved_sources(self) -> tuple[EvergreenSource, ...]:
        """Return explicitly approved sources; this does not run or connect tools."""
        return tuple(source for source in self.sources if source.status is SourceStatus.APPROVED)


def load_source_registry(path: Path = DEFAULT_SOURCE_CONFIG_PATH) -> EvergreenSourceRegistry:
    """Parse and validate the local JSON allowlist without network or DB access."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise ValueError("Evergreen source config must contain a sources array")

    sources: list[EvergreenSource] = []
    for index, item in enumerate(payload["sources"]):
        if not isinstance(item, dict):
            raise ValueError(f"Source at index {index} must be an object")
        try:
            bindings = item.get("tool_bindings", {})
            if not isinstance(bindings, dict):
                raise ValueError("tool_bindings must be an object mapping operations to tool names")
            sources.append(
                EvergreenSource(
                    source_id=item["source_id"],
                    name=item["name"],
                    mcp_server=item["mcp_server"],
                    content_types=tuple(RagContentType(value) for value in item["content_types"]),
                    allowed_tools=frozenset(item["allowed_tools"]),
                    status=SourceStatus(item["status"]),
                    usage_notes=item.get("usage_notes", ""),
                    allowed_hosts=frozenset(host.casefold().rstrip(".") for host in _host_list(item.get("allowed_hosts", []))),
                    allowed_path_prefixes=tuple(_path_prefixes(item.get("allowed_path_prefixes", []))),
                    allowed_media_hosts=frozenset(host.casefold().rstrip(".") for host in _host_list(item.get("allowed_media_hosts", []))),
                    tool_bindings=tuple(bindings.items()),
                    topic_id=item.get("topic_id"),
                    source_kind=item.get("source_kind"),
                    collect_method=item.get("collect_method", "mcp"),
                    site_url=item.get("site_url"),
                    feed_url=item.get("feed_url"),
                    source_grade=item.get("source_grade"),
                    copyright_status=item.get("copyright_status"),
                    license_basis=item.get("license_basis"),
                    is_collecting=item.get("is_collecting", False),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid evergreen source at index {index}: {error}") from error

    return EvergreenSourceRegistry(
        version=payload.get("version"),
        description=payload.get("description", ""),
        sources=tuple(sources),
    )


def require_source_access(
    registry: EvergreenSourceRegistry,
    source_id: str,
    tool: str,
    content_type: RagContentType | None = None,
) -> EvergreenSource:
    """Authorize one configured source/tool pair without invoking the tool."""
    source = next((item for item in registry.sources if item.source_id == source_id), None)
    if source is None:
        raise PermissionError(f"Source {source_id!r} is not configured")
    if source.status is not SourceStatus.APPROVED:
        raise PermissionError(f"Source {source_id!r} is not approved")
    if tool not in source.allowed_tools:
        raise PermissionError(f"Tool {tool!r} is not allowed for source {source_id!r}")
    if tool not in dict(source.tool_bindings):
        raise PermissionError(f"No verified MCP tool name is bound for {tool!r} on source {source_id!r}")
    if content_type is not None and content_type not in source.content_types:
        raise PermissionError(
            f"Content type {content_type.value!r} is not allowed for source {source_id!r}"
        )
    return source


def require_source_url(source: EvergreenSource, url: str) -> None:
    """Reject redirects or results outside the source's explicitly listed exact hosts."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise PermissionError("Source URL must be a credential-free absolute HTTP(S) URL")
    try:
        port = parsed.port
    except ValueError as error:
        raise PermissionError("Source URL has an invalid port") from error
    if port is not None and port not in ({80} if parsed.scheme == "http" else {443}):
        raise PermissionError("Non-standard ports are outside the source host allowlist")
    if not source.allowed_hosts or host not in source.allowed_hosts:
        raise PermissionError(f"Host {host!r} is outside source {source.source_id!r} allowlist")
    if source.allowed_path_prefixes:
        path = unquote(parsed.path)
        if any(part == ".." for part in path.split("/")) or not any(
            path == prefix.rstrip("/") or path.startswith(prefix)
            for prefix in source.allowed_path_prefixes
        ):
            raise PermissionError(f"URL path {path!r} is outside source {source.source_id!r} path scope")


def require_source_media_url(source: EvergreenSource, url: str) -> None:
    """Allow image downloads only from a source's separately reviewed media hosts."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise PermissionError("Image URL must be a credential-free HTTPS URL")
    try:
        port = parsed.port
    except ValueError as error:
        raise PermissionError("Image URL has an invalid port") from error
    if port not in (None, 443):
        raise PermissionError("Non-standard image ports are outside the media allowlist")
    if not source.allowed_media_hosts or host not in source.allowed_media_hosts:
        raise PermissionError(f"Image host {host!r} is outside source {source.source_id!r} media allowlist")


def _host_list(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(host, str) or not host.strip() for host in value):
        raise ValueError("allowed_hosts must be an array of bare host strings")
    return value


def _path_prefixes(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(path, str) or not path.strip() for path in value):
        raise ValueError("allowed_path_prefixes must be an array of absolute URL path prefixes")
    return value
