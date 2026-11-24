"""Concrete adapter for the configured Wikidata structured MCP tools."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ani_rag.mcp_transport import DEFAULT_MCP_CONFIG_PATH, StreamableHTTPMCPClient, load_mcp_endpoints
from ani_rag.structured_mcp import (
    StatementValue,
    StatementValuesRequest,
    StatementValuesResult,
    StructuredItemHit,
    StructuredSearchRequest,
)

_QID = re.compile(r"^(Q\d+):\s*(.*?)\s*$")
_STATEMENT = re.compile(r"^(.+?)\s+\(([QP]\d+)\):\s*(.+?)\s+\((P\d+)\):\s*(.*?)\s*$")
_DETAIL = re.compile(r"^\s+-\s+(.+?)\s+\((P\d+)\):\s*(.*?)\s*$")


class WikidataStructuredMCPAdapter:
    """Calls only the explicitly bound search_items/get_statement_values tools."""

    def __init__(self, *, config_path=DEFAULT_MCP_CONFIG_PATH) -> None:
        endpoints = load_mcp_endpoints(config_path)
        try:
            self._endpoint = endpoints["wikidata"]
        except KeyError as error:
            raise ValueError("MCP configuration must define the 'wikidata' server alias") from error
        self._client_instance: StreamableHTTPMCPClient | None = None

    async def list_tools(self, mcp_server_alias: str) -> frozenset[str]:
        if mcp_server_alias != "wikidata":
            raise PermissionError("Wikidata adapter cannot call a different MCP server alias")
        return await asyncio.to_thread(self._client().list_tools)

    async def search_items(self, request: StructuredSearchRequest) -> tuple[StructuredItemHit, ...]:
        self._check_request(request.source_id, request.mcp_server_alias, request.tool_name, "search_items")
        if not request.allowed_hosts <= {"www.wikidata.org"}:
            raise PermissionError("Wikidata structured search is restricted to the exact Wikidata item host")
        result = await asyncio.to_thread(self._call, request.tool_name, {
            "query": request.query,
            "lang": "en",
        })
        text = _result_text(result)
        hits: list[StructuredItemHit] = []
        for line in text.splitlines():
            match = _QID.match(line.strip())
            if not match:
                continue
            item_id, title_description = match.groups()
            title, separator, description = title_description.partition(" — ")
            title = title.strip()
            if not title:
                continue
            hits.append(StructuredItemHit(
                source_id=request.source_id,
                item_id=item_id,
                title=title,
                item_url=f"https://www.wikidata.org/wiki/{item_id}",
                language="en",
                mcp_server_alias="wikidata",
                tool_name=request.tool_name,
                description=description.strip() if separator and description.strip() else None,
            ))
            if len(hits) == request.limit:
                break
        return tuple(hits)

    async def get_statement_values(
        self, request: StatementValuesRequest,
    ) -> StatementValuesResult:
        self._check_request(request.source_id, request.mcp_server_alias, request.tool_name, "get_statement_values")
        if not re.fullmatch(r"Q\d+", request.item_id):
            raise ValueError("Wikidata reads require a discovered QID")
        if any(not re.fullmatch(r"P\d+", property_id) for property_id in request.property_ids):
            raise ValueError("Wikidata reads require explicit property IDs")

        values: list[StatementValue] = []
        for property_id in request.property_ids:
            result = await asyncio.to_thread(self._call, request.tool_name, {
                "entity_id": request.item_id,
                "property_id": property_id,
                "lang": "en",
            })
            values.extend(_parse_statements(_result_text(result), property_id, request.item_id))
        return StatementValuesResult(
            source_id=request.source_id,
            mcp_server_alias="wikidata",
            tool_name=request.tool_name,
            item_id=request.item_id,
            values=tuple(values),
        )

    def _check_request(self, source_id: str, alias: str, tool_name: str, expected_tool: str) -> None:
        if (source_id != "wikidata_character_catalog" or alias != "wikidata"
                or tool_name != expected_tool):
            raise PermissionError("Structured request does not match the configured Wikidata source")

    def _client(self) -> StreamableHTTPMCPClient:
        if self._client_instance is None:
            self._client_instance = StreamableHTTPMCPClient(self._endpoint)
        return self._client_instance

    def _call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._client().call_tool(tool_name, arguments)
        if result.get("isError") is True:
            raise RuntimeError(f"Wikidata MCP tool {tool_name!r} returned an error")
        return result


def _result_text(result: dict[str, Any]) -> str:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        for key in ("result", "text", "content"):
            value = structured.get(key)
            if isinstance(value, str):
                return value
    blocks = result.get("content")
    if isinstance(blocks, list):
        return "\n".join(
            item["text"] for item in blocks
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        )
    raise ValueError("Wikidata MCP response did not contain readable text")


def _parse_statements(text: str, requested_property_id: str, requested_item_id: str) -> tuple[StatementValue, ...]:
    output: list[StatementValue] = []
    current: dict[str, Any] | None = None
    current_reference: dict[str, str] | None = None

    def flush() -> None:
        nonlocal current, current_reference
        if current is not None:
            if current_reference:
                current["references"].append(current_reference)
            output.append(StatementValue(
                property_id=requested_property_id,
                value=current["value"],
                qualifiers=current["qualifiers"],
                references=tuple(current["references"]),
            ))
        current = None
        current_reference = None

    section = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        statement = _STATEMENT.match(line)
        if statement:
            flush()
            _entity_label, entity_id, _property_label, property_id, raw_value = statement.groups()
            if entity_id != requested_item_id:
                raise PermissionError("Wikidata returned a statement about another item")
            if property_id != requested_property_id:
                raise PermissionError("Wikidata returned an unrequested property")
            value_match = re.match(r"^(.*?)\s+\(([QP]\d+)\)$", raw_value.strip())
            value: object = (
                {"label": value_match.group(1), "entity_id": value_match.group(2)}
                if value_match else raw_value.strip()
            )
            current = {"value": value, "qualifiers": {}, "references": []}
            section = ""
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("Rank:"):
            current["qualifiers"]["rank"] = stripped.split(":", 1)[1].strip()
            section = ""
            continue
        if stripped.startswith("Qualifier:"):
            section = "qualifier"
            continue
        if re.match(r"^Reference\s+\d+:$", stripped):
            if current_reference:
                current["references"].append(current_reference)
            current_reference = {}
            section = "reference"
            continue
        detail = _DETAIL.match(line)
        if detail:
            label, property_id, raw_value = detail.groups()
            value_match = re.match(r"^(.*?)\s+\(([QP]\d+)\)$", raw_value.strip())
            value: object = (
                {"label": value_match.group(1), "entity_id": value_match.group(2)}
                if value_match else raw_value.strip()
            )
            item = {"label": label.strip(), "value": value}
            if section == "qualifier":
                current["qualifiers"][property_id] = item
            elif section == "reference":
                if current_reference is None:
                    current_reference = {}
                current_reference[property_id] = item
    flush()
    return tuple(output)
