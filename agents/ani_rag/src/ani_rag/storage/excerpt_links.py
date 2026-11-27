"""Find the persisted excerpt containing exactly the text and position cited by a chunk."""

import sqlite3

from ani_rag.excerpt_citations import cited_excerpt_index
from ani_rag.models import EvidenceExcerpt
from ani_rag.storage.excerpt_order import ordered_excerpt_rows


def stored_excerpt_id(
    db: sqlite3.Connection, document_id: str, index: int, locator: str, start: int, end: int, text: str,
) -> str | None:
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        rows = ordered_excerpt_rows(document_id, cursor.execute(
            "SELECT excerpt_id,locator,excerpt_text FROM rag_excerpt WHERE document_id=?", (document_id,),
        ))
    finally:
        cursor.close()
    excerpts = [EvidenceExcerpt(row["excerpt_text"], row["locator"]) for row in rows]
    original = cited_excerpt_index(document_id, excerpts, index, locator, start, end, text)
    return rows[original]["excerpt_id"] if original is not None else None
