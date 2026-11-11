"""Loopback-only, read-only preview of the ani_rag SQLite pilot databases."""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
DATABASES = {
    "pilot": ROOT / "data" / "ani_rag_pilot.sqlite3",
    "live": ROOT / "data" / "ani_rag_live_20261109.sqlite3",
}
INDEX = ROOT / "preview" / "index.html"


def connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def decode_json(value: str | None, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None


def read_database(db_id: str, path: Path) -> dict:
    result = {"id": db_id, "drafts": [], "reviews": [], "registered_sources": [], "queues": []}
    if not path.is_file():
        return result
    with connect(path) as connection:
        if not table_exists(connection, "rag_content_draft"):
            return result
        draft_rows = connection.execute(
            """SELECT draft_id,content_type,topic_id,topic_label,title,body,summary,keyword_json,
                      source_footer,ai_disclosure,menu_work_ids_json,status,created_at,updated_at
               FROM rag_content_draft ORDER BY created_at DESC LIMIT 200""",
        ).fetchall()
        for row in draft_rows:
            draft = dict(row)
            draft["menu_work_ids"] = decode_json(draft.pop("menu_work_ids_json"), [])
            draft["keywords"] = decode_json(draft.pop("keyword_json"), [])
            draft["sources"] = []
            draft["cards"] = []
            draft["reviews"] = []
            if table_exists(connection, "rag_content_source"):
                draft["sources"] = [dict(item) for item in connection.execute(
                    """SELECT source_url,publisher,published_at,is_primary
                       FROM rag_content_source WHERE draft_id=? ORDER BY is_primary DESC,publisher""",
                    (draft["draft_id"],),
                )]
            if table_exists(connection, "rag_content_card"):
                cards = connection.execute(
                    """SELECT card_id,page_no,role,title,body,source_image_url,source_page_url,
                              image_rights_status,image_credit
                       FROM rag_content_card WHERE draft_id=? ORDER BY page_no""",
                    (draft["draft_id"],),
                ).fetchall()
                for card in cards:
                    item = dict(card)
                    item["rendered"] = None
                    if table_exists(connection, "rag_rendered_card"):
                        rendered = connection.execute(
                            """SELECT width,height,frontend_copy_json,frontend_layout_json
                               FROM rag_rendered_card WHERE draft_id=? AND card_id=?""",
                            (draft["draft_id"], item["card_id"]),
                        ).fetchone()
                        if rendered:
                            item["rendered"] = dict(rendered)
                            item["rendered"]["frontend_copy"] = decode_json(rendered["frontend_copy_json"], {})
                            item["rendered"]["frontend_layout"] = decode_json(rendered["frontend_layout_json"], {})
                            item["rendered"].pop("frontend_copy_json", None)
                            item["rendered"].pop("frontend_layout_json", None)
                    draft["cards"].append(item)
            if table_exists(connection, "rag_review_history"):
                reviews = connection.execute(
                    """SELECT review_id,decision,revision_count,result_json,reviewed_at
                       FROM rag_review_history WHERE draft_id=? ORDER BY reviewed_at DESC""",
                    (draft["draft_id"],),
                ).fetchall()
                for review in reviews:
                    item = dict(review)
                    item["result"] = decode_json(item.pop("result_json"), {})
                    draft["reviews"].append(item)
            result["drafts"].append(draft)

        if table_exists(connection, "rag_review_history"):
            result["reviews"] = [dict(row) | {
                "result": decode_json(row["result_json"], {}),
            } for row in connection.execute(
                """SELECT rh.review_id,rh.draft_id,rh.decision,rh.revision_count,rh.result_json,
                          rh.reviewed_at,cd.title,cd.content_type,cd.status
                   FROM rag_review_history rh JOIN rag_content_draft cd ON cd.draft_id=rh.draft_id
                   ORDER BY rh.reviewed_at DESC LIMIT 500""",
            )]
            for row in result["reviews"]:
                row.pop("result_json", None)

        if table_exists(connection, "rag_source"):
            result["registered_sources"] = [dict(row) for row in connection.execute(
                """SELECT source_id,topic_id,content_types_json,media_name,source_kind,site_url,
                          source_grade,is_collecting,last_collected_at,last_error
                   FROM rag_source ORDER BY source_id""",
            )]
            for source in result["registered_sources"]:
                source["content_types"] = decode_json(source.pop("content_types_json"), [])

        if table_exists(connection, "rag_queue_job"):
            result["queues"] = [dict(row) for row in connection.execute(
                """SELECT queue_name,status,COUNT(*) AS count FROM rag_queue_job
                   GROUP BY queue_name,status ORDER BY queue_name,status""",
            )]
    return result


class PreviewHandler(BaseHTTPRequestHandler):
    server_version = "ani_rag_preview/1.0"

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api":
            payload = {
                "databases": [read_database(db_id, path) for db_id, path in DATABASES.items()],
                "sources": self._source_allowlist(),
            }
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) == 3 and parts[0] == "card" and parts[1] in DATABASES:
            db_id, card_id = parts[1], parts[2]
            path = DATABASES[db_id]
            if path.is_file():
                with connect(path) as connection:
                    if table_exists(connection, "rag_rendered_card"):
                        row = connection.execute(
                            "SELECT image_png FROM rag_rendered_card WHERE card_id=?", (card_id,),
                        ).fetchone()
                        if row:
                            self._send(200, row["image_png"], "image/png")
                            return
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _source_allowlist(self) -> list[dict]:
        path = ROOT / "config" / "sources.evergreen.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [{
            "source_id": item.get("source_id"), "name": item.get("name"),
            "content_types": item.get("content_types", []), "status": item.get("status"),
            "is_collecting": item.get("is_collecting", False), "site_url": item.get("site_url"),
            "allowed_hosts": item.get("allowed_hosts", []),
            "usage_notes": item.get("usage_notes", ""),
        } for item in payload.get("sources", []) if isinstance(item, dict)]

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


if __name__ == "__main__":
    # Keep the RAG preview isolated from the existing ani_me;nu preview server.
    address = ("127.0.0.1", 8766)
    print(f"ani_rag preview listening at http://{address[0]}:{address[1]}/", flush=True)
    ThreadingHTTPServer(address, PreviewHandler).serve_forever()
