"""Minimal synchronous Streamable HTTP MCP client for configured public endpoints.

This module performs network I/O only when a caller explicitly invokes a method.
It never reads environment files, starts workers, or persists source content.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ani_rag.environment import load_project_environment
from ani_rag.firecrawl_errors import is_credit_refusal, paid_work_started, tool_credit_refusal

DEFAULT_MCP_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "mcp_servers.json"
_MAX_RESPONSE_BYTES = 25 * 1024 * 1024
_T = TypeVar("_T")
_LOG = logging.getLogger(__name__)


class _RejectEndpointRedirects(HTTPRedirectHandler):
    """MCP endpoint redirects are rejected instead of followed implicitly."""

    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


_MCP_OPENER = build_opener(_RejectEndpointRedirects())


@dataclass(frozen=True)
class MCPServerEndpoint:
    alias: str
    endpoint: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if (not self.alias.strip() or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("MCP endpoint must be a credential-free HTTPS URL with a server alias")


def load_mcp_endpoints(path: Path = DEFAULT_MCP_CONFIG_PATH) -> dict[str, MCPServerEndpoint]:
    """Load non-secret endpoint URLs; this does not connect to any server."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or payload.get("transport") != "streamable_http":
        raise ValueError("Unsupported MCP endpoint configuration")
    servers = payload.get("servers")
    if not isinstance(servers, dict):
        raise ValueError("MCP configuration must contain a servers object")
    endpoints: dict[str, MCPServerEndpoint] = {}
    for alias, item in servers.items():
        if not isinstance(item, dict) or not isinstance(item.get("endpoint"), str):
            raise ValueError(f"Invalid MCP server entry for {alias!r}")
        endpoints[alias] = MCPServerEndpoint(alias=alias, endpoint=item["endpoint"])
    return endpoints


class _CreditRefused(RuntimeError):
    credits_exhausted = True
    permanent_failure = True
    safe_summary = "Firecrawl provider credits exhausted"

    def __init__(self) -> None:
        super().__init__(self.safe_summary)


def _firecrawl_credentials() -> list[tuple[int, str]]:
    load_project_environment()
    credentials = []
    seen = set()
    for slot in range(1, 7):
        suffix = "" if slot == 1 else str(slot)
        for prefix in ("ANI_RAG_FIRECRAWL_API_KEY", "Firecrawl_API_KEY", "FIRECRAWL_API_KEY"):
            value = os.environ.get(prefix + suffix, "")
            if "\r" in value or "\n" in value:
                raise ValueError(f"Firecrawl key slot {slot} contains a line break")
            key = value.strip()
            if key:
                if key not in seen:
                    credentials.append((slot, key))
                    seen.add(key)
                break
    return credentials


class StreamableHTTPMCPClient:
    """Keep the selected credential and its MCP session together for this worker."""

    def __init__(self, server: MCPServerEndpoint, *, timeout_seconds: float = 60.0) -> None:
        self.server = server
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._sessions: list[tuple[int, _HTTPSession]] | None = None
        self._active = 0

    def initialize(self) -> dict[str, Any]:
        return self._run(lambda session: session.initialize())

    def list_tools(self) -> frozenset[str]:
        return self._run(lambda session: session.list_tools())

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._run(lambda session: session.call_tool(name, arguments))

    def _run(self, operation: Callable[[_HTTPSession], _T]) -> _T:
        while True:
            with self._lock:
                if self._sessions is None:
                    keys = _firecrawl_credentials() if self.server.alias == "firecrawl" else []
                    # Preserve anonymous and non-Firecrawl sessions when no key is configured.
                    self._sessions = [
                        (slot, _HTTPSession(self.server, self.timeout_seconds, key))
                        for slot, key in keys or [(0, "")]
                    ]
                if self._active >= len(self._sessions):
                    raise _CreditRefused()
                slot, session = self._sessions[self._active]
            try:
                return operation(session)
            except _CreditRefused:
                with self._lock:
                    # Two concurrent refusals on the same key must advance only once.
                    if self._active < len(self._sessions) and self._sessions[self._active][1] is session:
                        self._active += 1
                        if self._active < len(self._sessions):
                            _LOG.info("Firecrawl credit exhausted on slot %d; switching to slot %d",
                                      slot, self._sessions[self._active][0])


