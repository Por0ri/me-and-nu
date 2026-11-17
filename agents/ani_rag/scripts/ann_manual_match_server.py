"""Loopback-only UI for manually matching ANN encyclopedia entries to local works."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(SCRIPTS))

from ani_rag.catalog import LocalWorkCatalog, normalize_title  # noqa: E402
from ani_rag.models import (  # noqa: E402
    CollectionMethod,
    EvidenceDocument,
    EvidenceExcerpt,
    EvidenceType,
    RagContentType,
    WorkLinkStatus,
)
from ani_rag.profiles import validate_document_profile  # noqa: E402
from ani_rag.settings import load_source_registry  # noqa: E402
from ani_rag.storage.sqlite_core import SQLiteRagStore  # noqa: E402
from enrich_ann_rag_direct import (  # noqa: E402
    ANN_ROOT,
    ANN_SOURCE_ID,
    DEFAULT_DB,
    DEFAULT_WORKLIST,
    _read_ann_page,
    _read_worklist,
    _write_worklist,
)

PAGE = ROOT / "preview" / "ann_manual_match.html"
CACHE_TTL_SECONDS = 30 * 60
MAX_REQUEST_BYTES = 32_000
NEW_FIELDS = ("manual_match_ids_json", "manual_match_history_json", "manual_mapped_to_work_id")


def _json_list(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _ensure_fields(rows: list[dict[str, str]], fields: list[str]) -> list[str]:
    for field in NEW_FIELDS:
        if field not in fields:
            fields.append(field)
        for row in rows:
            row.setdefault(field, "")
    return fields


class ManualMatchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.catalog = LocalWorkCatalog()
        self.work_titles = {
            seed.menu_work_id: self.catalog.titles_for_work_id(seed.menu_work_id)
            for seed in self.catalog.work_search_seeds()
        }
        for work_id in range(1, self.catalog.max_work_id + 1):
            self.work_titles.setdefault(work_id, self.catalog.titles_for_work_id(work_id))
        self.preview_cache: dict[str, dict] = {}
        self.cache_lock = threading.Lock()
        self.fetch_lock = threading.Lock()
        self.last_ann_request: float | None = None


class ManualMatchHandler(BaseHTTPRequestHandler):
    server_version = "ani_rag_ann_manual_match/1.0"

    @property
    def app(self) -> ManualMatchServer:
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif parsed.path == "/api/manual-worklist":
            include_completed = parse_qs(parsed.query).get("include_completed", ["0"])[0] == "1"
            self._json(200, self._manual_worklist(include_completed))
        elif parsed.path == "/api/catalog-search":
            query = parse_qs(parsed.query).get("q", [""])[0].strip()
            self._json(200, self._catalog_search(query))
        else:
            self._json(404, {"error": "경로를 찾을 수 없습니다."})

    def do_POST(self) -> None:
        if not self._is_loopback_request():
            self._json(403, {"error": "로컬 브라우저 요청만 허용됩니다."})
            return
        try:
            payload = self._read_json()
            if urlsplit(self.path).path == "/api/ann-preview":
                self._json(200, self._preview_ann(payload))
            elif urlsplit(self.path).path == "/api/ann-match":
                self._json(200, self._apply_match(payload))
            elif urlsplit(self.path).path == "/api/complete-manual-work":
                self._json(200, self._complete_manual_work(payload))
            else:
                self._json(404, {"error": "경로를 찾을 수 없습니다."})
        except ValueError as error:
            self._json(400, {"error": str(error)})
        except PermissionError as error:
            self._json(403, {"error": str(error)})
        except FileNotFoundError as error:
            self._json(404, {"error": str(error)})
        except sqlite3.IntegrityError as error:
            self._json(409, {"error": f"DB 연결 충돌로 저장하지 못했습니다: {error}"})
        except Exception as error:
            self._json(500, {"error": f"처리 실패 ({type(error).__name__}): {error}"})

    def _is_loopback_request(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").casefold()
        if host not in {"127.0.0.1", "localhost"}:
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlsplit(origin).hostname in {"127.0.0.1", "localhost"}

    def _read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("요청 크기를 확인할 수 없습니다.") from error
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ValueError("요청 크기가 올바르지 않습니다.")
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            raise ValueError("JSON 요청을 읽을 수 없습니다.") from error
        if not isinstance(data, dict):
            raise ValueError("요청 형식이 올바르지 않습니다.")
        return data

    def _manual_worklist(self, include_completed: bool) -> dict:
        rows, _ = _read_worklist(DEFAULT_WORKLIST)
        open_rows = []
        completed_rows = []
        status_counts: dict[str, int] = {}
        for row in rows:
            status = row.get("lookup_status", "")
            status_counts[status] = status_counts.get(status, 0) + 1
            manual_ids = _json_list(row.get("manual_match_ids_json"))
            item = {
                "menu_work_id": int(row["menu_work_id"]),
                "title_english": row.get("title_english", ""),
                "title_romaji": row.get("title_romaji", ""),
                "title_native": row.get("title_native", ""),
                "title_ko": row.get("title_ko", ""),
                "anilist_ids": _json_list(row.get("anilist_ids_json")),
                "entry_count": row.get("entry_count", ""),
                "lookup_status": status,
                "notes": row.get("notes", ""),
                "ann_documents_ingested": int(row.get("ann_documents_ingested") or 0),
                "existing_ann_urls": _json_list(row.get("existing_ann_urls_json")),
                "manual_match_ids": manual_ids,
                "manual_match_history": _json_list(row.get("manual_match_history_json")),
                "manual_mapped_to_work_id": row.get("manual_mapped_to_work_id", ""),
            }
            if status == "pending_manual_search":
                open_rows.append(item)
            elif include_completed and (manual_ids or status == "manual_mapped_elsewhere"):
                completed_rows.append(item)
        return {
            "total_catalog_works": len(rows),
            "open_count": len(open_rows),
            "completed_count": len(completed_rows),
            "status_counts": status_counts,
            "items": open_rows + completed_rows,
        }

    def _catalog_search(self, query: str) -> dict:
        if not query:
            return {"items": []}
        needle = normalize_title(query)
        matches = []
        for work_id, titles in self.app.work_titles.items():
            found = [title for title in titles if needle in normalize_title(title)]
            if found:
                matches.append({"menu_work_id": work_id, "titles": list(titles), "matched_titles": found})
        matches.sort(key=lambda item: (item["menu_work_id"], normalize_title(item["matched_titles"][0])))
        return {"items": matches[:60], "truncated": len(matches) > 60}

    def _preview_ann(self, payload: dict) -> dict:
        ann_id = self._validate_ann_id(payload.get("ann_id"))
        cached = self._cached_page(ann_id)
        if cached is None:
            source = next(item for item in load_source_registry().sources if item.source_id == ANN_SOURCE_ID)
            if source.status.value != "approved":
                raise PermissionError("ANN 출처가 승인 상태가 아니어서 페이지를 확인할 수 없습니다.")
            with self.app.fetch_lock:
                last_request = self.app.last_ann_request
                self.app.last_ann_request = time.monotonic()
                title, body, url, raw = _read_ann_page(
                    ann_id, interval=2.0, last_request=last_request,
                )
                fetched_at = datetime.now(timezone.utc)
                cached = {
                    "ann_id": ann_id, "title": title, "body": body, "url": url,
                    "raw": raw, "fetched_at": fetched_at,
                    "html_sha256": hashlib.sha256(raw).hexdigest(),
                    "cached_monotonic": time.monotonic(),
                }
                with self.app.cache_lock:
                    self.app.preview_cache[ann_id] = cached
        existing_links = self._existing_links(ann_id)
        return {
            "ann_id": ann_id,
            "title": cached["title"],
            "url": cached["url"],
            "body_preview": cached["body"][:5000],
            "body_chars": len(cached["body"]),
            "fetched_at": cached["fetched_at"].isoformat(),
            "existing_links": existing_links,
        }

    def _apply_match(self, payload: dict) -> dict:
        ann_id = self._validate_ann_id(payload.get("ann_id"))
        try:
            origin_work_id = int(payload.get("origin_work_id"))
            target_work_id = int(payload.get("target_work_id"))
        except (TypeError, ValueError) as error:
            raise ValueError("작품 선택을 확인할 수 없습니다.") from error
        if not payload.get("confirmed"):
            raise ValueError("작품 연결 확인란을 체크해 주세요.")
        cache = self._cached_page(ann_id)
        if cache is None:
            raise ValueError("미리보기를 다시 불러온 뒤 저장해 주세요.")
        if not any(row.get("menu_work_id") == target_work_id for row in self._catalog_search_for_exact_id(target_work_id)):
            raise ValueError("선택한 작품 ID가 로컬 작품 카탈로그에 없습니다.")

        rows, fields = _read_worklist(DEFAULT_WORKLIST)
        _ensure_fields(rows, fields)
        by_id = {int(row["menu_work_id"]): row for row in rows}
        origin_row = by_id.get(origin_work_id)
        target_row = by_id.get(target_work_id)
        if origin_row is None or target_row is None:
            raise FileNotFoundError("작업목록에서 작품을 찾을 수 없습니다.")
        if origin_row.get("lookup_status") not in {
            "pending_manual_search", "indexed", "already_indexed", "manual_mapped_elsewhere",
        }:
            raise ValueError("이 작품은 현재 수동 확인 대상으로 등록되어 있지 않습니다. 목록을 새로고침해 주세요.")

        source = next(item for item in load_source_registry().sources if item.source_id == ANN_SOURCE_ID)
        if source.status.value != "approved":
            raise PermissionError("ANN 출처가 승인 상태가 아니어서 저장할 수 없습니다.")
        connection = sqlite3.connect(DEFAULT_DB, check_same_thread=False, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            prior_links = self._existing_links(ann_id, connection=connection)
            other_work_links = [
                link for link in prior_links
                if (link["menu_work_id"] is not None and link["menu_work_id"] != target_work_id)
                or (link["candidate_menu_work_id"] is not None and link["candidate_menu_work_id"] != target_work_id)
            ]
            if other_work_links and not payload.get("reassign_confirmed"):
                return {
                    "conflict": True,
                    "error": "이 ANN 페이지는 이미 다른 작품에 연결되어 있습니다. 변경하려면 기존 연결 변경을 확인해 주세요.",
                    "existing_links": prior_links,
                }

            already_linked = any(
                link["status"] == "matched" and link["menu_work_id"] == target_work_id
                for link in prior_links
            )
            if not already_linked:
                title = cache["title"]
                url = cache["url"]
                body = cache["body"]
                digest = hashlib.sha256(f"{ANN_SOURCE_ID}\n{url}\n{body}".encode("utf-8")).hexdigest()
                target_titles = self.app.work_titles.get(target_work_id, ())
                metadata = {
                    "work_match_method": "manual_user_confirmed",
                    "manual_match_reviewed_at": datetime.now(timezone.utc).isoformat(),
                    "manual_match_origin_work_id": str(origin_work_id),
                    "manual_match_target_work_id": str(target_work_id),
                    "manual_match_catalog_titles_json": json.dumps(list(target_titles), ensure_ascii=False),
                    "ann_id": ann_id,
                    "ingest_route": "direct_html_manual_review",
                }
                document = EvidenceDocument(
                    document_id=f"ev_{digest[:32]}", content_type=RagContentType.GUIDE,
                    title=title, source_url=url, source_id=ANN_SOURCE_ID,
                    retrieved_at=cache["fetched_at"], evidence_type=EvidenceType.REFERENCE_FACT,
                    collection_method=CollectionMethod.MANUAL_IMPORT, language="en",
                    source_native_id=ann_id, publisher=source.name, body=body,
                    raw_payload={
                        "ingest_route": "direct_html_manual_review",
                        "candidate_id_source": "user_selected_ann_id",
                        "http_status": 200,
                        "html_sha256": cache["html_sha256"],
                        "manual_match_confirmed": True,
                        "origin_work_id": origin_work_id,
                        "target_work_id": target_work_id,
                    },
                    excerpts=(EvidenceExcerpt(text=body[:2000], locator="page:body"),),
                    work_link_status=WorkLinkStatus.MATCHED,
                    menu_work_ids=(target_work_id,), metadata=metadata,
                )
                validate_document_profile(document)
                store = SQLiteRagStore(connection)
                asyncio.run(store.upsert_and_schedule_generation(document, schedule_generation=False))

            if other_work_links and payload.get("reassign_confirmed"):
                connection.execute("BEGIN IMMEDIATE")
                try:
                    ann_documents = connection.execute(
                        "SELECT document_id FROM rag_document WHERE source_id=? AND external_id=? ORDER BY document_id",
                        (ANN_SOURCE_ID, ann_id),
                    ).fetchall()
                    for ann_document in ann_documents:
                        document_id = str(ann_document["document_id"])
                        connection.execute("DELETE FROM rag_work_link WHERE document_id=?", (document_id,))
                        connection.execute(
                            """INSERT INTO rag_work_link(link_id,document_id,menu_work_id,candidate_menu_work_id,status,matched_on)
                               VALUES(?,?,?,NULL,'matched','manual_user_confirmed')""",
                            (f"wl_{document_id}_0", document_id, target_work_id),
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

            now = datetime.now(timezone.utc).isoformat()
            history = {
                "ann_id": ann_id,
                "ann_url": cache["url"],
                "ann_title": cache["title"],
                "origin_work_id": origin_work_id,
                "target_work_id": target_work_id,
                "confirmed_at": now,
            }
            target_ids = _json_list(target_row.get("manual_match_ids_json"))
            if ann_id not in {str(value) for value in target_ids}:
                target_ids.append(ann_id)
            target_history = _json_list(target_row.get("manual_match_history_json"))
            if not any(str(item.get("ann_id")) == ann_id and int(item.get("target_work_id", -1)) == target_work_id for item in target_history if isinstance(item, dict)):
                target_history.append(history)
            target_row["manual_match_ids_json"] = json.dumps(target_ids, ensure_ascii=False)
            target_row["manual_match_history_json"] = json.dumps(target_history, ensure_ascii=False)

            linked = connection.execute(
                """SELECT DISTINCT d.external_id,d.url FROM rag_document d
                   JOIN rag_work_link l ON l.document_id=d.document_id
                   WHERE d.source_id=? AND l.menu_work_id=? AND l.status='matched'
                   ORDER BY d.external_id""",
                (ANN_SOURCE_ID, target_work_id),
            ).fetchall()
            target_row["ann_documents_ingested"] = str(len(linked))
            target_row["existing_ann_urls_json"] = json.dumps(
                [str(item["url"]) for item in linked], ensure_ascii=False,
            )
            # Keep the current review item open while the user may add more ANN IDs.
            # The separate completion action closes it after all IDs are entered.
            target_row["lookup_status"] = "pending_manual_search" if target_work_id == origin_work_id else "indexed"
            target_row["notes"] = self._append_note(
                target_row.get("notes", ""), f"Manual match confirmed: ANN {ann_id} → work {target_work_id}."
            )
            target_row["manual_mapped_to_work_id"] = ""

            old_work_ids = {
                int(link["menu_work_id"])
                for link in prior_links
                if link["menu_work_id"] is not None and int(link["menu_work_id"]) != target_work_id
            }
            old_work_ids.update(
                int(link["candidate_menu_work_id"])
                for link in prior_links
                if link["candidate_menu_work_id"] is not None
                and int(link["candidate_menu_work_id"]) != target_work_id
            )
            for old_work_id in sorted(old_work_ids):
                old_row = by_id.get(old_work_id)
                if old_row is None:
                    continue
                old_linked = connection.execute(
                    """SELECT DISTINCT d.external_id,d.url FROM rag_document d
                       JOIN rag_work_link l ON l.document_id=d.document_id
                       WHERE d.source_id=? AND l.menu_work_id=? AND l.status='matched'
                       ORDER BY d.external_id""",
                    (ANN_SOURCE_ID, old_work_id),
                ).fetchall()
                old_ids = {str(item["external_id"]) for item in old_linked if item["external_id"]}
                old_row["ann_documents_ingested"] = str(len(old_linked))
                old_row["existing_ann_urls_json"] = json.dumps(
                    [str(item["url"]) for item in old_linked], ensure_ascii=False,
                )
                old_row["lookup_status"] = "indexed" if old_ids else "pending_manual_search"
                current_manual_ids = [str(value) for value in _json_list(old_row.get("manual_match_ids_json"))]
                old_row["manual_match_ids_json"] = json.dumps(
                    [value for value in current_manual_ids if value != ann_id], ensure_ascii=False,
                )
                old_row["notes"] = self._append_note(
                    old_row.get("notes", ""), f"ANN {ann_id} was manually reassigned to work {target_work_id}."
                )

            if origin_work_id != target_work_id:
                origin_row["lookup_status"] = "manual_mapped_elsewhere"
                origin_row["manual_mapped_to_work_id"] = str(target_work_id)
                origin_row["notes"] = self._append_note(
                    origin_row.get("notes", ""),
                    f"Manual review assigned this ANN candidate to work {target_work_id}; this catalog entry remains unresolved.",
                )
            else:
                origin_row["manual_mapped_to_work_id"] = ""

            _write_worklist(DEFAULT_WORKLIST, rows, fields)
            return {
                "saved": True,
                "already_linked": already_linked,
                "ann_id": ann_id,
                "ann_title": cache["title"],
                "ann_url": cache["url"],
                "origin_work_id": origin_work_id,
                "target_work_id": target_work_id,
                "target_titles": list(self.app.work_titles.get(target_work_id, ())),
                "linked_document_count": len(linked),
            }
        finally:
            connection.close()

    def _complete_manual_work(self, payload: dict) -> dict:
        try:
            work_id = int(payload.get("menu_work_id"))
        except (TypeError, ValueError) as error:
            raise ValueError("완료할 작품을 확인할 수 없습니다.") from error
        rows, fields = _read_worklist(DEFAULT_WORKLIST)
        for row in rows:
            if int(row["menu_work_id"]) != work_id:
                continue
            manual_ids = [str(value) for value in _json_list(row.get("manual_match_ids_json"))]
            if not manual_ids and int(row.get("ann_documents_ingested") or 0) == 0:
                raise ValueError("ANN ID를 하나 이상 저장한 뒤 완료할 수 있습니다.")
            row["lookup_status"] = "indexed"
            row["manual_mapped_to_work_id"] = ""
            row["notes"] = self._append_note(
                row.get("notes", ""),
                f"Manual ANN matching completed with {len(manual_ids)} manually confirmed ID(s).",
            )
            _write_worklist(DEFAULT_WORKLIST, rows, fields)
            return {"completed": True, "menu_work_id": work_id, "ann_ids": manual_ids}
        raise FileNotFoundError("완료할 작품을 작업 목록에서 찾을 수 없습니다.")

    @staticmethod
    def _append_note(existing: str, note: str) -> str:
        if note in existing:
            return existing
        return f"{existing}; {note}".strip("; ")[-1500:]

    def _catalog_search_for_exact_id(self, work_id: int) -> list[dict]:
        titles = self.app.work_titles.get(work_id)
        return [{"menu_work_id": work_id, "titles": list(titles or ())}] if titles else []

    @staticmethod
    def _validate_ann_id(value: object) -> str:
        ann_id = str(value or "").strip()
        if not re.fullmatch(r"[1-9][0-9]{0,7}", ann_id):
            raise ValueError("ANN ID는 1 이상의 숫자여야 합니다.")
        return ann_id

    def _cached_page(self, ann_id: str) -> dict | None:
        with self.app.cache_lock:
            value = self.app.preview_cache.get(ann_id)
            if value and time.monotonic() - value["cached_monotonic"] <= CACHE_TTL_SECONDS:
                return value
            self.app.preview_cache.pop(ann_id, None)
        return None

    def _existing_links(self, ann_id: str, *, connection: sqlite3.Connection | None = None) -> list[dict]:
        owned = connection is None
        if connection is None:
            if not DEFAULT_DB.is_file():
                return []
            connection = sqlite3.connect(DEFAULT_DB.as_uri() + "?mode=ro", uri=True, timeout=15)
            connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """SELECT DISTINCT d.document_id,l.menu_work_id,l.candidate_menu_work_id,l.status
                   FROM rag_document d LEFT JOIN rag_work_link l ON l.document_id=d.document_id
                   WHERE d.source_id=? AND d.external_id=? ORDER BY l.menu_work_id""",
                (ANN_SOURCE_ID, ann_id),
            ).fetchall()
            result = []
            for row in rows:
                work_id = row["menu_work_id"]
                result.append({
                    "document_id": row["document_id"],
                    "menu_work_id": work_id,
                    "candidate_menu_work_id": row["candidate_menu_work_id"],
                    "status": row["status"] or "unlinked",
                    "titles": list(self.app.work_titles.get(int(work_id), ())) if work_id else [],
                })
            return result
        finally:
            if owned:
                connection.close()

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> None:
    if not DEFAULT_DB.is_file():
        raise FileNotFoundError(f"ani_rag database not found: {DEFAULT_DB}")
    if not DEFAULT_WORKLIST.is_file():
        raise FileNotFoundError(f"ANN worklist not found: {DEFAULT_WORKLIST}")
    server = ManualMatchServer(("127.0.0.1", 8767), ManualMatchHandler)
    print("ANN manual match page: http://127.0.0.1:8767/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
