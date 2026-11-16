"""Add explicitly related ANN franchise entries to existing local work IDs.

This is a bounded, low-rate follow-up pass over already matched ANN pages. It
follows only links in ANN's encyclopedia relationship block, preserves each
page as its own raw document, and links the page to the same local parent work.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, Tag

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
ANN_HOST = "www.animenewsnetwork.com"
ANN_ENTRY_PATH = re.compile(r"^/encyclopedia/(anime|manga)\.php$")


def _read_worklist(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fields = list(reader.fieldnames or ())
    if not rows or "menu_work_id" not in fields:
        raise ValueError("ANN worklist has an unexpected or empty schema")
    if "existing_ann_urls_json" not in fields:
        raise ValueError("ANN worklist has no existing_ann_urls_json field")
    for field in ("relationship_sweep_status", "relationship_pages_read"):
        if field not in fields:
            fields.append(field)
            for row in rows:
                row[field] = ""
    return rows, fields


def _write_worklist(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=path.parent, prefix=path.stem + ".", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _related_entries(raw: bytes, source_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
    section = soup.find(id="infotype-related")
    if not isinstance(section, Tag):
        return []
    found: dict[tuple[str, str], dict[str, str]] = {}
    for anchor in section.select("a[href]"):
        href = urljoin(source_url, str(anchor.get("href") or ""))
        parsed = urlsplit(href)
        match = ANN_ENTRY_PATH.fullmatch(parsed.path)
        query = re.search(r"(?:^|&)id=([1-9][0-9]{0,7})(?:&|$)", parsed.query)
        if parsed.hostname != ANN_HOST or not match or not query:
            continue
        kind, ann_id = match.group(1), query.group(1)
        parent_text = anchor.parent.get_text(" ", strip=True) if isinstance(anchor.parent, Tag) else ""
        tail_parts: list[str] = []
        for sibling in anchor.next_siblings:
            if isinstance(sibling, Tag) and sibling.name == "br":
                break
            tail_parts.append(sibling.get_text(" ", strip=True) if isinstance(sibling, Tag) else str(sibling).strip())
        context = " ".join(part for part in (parent_text if isinstance(anchor.parent, Tag) and anchor.parent.name == "small" else "", " ".join(tail_parts)) if part).casefold()
        if "adapted from" in context or "adaptation of" in context:
            relation = "adaptation_of"
        elif "sequel" in context:
            relation = "sequel_of"
        elif "prequel" in context:
            relation = "prequel_of"
        elif "side story" in context:
            relation = "side_story_of"
        elif "spinoff" in context or "spin-off" in context:
            relation = "spinoff_of"
        elif "compilation" in context:
            relation = "compilation_of"
        elif "part of" in context:
            relation = "part_of"
        else:
            # ANN also lists magazines, publishers, staff and loosely related
            # pages here. Those are not franchise subworks without an explicit
            # sequel/adaptation/spinoff-style relation label.
            continue
        found[(kind, ann_id)] = {
            "kind": kind,
            "ann_id": ann_id,
            "url": href,
            "title": anchor.get_text(" ", strip=True),
            "relation": relation,
        }
    return list(found.values())


def _read_ann_page(url: str, *, interval: float, last_request: float | None) -> tuple[str, str, bytes]:
    source = next(item for item in load_source_registry().sources if item.source_id == ANN_SOURCE_ID)
    require_source_url(source, url)
    parsed = urlsplit(url)
    if parsed.hostname != ANN_HOST or not ANN_ENTRY_PATH.fullmatch(parsed.path):
        raise ValueError("ANN relationship URL is outside anime.php/manga.php encyclopedia pages")
    if last_request is not None:
        time.sleep(max(0.0, interval - (time.monotonic() - last_request)))
    request = Request(url, headers={"Accept": "text/html", "User-Agent": "me-nu-ani-rag/0.1 (ANN relationship lookup; low-rate)"})
    with urlopen(request, timeout=45) as response:
        final_url, status, raw = response.geturl(), response.status, response.read()
    if status != 200 or final_url != url:
        raise ValueError(f"ANN page verification failed: HTTP {status}, final URL {final_url}")
    soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title = re.sub(r"\s+-\s+Anime News Network.*$", "", title, flags=re.IGNORECASE).strip()
    for node in soup(("script", "style", "noscript", "nav", "header", "footer", "aside", "form")):
        node.decompose()
    body = soup.body.get_text("\n", strip=True) if soup.body else soup.get_text("\n", strip=True)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(body) < 100:
        raise ValueError(f"ANN page is unavailable or too short ({len(body)} characters)")
    return title, body, raw


def _entry_kind(kind: str, title: str) -> str:
    lowered = title.casefold()
    if "live-action" in lowered or "live action" in lowered:
        return "live_action"
    if kind == "manga":
        return "manga"
    if "movie" in lowered:
        return "movie"
    if "oav" in lowered or "ova" in lowered:
        return "ova"
    if "special" in lowered:
        return "special"
    return "anime"


async def _run(args: argparse.Namespace) -> None:
    if args.interval < 2.0:
        raise ValueError("ANN robots.txt specifies a 2-second crawl delay; --interval cannot be lower")
    rows, fields = _read_worklist(args.worklist)
    connection = sqlite3.connect(args.database, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    store = SQLiteRagStore(connection)
    source = next(item for item in load_source_registry().sources if item.source_id == ANN_SOURCE_ID)
    if source.status.value != "approved":
        raise PermissionError("ANN source is not approved for evidence storage")
    last_request: float | None = None
    try:
        visited_work_count = 0
        for row in rows:
            work_id = int(row["menu_work_id"])
            if work_id < args.start_id or (args.end_id is not None and work_id > args.end_id):
                continue
            if args.limit is not None and visited_work_count >= args.limit:
                break
            roots = connection.execute(
                """SELECT DISTINCT d.external_id,d.url FROM rag_document d
                   JOIN rag_work_link l ON l.document_id=d.document_id
                   WHERE d.source_id=? AND l.menu_work_id=? AND l.status='matched'
                     AND d.url LIKE 'https://www.animenewsnetwork.com/encyclopedia/%'
                   ORDER BY CASE WHEN json_extract(d.metadata_json,'$.work_match_method')='ann_explicit_relationship_graph' THEN 1 ELSE 0 END,
                            CAST(d.external_id AS INTEGER)""",
                (ANN_SOURCE_ID, work_id),
            ).fetchall()
            if not roots:
                visited_work_count += 1
                continue
            roots = roots[:args.root_limit]
            queue: deque[tuple[str, int, str | None, str]] = deque()
            for root in roots:
                queue.append((str(root["url"]), 0, None, "catalogued_entry"))
            seen: set[str] = set()
            added = 0
            conflicts: list[str] = []
            requests = 0
            while queue and requests < args.max_pages_per_work:
                url, depth, parent_ann_id, relation = queue.popleft()
                if url in seen:
                    continue
                seen.add(url)
                last_request = time.monotonic()
                try:
                    title, body, raw = _read_ann_page(url, interval=args.interval, last_request=last_request)
                    last_request = time.monotonic()
                except Exception as error:
                    last_request = time.monotonic()
                    if "429" in str(error) or "Too Many Requests" in str(error):
                        raise
                    conflicts.append(f"{url}: {type(error).__name__}")
                    continue
                requests += 1
                path_kind = urlsplit(url).path.rsplit("/", 1)[-1].split(".", 1)[0]
                ann_id_match = re.search(r"(?:^|&)id=([1-9][0-9]{0,7})(?:&|$)", urlsplit(url).query)
                if not ann_id_match:
                    continue
                ann_id = ann_id_match.group(1)
                existing = connection.execute(
                    """SELECT document_id FROM rag_document
                       WHERE source_id=? AND (url=? OR canonical_url=?)
                       ORDER BY CASE WHEN url=? THEN 0 ELSE 1 END
                       LIMIT 1""",
                    (ANN_SOURCE_ID, url, url, url),
                ).fetchone()
                document_id = str(existing[0]) if existing else "ev_" + hashlib.sha256(f"{ANN_SOURCE_ID}\n{url}\n{body}".encode("utf-8")).hexdigest()[:32]
                other_links = connection.execute(
                    "SELECT DISTINCT menu_work_id FROM rag_work_link WHERE document_id=? AND status='matched'",
                    (document_id,),
                ).fetchall() if existing else []
                other_work_ids = {int(link[0]) for link in other_links if link[0] is not None}
                if work_id in other_work_ids:
                    pass
                elif other_work_ids and relation == "related_to":
                    conflicts.append(f"ANN {ann_id} already linked to work IDs {sorted(other_work_ids)}; relation is generic, left unchanged")
                elif other_work_ids:
                    next_link = int(connection.execute(
                        "SELECT COUNT(*) FROM rag_work_link WHERE document_id=?", (document_id,),
                    ).fetchone()[0])
                    connection.execute(
                        "INSERT INTO rag_work_link(link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on) VALUES(?,?,?,?,?,?)",
                        (f"wl_{document_id}_{next_link}", document_id, work_id, None, "matched", f"ann_explicit_{relation}"),
                    )
                    metadata_row = connection.execute(
                        "SELECT metadata_json FROM rag_document WHERE document_id=?", (document_id,),
                    ).fetchone()
                    metadata = json.loads(metadata_row[0] or "{}") if metadata_row else {}
                    parent_ids = set(metadata.get("franchise_parent_work_ids", []))
                    parent_ids.add(work_id)
                    metadata["franchise_parent_work_ids"] = sorted(parent_ids)
                    metadata["franchise_relation"] = relation
                    connection.execute(
                        "UPDATE rag_document SET metadata_json=? WHERE document_id=?",
                        (json.dumps(metadata, ensure_ascii=False), document_id),
                    )
                    connection.commit()
                    added += 1
                elif work_id not in other_work_ids:
                    digest = hashlib.sha256(raw).hexdigest()
                    document = EvidenceDocument(
                        document_id=document_id,
                        content_type=RagContentType.GUIDE,
                        title=title,
                        source_url=url,
                        source_id=ANN_SOURCE_ID,
                        retrieved_at=datetime.now(timezone.utc),
                        evidence_type=EvidenceType.REFERENCE_FACT,
                        collection_method=CollectionMethod.MANUAL_IMPORT,
                        language="en",
                        source_native_id=ann_id,
                        publisher=source.name,
                        body=body,
                        raw_payload={
                            "ingest_route": "ann_explicit_relationship_graph",
                            "http_status": 200,
                            "html_sha256": digest,
                            "entry_kind": path_kind,
                            "parent_ann_id": parent_ann_id,
                            "franchise_relation": relation,
                        },
                        excerpts=(EvidenceExcerpt(text=body[:2000], locator="page:body"),),
                        work_link_status=WorkLinkStatus.MATCHED,
                        menu_work_ids=(work_id,),
                        metadata={
                            "work_match_method": "ann_explicit_relationship_graph",
                            "work_entry_kind": _entry_kind(path_kind, title),
                            "franchise_relation": relation,
                            "parent_ann_id": parent_ann_id,
                            "franchise_parent_work_ids": [work_id],
                            "ann_id": ann_id,
                        },
                    )
                    validate_document_profile(document)
                    await store.upsert_and_schedule_generation(document, schedule_generation=False)
                    added += 1
                # The root anime page lists the franchise's direct entries.
                # Also inspect manga source pages for their own spin-offs, but
                # avoid recursively walking every sibling's full network.
                if depth < args.max_depth and (depth == 0 or path_kind == "manga"):
                    for child in _related_entries(raw, url):
                        queue.append((child["url"], depth + 1, ann_id, child["relation"]))
            if added or conflicts:
                print(json.dumps({
                    "menu_work_id": work_id,
                    "root_pages": len(roots),
                    "relationship_pages_read": requests,
                    "new_documents": added,
                    "conflicts": conflicts,
                    "truncated": bool(queue),
                }, ensure_ascii=True), flush=True)
            linked_rows = connection.execute(
                """SELECT DISTINCT d.url FROM rag_document d JOIN rag_work_link l ON l.document_id=d.document_id
                   WHERE d.source_id=? AND l.menu_work_id=? AND l.status='matched'
                     AND d.url LIKE 'https://www.animenewsnetwork.com/encyclopedia/%' ORDER BY d.url""",
                (ANN_SOURCE_ID, work_id),
            ).fetchall()
            row["existing_ann_urls_json"] = json.dumps([str(item[0]) for item in linked_rows], ensure_ascii=False)
            row["ann_documents_ingested"] = str(len(linked_rows))
            row["relationship_sweep_status"] = "truncated" if queue else "complete"
            row["relationship_pages_read"] = str(requests)
            _write_worklist(args.worklist, rows, fields)
            visited_work_count += 1
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--worklist", type=Path, default=DEFAULT_WORKLIST)
    parser.add_argument("--start-id", type=int, default=1)
    parser.add_argument("--end-id", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-pages-per-work", type=int, default=100)
    parser.add_argument("--root-limit", type=int, default=1, help="How many current work pages seed relationship expansion")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    if args.start_id < 1 or (args.end_id is not None and args.end_id < args.start_id) or args.root_limit < 1:
        raise SystemExit("invalid work ID range")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