class _HTTPSession:
    """An MCP session bound to one credential; tool discovery is repeated on failover."""

    def __init__(self, server: MCPServerEndpoint, timeout_seconds: float, api_key: str) -> None:
        self.server = server
        self.timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._request_ids = count(1)
        self._handshake = threading.Lock()
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._tool_names: frozenset[str] | None = None
        self._tool_names_checked_at = 0.0

    def initialize(self) -> dict[str, Any]:
        with self._handshake:
            return self._initialize()

    def _initialize(self) -> dict[str, Any]:
        result = self._request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "ani-rag", "version": "0.1.0"},
        }, include_protocol=False)
        self._protocol_version = result.get("protocolVersion")
        if not isinstance(self._protocol_version, str) or not self._protocol_version:
            raise RuntimeError("MCP initialize response omitted protocolVersion")
        self._notification("notifications/initialized")
        return result

    def list_tools(self) -> frozenset[str]:
        with self._handshake:
            return self._list_tools()

    def _list_tools(self) -> frozenset[str]:
        if self._tool_names is not None and time.monotonic() - self._tool_names_checked_at < 60:
            return self._tool_names
        if self._protocol_version is None:
            self._initialize()
        result = self._request("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise RuntimeError("MCP tools/list response omitted tools array")
        names = {item.get("name") for item in tools if isinstance(item, dict)}
        if any(not isinstance(name, str) or not name for name in names):
            raise RuntimeError("MCP tools/list contained an invalid tool name")
        self._tool_names = frozenset(names)
        self._tool_names_checked_at = time.monotonic()
        return self._tool_names

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self.list_tools():
            raise PermissionError(f"MCP tool {name!r} is not present on alias {self.server.alias!r}")
        return self._request("tools/call", {"name": name, "arguments": arguments})

    def _request(
        self, method: str, params: dict[str, Any], *, include_protocol: bool = True
    ) -> dict[str, Any]:
        request_id = next(self._request_ids)
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        headers = self._headers(include_protocol=include_protocol)
        response = self._post(payload, headers)
        if response.get("id") != request_id:
            raise RuntimeError(f"MCP response ID did not match request for {method}")
        if "error" in response:
            error = response["error"]
            if self.server.alias == "firecrawl":
                if isinstance(error, dict):
                    data = error.get("data")
                    fields = dict(data) if isinstance(data, dict) else {}
                    fields.setdefault("message", error.get("message", ""))
                    code = error.get("code")
                    http_code = code if isinstance(code, int) and 100 <= code <= 599 else None
                    if http_code in (None, 400, 402) and not paid_work_started(error) and tool_credit_refusal(
                        {"isError": True, "structuredContent": fields}
                    ):
                        raise _CreditRefused()
                # An upstream error may echo credentials. Keep only its numeric code.
                code = error.get("code") if isinstance(error, dict) else None
                raise RuntimeError(f"Firecrawl MCP {method} failed (JSON-RPC {code if isinstance(code, int) else 'error'})")
            raise RuntimeError(f"MCP {method} failed: {error}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"MCP {method} response omitted result object")
        if self.server.alias == "firecrawl" and method == "tools/call" and tool_credit_refusal(result):
            raise _CreditRefused()
        return result

    def _notification(self, method: str) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": {}}
        self._post(payload, self._headers(include_protocol=True), allow_empty=True)

    def _headers(self, *, include_protocol: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if include_protocol and self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    def _post(
        self, payload: dict[str, Any], headers: dict[str, str], *, allow_empty: bool = False
    ) -> dict[str, Any]:
        request = Request(self.server.endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        request_id = payload.get("id")
        try:
            with _MCP_OPENER.open(request, timeout=self.timeout_seconds) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                content_type = response.headers.get("Content-Type", "")
                if "text/event-stream" in content_type.casefold():
                    return _read_matching_sse(response, request_id, allow_empty=allow_empty)
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            if self.server.alias == "firecrawl":
                # A complete billing refusal is safe to replay; a partially read error is ambiguous.
                raw_error = error.read(8193)
                if error.code in (400, 402) and len(raw_error) <= 8192:
                    detail = raw_error.decode("utf-8", "replace")
                    try:
                        envelope = json.loads(detail)
                    except ValueError:
                        envelope = None
                    if isinstance(envelope, dict):
                        fields = {"statusCode": error.code, **envelope}
                        if tool_credit_refusal({"isError": True, "structuredContent": fields}):
                            raise _CreditRefused() from None
                    elif is_credit_refusal(error.code, detail):
                        raise _CreditRefused() from None
                raise RuntimeError(f"Firecrawl MCP endpoint returned HTTP {error.code}") from None
            detail = error.read(2048).decode("utf-8", "replace")
            raise RuntimeError(f"MCP endpoint returned HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Could not reach MCP endpoint for alias {self.server.alias!r}: {error.reason}") from error
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise RuntimeError("MCP response exceeded the configured 25 MiB limit")
        if not raw:
            if allow_empty:
                return {}
            raise RuntimeError("MCP endpoint returned an empty response")
        body = raw.decode("utf-8", "replace")
        value = json.loads(body)
        if isinstance(value, list):
            match = next((item for item in value if isinstance(item, dict) and item.get("id") == payload.get("id")), None)
            if match is None:
                raise RuntimeError("MCP response batch contained no matching JSON-RPC message")
            return match
        if not isinstance(value, dict):
            raise RuntimeError("MCP endpoint returned an invalid JSON-RPC response")
        return value


def _read_matching_sse(response: Any, request_id: int | None, *, allow_empty: bool) -> dict[str, Any]:
    """Read SSE events until this request's response arrives; never wait for stream EOF."""
    data_lines: list[str] = []
    total_bytes = 0
    while True:
        line = response.readline(_MAX_RESPONSE_BYTES - total_bytes + 1)
        if not line:
            if allow_empty and total_bytes == 0:
                return {}
            break
        total_bytes += len(line)
        if total_bytes > _MAX_RESPONSE_BYTES:
            raise RuntimeError("MCP response exceeded the configured 25 MiB limit")
        decoded = line.decode("utf-8", "replace").rstrip("\r\n")
        if decoded.startswith("data:"):
            data_lines.append(decoded[5:].lstrip())
            continue
        if decoded or not data_lines:
            continue
        value_text = "\n".join(data_lines)
        data_lines.clear()
        try:
            value = json.loads(value_text)
        except json.JSONDecodeError:
            continue
        if request_id is None and allow_empty:
            return {}
        messages = value if isinstance(value, list) else [value]
        match = next((
            item for item in messages
            if isinstance(item, dict) and item.get("id") == request_id
        ), None)
        if match is not None:
            return match
    raise RuntimeError("MCP SSE response contained no matching JSON-RPC message")
