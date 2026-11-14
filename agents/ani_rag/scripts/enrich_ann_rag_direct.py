"""Import exact ANN encyclopedia entries without using Firecrawl.

Candidate ANN IDs come from Wikidata's AniList ID (P8729) to ANN anime ID
(P1985) cross-reference. Every page is fetched from ANN directly, checked
against the local title snapshot, and stored without scheduling generation.
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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ani_rag.catalog import LocalWorkCatalog, normalize_title
from ani_rag.interfaces import WorkIdentity
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
WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
ANN_ROOT = "https://www.animenewsnetwork.com/encyclopedia/anime.php"
ANN_SEARCH_ROOT = "https://www.animenewsnetwork.com/encyclopedia/search/name"


def _read_worklist(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fields = list(reader.fieldnames or ())
    if not rows or "menu_work_id" not in fields or "anilist_ids_json" not in fields:
        raise ValueError("ANN worklist has an unexpected or empty schema")
    if "notes" not in fields:
        fields.append("notes")
        for row in rows:
            row["notes"] = ""
    if "enrichment_history_json" not in fields:
        fields.append("enrichment_history_json")
        for row in rows:
            row["enrichment_history_json"] = "[]"
    return rows, fields


def _write_worklist(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8-sig", newline="", dir=path.parent,
        prefix=f"{path.stem}.", suffix=".tmp", delete=False,
    ) as stream:
        temporary = Path(stream.name)
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _anilist_ids(rows: list[dict[str, str]]) -> list[str]:
    values: list[str] = []
    for row in rows:
        parsed = json.loads(row.get("anilist_ids_json") or "[]")
        if isinstance(parsed, list):
            values.extend(str(value) for value in parsed if str(value).isdigit())
    return list(dict.fromkeys(values))


def _wikidata_ann_ids(anilist_ids: list[str]) -> dict[str, list[str]]:
    if not anilist_ids:
        return {}
    values = " ".join(f'"{value}"' for value in anilist_ids)
    query = (
        "SELECT ?anilist ?ann WHERE { VALUES ?anilist { " + values + " } "
        "?item wdt:P8729 ?anilist; wdt:P1985 ?ann. }"
    )
    request = Request(
        WIKIDATA_ENDPOINT,
        data=urlencode({"query": query}).encode("ascii"),
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "me-nu-ani-rag/0.1 (catalog cross-reference resolution)",
        },
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        payload = json.load(response)
    found: dict[str, list[str]] = {}
    for binding in payload.get("results", {}).get("bindings", []):
        anilist = str(binding.get("anilist", {}).get("value", ""))
        ann = str(binding.get("ann", {}).get("value", ""))
        if anilist.isdigit() and ann.isdigit():
            found.setdefault(anilist, []).append(ann)
    return {key: list(dict.fromkeys(values)) for key, values in found.items()}


def _ann_title(page_title: str) -> str:
    value = re.sub(r"\s+-\s+Anime News Network.*$", "", page_title, flags=re.IGNORECASE)
    return re.sub(
        r"\s+\((?:TV(?:\s+Short)?|Movie|OVA|OAV|ONA|Web|Special)\)$",
        "", value, flags=re.IGNORECASE,
    ).strip()


def _read_ann_page(ann_id: str, *, interval: float, last_request: float | None) -> tuple[str, str, str, bytes]:
    if last_request is not None:
        time.sleep(max(0.0, interval - (time.monotonic() - last_request)))
    url = f"{ANN_ROOT}?id={ann_id}"
    require_source_url(
        next(item for item in load_source_registry().sources if item.source_id == ANN_SOURCE_ID), url,
    )
    request = Request(
        url,
        headers={
            "Accept": "text/html",
            "User-Agent": "me-nu-ani-rag/0.1 (reference ingestion; 2s interval)",
        },
    )
    with urlopen(request, timeout=45) as response:
        final_url = response.geturl()
        status = response.status
        raw = response.read()
    if status != 200 or final_url != url:
        raise ValueError(f"ANN page verification failed: HTTP {status}, final URL {final_url}")
    html = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title = _ann_title(page_title)
    for node in soup(("script", "style", "noscript", "nav", "header", "footer", "aside", "form")):
        node.decompose()
    body = soup.body.get_text("\n", strip=True) if soup.body else soup.get_text("\n", strip=True)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(body) < 100 or any(marker in body.casefold() for marker in (
        "currently unavailable", "connection broke midway", "unexpected error occured",
    )):
        raise ValueError(f"ANN page is unavailable or too short ({len(body)} characters)")
    return title, body, url, raw


def _search_ann_japanese_title(
    japanese_title: str, *, interval: float, last_request: float | None,
) -> tuple[list[str], str]:
    """Search ANN's own encyclopedia by the catalog's Japanese/native title."""
    title = japanese_title.strip()
    if not title:
        return [], ""
    if last_request is not None:
        time.sleep(max(0.0, interval - (time.monotonic() - last_request)))
    url = f"{ANN_SEARCH_ROOT}?q={quote(title)}"
    source = next(item for item in load_source_registry().sources if item.source_id == ANN_SOURCE_ID)
    require_source_url(source, url)
    request = Request(
        url,
        headers={
            "Accept": "text/html",
            "User-Agent": "me-nu-ani-rag/0.1 (Japanese title lookup; 2s interval)",
        },
    )
    with urlopen(request, timeout=45) as response:
        final_url = response.geturl()
        status = response.status
        raw = response.read()
    final = urlsplit(final_url)
    if status != 200 or final.hostname != "www.animenewsnetwork.com" or not (
        final.path.startswith("/encyclopedia/search/")
        or final.path == "/encyclopedia/anime.php"
    ):
        raise ValueError(f"ANN title search verification failed: HTTP {status}, final URL {final_url}")
    soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
    found: list[str] = []
    for anchor in soup.select('a[href*="/encyclopedia/anime.php?id="]'):
        href = str(anchor.get("href") or "")
        match = re.search(r"(?:^|/)anime\.php\?id=([1-9][0-9]{0,7})(?:&|$)", href)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found, final_url


def _title_key(value: str) -> str:
    punctuation = str.maketrans({
        "’": "'", "‘": "'", "ʼ": "'",
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    })
    return normalize_title(value).translate(punctuation)


def _match_local_work(page_title: str, body: str, catalog: LocalWorkCatalog, work_id: int) -> str | None:
    normalized_page_title = _title_key(page_title)
    # Never use arbitrary page-body mentions as identity evidence. Match a
    # canonical or alternate title only in its dedicated ANN title field.
    candidates = [page_title] if normalized_page_title else []
    lines = [line.strip() for line in body.splitlines()]
    try:
        start = lines.index("Alternative title:") + 1
        end = next(i for i in range(start, len(lines)) if lines[i] in {"Genres:", "Themes:"})
        for line in lines[start:end]:
            # ANN labels alternate titles with language tags. Consider only
            # this dedicated title block, never arbitrary plot or navigation text.
            candidates.append(re.sub(r"\s+\([^()]+\)$", "", line).strip())
    except (ValueError, StopIteration):
        pass
    exact_entry_match = False
    for candidate in dict.fromkeys(candidates):
        resolution = catalog.resolve_sync(WorkIdentity(titles=(candidate,), external_ids={}))
        if resolution.status.value == "matched" and resolution.menu_work_ids == (work_id,):
            if "live-action" not in normalized_page_title and "live action" not in normalized_page_title:
                exact_entry_match = True
    # A sub-entry can have a different title and medium. ANN's explicit
    # franchise edges (sequel, side story, spinoff, adaptation, etc.) are valid
    # identity evidence when the referenced work uniquely resolves locally.
    relation_names = {
        "adapted from": "adaptation_of", "adaptation of": "adaptation_of",
        "sequel of": "sequel_of", "prequel of": "prequel_of",
        "side story of": "side_story_of", "spinoff of": "spinoff_of",
        "spin-off of": "spinoff_of", "part of": "part_of",
        "compilation of": "compilation_of", "related to": "related_to",
    }
    for index, line in enumerate(lines):
        relation = relation_names.get(line.casefold().strip(" []:"))
        if not relation:
            continue
        source_title = next((item.strip(" []") for item in lines[index + 1:index + 5] if item.strip(" []")), "")
        source_title = re.sub(
            r"\s+\((?:TV(?:\s+Short)?(?:\s+[^()]*)?|Movie(?:\s+[^()]*)?|OVA|OAV|ONA|Web|Special|manga|light novel|novel|game|book)\)$",
            "", source_title, flags=re.IGNORECASE,
        ).strip()
        if not source_title:
            continue
        resolution = catalog.resolve_sync(WorkIdentity(titles=(source_title,), external_ids={}))
        if resolution.status.value == "matched" and resolution.menu_work_ids == (work_id,):
            return f"ann_explicit_{relation}_relation_to_local_work"
    if exact_entry_match:
        return "ann_canonical_or_alternate_title_unique_exact_local_entry"
    return None


def _relationship_metadata(match_method: str, page_title: str, body: str) -> tuple[str, str]:
    relation_match = re.fullmatch(r"ann_explicit_(.+)_relation_to_local_work", match_method)
    relation = relation_match.group(1) if relation_match else "catalogued_entry"
    title_context = f"{page_title}\n" + "\n".join(body.splitlines()[:20])
    if re.search(r"live[- ]action", title_context, flags=re.IGNORECASE):
        return "live_action_movie", "adaptation_of" if relation != "catalogued_entry" else "adaptation_of"
    for line in body.splitlines()[:40]:
        match = re.search(r"\((live-action\s+(?:movie|TV)|TV(?:\s+[^()]*)?|movie(?:\s+[^()]*)?|OAV|OVA|ONA|special)\)\s*$", line, re.IGNORECASE)
        if not match:
            continue
        kind = match.group(1).casefold()
        if kind.startswith("live-action"):
            entry_kind = "live_action_movie" if "movie" in kind else "live_action"
        elif kind.startswith("movie"):
            entry_kind = "movie"
        elif kind in {"oav", "ova"}:
            entry_kind = "ova"
        elif kind == "special":
            entry_kind = "special"
        else:
            entry_kind = "anime"
        return entry_kind, relation
    return "anime", relation


async def _run(args: argparse.Namespace) -> None:
    if args.interval < 2.0:
        raise ValueError("ANN robots.txt specifies a 2-second crawl delay; --interval cannot be lower")
    if not args.database.is_file():
        raise FileNotFoundError(f"RAG database does not exist: {args.database}")
    rows, fields = _read_worklist(args.worklist)
    candidates = [row for row in rows if row.get("lookup_status") in {
        "pending_wikidata", "pending_mcp", "read_failed",
    }]
    candidates = [row for row in candidates if int(row["menu_work_id"]) >= args.start_id]
    if args.end_id is not None:
        candidates = [row for row in candidates if int(row["menu_work_id"]) <= args.end_id]
    candidates = candidates[:args.limit]
    anilist_to_ann = _wikidata_ann_ids(_anilist_ids(candidates))
    catalog = LocalWorkCatalog()
    source = next(item for item in load_source_registry().sources if item.source_id == ANN_SOURCE_ID)
    if source.status.value != "approved":
        raise PermissionError("ANN source is not approved for evidence storage")
    connection = sqlite3.connect(args.database, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    store = SQLiteRagStore(connection)
    last_request: float | None = None
    try:
        for row in candidates:
            work_id = int(row["menu_work_id"])
            linked = connection.execute(
                """SELECT d.external_id FROM rag_document d JOIN rag_work_link l ON l.document_id=d.document_id
                   WHERE d.source_id=? AND l.menu_work_id=? AND l.status='matched'""",
                (ANN_SOURCE_ID, work_id),
            ).fetchall()
            linked_ids = {str(item[0]) for item in linked}
            anilist_ids = json.loads(row.get("anilist_ids_json") or "[]")
            manual_ids = json.loads(row.get("manual_match_ids_json") or "[]")
            crosswalk_ids = list(dict.fromkeys(
                ann_id for anilist_id in anilist_ids
                for ann_id in anilist_to_ann.get(str(anilist_id), ())
            ))
            ann_ids = list(dict.fromkeys([*crosswalk_ids, *(str(item) for item in manual_ids if str(item).isdigit())]))
            new_docs = 0
            notes: list[str] = []
            japanese_title = (row.get("title_native") or "").strip()
            attempts = list(ann_ids)
            attempted_ids = set(linked_ids)
            last_failure: str | None = None
            for ann_id in attempts:
                if ann_id in attempted_ids:
                    continue
                attempted_ids.add(ann_id)
                last_request = time.monotonic()
                try:
                    title, body, url, raw = _read_ann_page(
                        ann_id, interval=args.interval, last_request=last_request,
                    )
                    last_request = time.monotonic()
                except Exception as error:
                    last_request = time.monotonic()
                    if "429" in str(error) or "Too Many Requests" in str(error):
                        raise
                    notes.append(f"ANN {ann_id}: {type(error).__name__}")
                    last_failure = str(error)
                    continue
                match_method = _match_local_work(title, body, catalog, work_id)
                if not match_method:
                    notes.append(f"ANN {ann_id}: title mismatch {title!r}")
                    last_failure = f"title mismatch {title!r}"
                    continue
                entry_kind, franchise_relation = _relationship_metadata(match_method, title, body)
                digest = hashlib.sha256(f"{ANN_SOURCE_ID}\n{url}\n{body}".encode("utf-8")).hexdigest()
                document = EvidenceDocument(
                    document_id=f"ev_{digest[:32]}", content_type=RagContentType.GUIDE,
                    title=title, source_url=url, source_id=ANN_SOURCE_ID,
                    retrieved_at=datetime.now(timezone.utc), evidence_type=EvidenceType.REFERENCE_FACT,
                    collection_method=CollectionMethod.MANUAL_IMPORT, language="en",
                    source_native_id=ann_id, publisher=source.name, body=body,
                    raw_payload={
                        "ingest_route": "direct_html_no_firecrawl",
                        "candidate_id_source": "Wikidata AniList ID P8729 to ANN anime ID P1985",
                        "http_status": 200, "robots_crawl_delay_seconds": args.interval,
                        "html_sha256": hashlib.sha256(raw).hexdigest(),
                    },
                    excerpts=(EvidenceExcerpt(text=body[:2000], locator="page:body"),),
                    work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=(work_id,),
                    metadata={
                        "work_match_method": match_method,
                        "work_entry_kind": entry_kind,
                        "franchise_relation": franchise_relation,
                        "franchise_parent_work_ids": [work_id],
                        "ingest_route": "direct_html_no_firecrawl", "ann_id": ann_id,
                    },
                )
                validate_document_profile(document)
                stored = await store.upsert_and_schedule_generation(document, schedule_generation=False)
                linked_ids.add(ann_id)
                new_docs += stored.disposition == "inserted"
                notes.append(f"ANN {ann_id}: {stored.disposition}, {len(body)} chars")
            # Wikidata misses and title mismatches fall back to ANN's own
            # encyclopedia title search using the Japanese catalog title.
            japanese_search_completed = not japanese_title
            japanese_search_failed = False
            if japanese_title:
                try:
                    last_request = time.monotonic()
                    searched_ids, search_url = _search_ann_japanese_title(
                        japanese_title, interval=args.interval, last_request=last_request,
                    )
                    last_request = time.monotonic()
                    japanese_search_completed = True
                    notes.append(f"Japanese-title ANN search: {len(searched_ids)} candidates")
                    for ann_id in searched_ids[:25]:
                        if ann_id in attempted_ids:
                            continue
                        attempted_ids.add(ann_id)
                        last_request = time.monotonic()
                        try:
                            title, body, url, raw = _read_ann_page(
                                ann_id, interval=args.interval, last_request=last_request,
                            )
                            last_request = time.monotonic()
                        except Exception as error:
                            last_request = time.monotonic()
                            if "429" in str(error) or "Too Many Requests" in str(error):
                                raise
                            notes.append(f"ANN {ann_id}: {type(error).__name__}")
                            last_failure = str(error)
                            continue
                        match_method = _match_local_work(title, body, catalog, work_id)
                        if not match_method:
                            notes.append(f"ANN {ann_id}: Japanese-title search result did not match catalog title ({title!r})")
                            last_failure = f"title mismatch {title!r}"
                            continue
                        entry_kind, franchise_relation = _relationship_metadata(match_method, title, body)
                        digest = hashlib.sha256(f"{ANN_SOURCE_ID}\n{url}\n{body}".encode("utf-8")).hexdigest()
                        document = EvidenceDocument(
                            document_id=f"ev_{digest[:32]}", content_type=RagContentType.GUIDE,
                            title=title, source_url=url, source_id=ANN_SOURCE_ID,
                            retrieved_at=datetime.now(timezone.utc), evidence_type=EvidenceType.REFERENCE_FACT,
                            collection_method=CollectionMethod.MANUAL_IMPORT, language="en",
                            source_native_id=ann_id, publisher=source.name, body=body,
                            raw_payload={
                                "ingest_route": "direct_html_japanese_title_search",
                                "candidate_id_source": "ANN encyclopedia search/name using Japanese title",
                                "japanese_search_title": japanese_title,
                                "ann_search_url": search_url,
                                "http_status": 200, "robots_crawl_delay_seconds": args.interval,
                                "html_sha256": hashlib.sha256(raw).hexdigest(),
                            },
                            excerpts=(EvidenceExcerpt(text=body[:2000], locator="page:body"),),
                            work_link_status=WorkLinkStatus.MATCHED, menu_work_ids=(work_id,),
                            metadata={
                                "work_match_method": match_method,
                                "work_entry_kind": entry_kind,
                                "franchise_relation": franchise_relation,
                                "franchise_parent_work_ids": [work_id],
                                "ingest_route": "direct_html_japanese_title_search",
                                "ann_id": ann_id, "japanese_search_title": japanese_title,
                            },
                        )
                        validate_document_profile(document)
                        stored = await store.upsert_and_schedule_generation(document, schedule_generation=False)
                        linked_ids.add(ann_id)
                        new_docs += stored.disposition == "inserted"
                        notes.append(f"ANN {ann_id}: {stored.disposition}, {len(body)} chars (Japanese title search)")
                except Exception as error:
                    japanese_search_failed = True
                    if "429" in str(error) or "Too Many Requests" in str(error):
                        raise
                    notes.append(f"ANN Japanese-title search: {type(error).__name__}")
                    last_failure = str(error)
            row["ann_documents_ingested"] = str(len(linked_ids))
            row["existing_ann_urls_json"] = json.dumps(
                [f"{ANN_ROOT}?id={ann_id}" for ann_id in sorted(linked_ids)], ensure_ascii=False,
            )
            row["lookup_status"] = (
                "indexed" if linked_ids and japanese_search_completed
                else "pending_manual_search"
            )
            new_notes = "; ".join(notes) if notes else "Wikidata cross-reference did not contain an ANN anime ID; manual title search remains."
            old_notes = (row.get("notes") or "").strip()
            if old_notes and old_notes != new_notes:
                history = json.loads(row.get("enrichment_history_json") or "[]")
                history.append({"recorded_at": datetime.now(timezone.utc).isoformat(), "previous_notes": old_notes})
                row["enrichment_history_json"] = json.dumps(history, ensure_ascii=False)
            row["notes"] = new_notes
            _write_worklist(args.worklist, rows, fields)
            print(json.dumps({
                "menu_work_id": work_id, "candidate_ann_ids": ann_ids,
                "linked_ann_ids": sorted(linked_ids), "new_documents": new_docs,
                "status": row["lookup_status"], "notes": row["notes"],
            }, ensure_ascii=True), flush=True)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--worklist", type=Path, default=DEFAULT_WORKLIST)
    parser.add_argument("--limit", type=int, default=10, help="Maximum local works to resolve in this run")
    parser.add_argument("--start-id", type=int, default=1, help="First local work ID to consider")
    parser.add_argument("--end-id", type=int, help="Last local work ID to consider")
    parser.add_argument("--interval", type=float, default=2.0, help="Minimum seconds between ANN page reads")
    args = parser.parse_args()
    if args.limit < 1 or args.start_id < 1 or (args.end_id is not None and args.end_id < args.start_id):
        raise SystemExit("invalid limit or work ID range")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
