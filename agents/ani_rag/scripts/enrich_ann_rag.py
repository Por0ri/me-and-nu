"""Build the ANN reference corpus for local work IDs without dispatching generation."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ani_rag.adapter import ReadRequest, SearchRequest
from ani_rag.catalog import LocalWorkCatalog, normalize_title
from ani_rag.firecrawl_adapter import FirecrawlMCPAdapter, MCPToolCallError
from ani_rag.models import (
    CollectionMethod,
    EvidenceDocument,
    EvidenceExcerpt,
    EvidenceType,
    RagContentType,
    WorkLinkStatus,
)
from ani_rag.profiles import validate_document_profile
from ani_rag.settings import load_source_registry, require_source_url
from ani_rag.storage.sqlite_core import SQLiteRagStore

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ani_rag_live_20261111.sqlite3"
DEFAULT_WORKLIST = ROOT / "data" / "plans" / "ann_enrichment_worklist_20261111.csv"
ANN_SOURCE_ID = "ann_encyclopedia"
ERROR_PAGE_MARKERS = (
    "currently unavailable",
    "connection broke midway",
    "unexpected error occured",
)


def _ann_title(value: str) -> str:
    value = re.sub(r"\s+-\s+Anime News Network\s*$", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(
        r"\s+\((?:TV(?:\s+Short)?|Movie|OVA|OAV|ONA|Web|Special)\)\s*$",
        "", value, flags=re.IGNORECASE,
    ).strip()
    return normalize_title(value)


def _work_titles(catalog: LocalWorkCatalog, work_id: int) -> set[str]:
    return {_ann_title(value) for value in catalog.titles_for_work_id(work_id) if value.strip()}


def _entry_id(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.hostname != "www.animenewsnetwork.com" or parsed.path != "/encyclopedia/anime.php":
        return None
    values = parse_qs(parsed.query).get("id", ())
    return values[0] if len(values) == 1 and values[0].isdigit() else None


def _read_worklist(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fields = list(reader.fieldnames or ())
    if not rows or "menu_work_id" not in fields or "query_titles_json" not in fields:
        raise ValueError("ANN worklist has an unexpected or empty schema")
    for field in ("notes",):
        if field not in fields:
            fields.append(field)
            for row in rows:
                row[field] = ""
    return rows, fields


def _write_worklist(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8-sig", newline="", dir=path.parent,
        prefix=f"{path.stem}.", suffix=".tmp", delete=False,
    ) as stream:
        temporary = Path(stream.name)
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _already_linked(connection: sqlite3.Connection, work_id: int) -> int:
    row = connection.execute(
        """SELECT count(DISTINCT d.document_id)
           FROM rag_document d JOIN rag_work_link l ON l.document_id=d.document_id
           WHERE d.source_id=? AND l.menu_work_id=? AND l.status='matched'""",
        (ANN_SOURCE_ID, work_id),
    ).fetchone()
    return int(row[0])


def _stored_native_id(connection: sqlite3.Connection, native_id: str) -> tuple[str, int | None] | None:
    row = connection.execute(
        """SELECT d.document_id,l.menu_work_id FROM rag_document d
           LEFT JOIN rag_work_link l ON l.document_id=d.document_id AND l.status='matched'
           WHERE d.source_id=? AND d.external_id=? LIMIT 1""",
        (ANN_SOURCE_ID, native_id),
    ).fetchone()
    return (str(row[0]), int(row[1]) if row[1] is not None else None) if row else None


async def _collect_one(
    row: dict[str, str], *, adapter: FirecrawlMCPAdapter,
    catalog: LocalWorkCatalog, store: SQLiteRagStore,
    connection: sqlite3.Connection, interval: float,
) -> tuple[str, int, str]:
    work_id = int(row["menu_work_id"])
    titles = json.loads(row["query_titles_json"])
    if not isinstance(titles, list) or not titles or not all(isinstance(v, str) and v.strip() for v in titles):
        return "invalid_titles", 0, "No usable canonical search title"
    query = titles[0]
    source = next(item for item in load_source_registry().sources if item.source_id == ANN_SOURCE_ID)
    if source.status.value != "approved":
        return "source_not_approved", 0, "ANN source is not approved"
    tools = dict(source.tool_bindings)
    if not {tools.get("search"), tools.get("read")} <= await adapter.list_tools(source.mcp_server):
        return "tool_inventory_mismatch", 0, "Configured Firecrawl tools are not present"

    await asyncio.sleep(interval)
    hits = await adapter.search(SearchRequest(
        source_id=ANN_SOURCE_ID, query=f"{query} Anime News Network Encyclopedia",
        mcp_server_alias=source.mcp_server, tool_name=tools["search"],
        allowed_hosts=source.allowed_hosts, limit=5,
    ))
    searches = 1
    allowed_titles = _work_titles(catalog, work_id)
    exact_hits = [
        hit for hit in hits
        if _entry_id(hit.url) is not None and _ann_title(hit.title) in allowed_titles
    ]
    if not exact_hits:
        return "not_found", searches, "No exact ANN anime title matched the local work/entry titles"

    errors: list[str] = []
    for hit in exact_hits:
        native_id = _entry_id(hit.url)
        if native_id is None:
            continue
        existing = _stored_native_id(connection, native_id)
        if existing and existing[1] == work_id:
            return "already_indexed", searches, f"ANN entry {native_id} is already linked"
        require_source_url(source, hit.url)
        await asyncio.sleep(interval)
        result = await adapter.read(ReadRequest(
            source_id=ANN_SOURCE_ID, url=hit.url, mcp_server_alias=source.mcp_server,
            tool_name=tools["read"], allowed_hosts=source.allowed_hosts,
            allowed_path_prefixes=source.allowed_path_prefixes,
        ))
        if result.source_id != ANN_SOURCE_ID or result.mcp_server_alias != source.mcp_server or result.tool_name != tools["read"]:
            errors.append(f"entry {native_id}: source/tool attestation mismatch")
            continue
        if not result.redirects_verified or result.url != hit.url:
            errors.append(f"entry {native_id}: final URL/redirect not verified")
            continue
        if result.source_native_id != native_id or _ann_title(result.title) not in allowed_titles:
            errors.append(f"entry {native_id}: read title or native ID differs from local match")
            continue
        body = result.body or ""
        if len(body) < 100 or any(marker in body.casefold() for marker in ERROR_PAGE_MARKERS):
            errors.append(f"entry {native_id}: page returned an error/empty body")
            continue
        digest = hashlib.sha256(f"{ANN_SOURCE_ID}\n{result.url}\n{body}".encode("utf-8")).hexdigest()
        document = EvidenceDocument(
            document_id=f"ev_{digest[:32]}", content_type=RagContentType.GUIDE,
            title=result.title, source_url=result.url, source_id=ANN_SOURCE_ID,
            retrieved_at=datetime.now(timezone.utc), evidence_type=EvidenceType.REFERENCE_FACT,
            collection_method=CollectionMethod.MCP_READ, language=result.language or "en",
            source_native_id=native_id, publisher=result.publisher or source.name,
            body=body,
            raw_payload={
                **(result.raw_payload or {}), "mcp_server_alias": source.mcp_server,
                "mcp_tool": tools["read"], "search_query": query,
                "search_hit_title": hit.title, "search_hit_url": hit.url,
            },
            excerpts=(EvidenceExcerpt(text=body[:2000], locator="page:main"),),
            work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=(work_id,),
            metadata={
                "mcp_server_alias": source.mcp_server, "mcp_tool": "read",
                "work_match_method": "exact_local_catalog_title",
                "work_match_seed_menu_work_id": str(work_id),
            },
        )
        validate_document_profile(document)
        stored = await store.upsert_and_schedule_generation(document, schedule_generation=False)
        return "indexed", searches, f"{stored.document_id}; ANN ID {native_id}; {len(body)} characters"
    return "read_failed", searches, "; ".join(errors)[:800]


async def _run(args: argparse.Namespace) -> None:
    database = args.database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"RAG DB does not exist: {database}")
    rows, fields = _read_worklist(args.worklist)
    catalog = LocalWorkCatalog()
    adapter = FirecrawlMCPAdapter()
    connection = sqlite3.connect(database, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    store = SQLiteRagStore(connection)
    processed = 0
    try:
        for row in rows:
            if processed >= args.limit:
                break
            if row["lookup_status"] not in {"pending_mcp", "read_failed", "not_found"}:
                continue
            wid = int(row["menu_work_id"])
            linked = _already_linked(connection, wid)
            if linked:
                row["lookup_status"] = "already_indexed"
                row["ann_documents_ingested"] = str(linked)
                row["existing_ann_urls_json"] = json.dumps([
                    item[0] for item in connection.execute(
                        """SELECT d.url FROM rag_document d JOIN rag_work_link l ON l.document_id=d.document_id
                           WHERE d.source_id=? AND l.menu_work_id=? AND l.status='matched' ORDER BY d.url""",
                        (ANN_SOURCE_ID, wid),
                    ).fetchall()
                ], ensure_ascii=False)
                continue
            try:
                status, searches, note = await _collect_one(
                    row, adapter=adapter, catalog=catalog, store=store,
                    connection=connection, interval=args.interval,
                )
            except MCPToolCallError as error:
                row["lookup_status"] = "blocked_rate_limited" if error.rate_limited else "blocked_operator"
                row["notes"] = str(error.safe_summary)[:800]
                _write_worklist(args.worklist, rows, fields)
                print(json.dumps({"stopped": True, "menu_work_id": wid,
                                  "status": row["lookup_status"], "note": row["notes"]}, ensure_ascii=False), flush=True)
                break
            except Exception as error:
                error_text = str(error).casefold()
                rate_limited = "http 429" in error_text or "rate limit" in error_text or "too many requests" in error_text
                auth_failed = "http 401" in error_text or "http 403" in error_text or "authentication required" in error_text
                if rate_limited or auth_failed:
                    row["lookup_status"] = "blocked_rate_limited" if rate_limited else "blocked_operator"
                    row["notes"] = "Firecrawl returned a rate-limit response" if rate_limited else "Firecrawl authentication/authorization needs operator review"
                    _write_worklist(args.worklist, rows, fields)
                    print(json.dumps({"stopped": True, "menu_work_id": wid,
                                      "status": row["lookup_status"], "note": row["notes"]}, ensure_ascii=False), flush=True)
                    break
                status, searches, note = "error", 0, type(error).__name__
            row["lookup_status"] = status
            row["ann_searches_run"] = str(int(row.get("ann_searches_run") or 0) + searches)
            row["ann_documents_ingested"] = str(_already_linked(connection, wid))
            row["notes"] = note
            if status == "indexed":
                row["existing_ann_urls_json"] = json.dumps([
                    item[0] for item in connection.execute(
                        """SELECT d.url FROM rag_document d JOIN rag_work_link l ON l.document_id=d.document_id
                           WHERE d.source_id=? AND l.menu_work_id=? AND l.status='matched' ORDER BY d.url""",
                        (ANN_SOURCE_ID, wid),
                    ).fetchall()
                ], ensure_ascii=False)
            processed += 1
            _write_worklist(args.worklist, rows, fields)
            print(json.dumps({"menu_work_id": wid, "status": status, "note": note}, ensure_ascii=False), flush=True)
    finally:
        connection.close()
    print(json.dumps({"processed": processed, "remaining": sum(row["lookup_status"] in {"pending_mcp", "read_failed", "not_found"} for row in rows)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--worklist", type=Path, default=DEFAULT_WORKLIST)
    parser.add_argument("--limit", type=int, default=10, help="Maximum works to attempt in this bounded batch")
    parser.add_argument("--interval", type=float, default=6.0, help="Seconds between search/read calls")
    args = parser.parse_args()
    if args.limit < 1 or args.interval < 0:
        raise SystemExit("--limit must be positive and --interval cannot be negative")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
