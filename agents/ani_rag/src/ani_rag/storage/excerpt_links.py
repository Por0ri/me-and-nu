"""Find the persisted excerpt containing exactly the text and position cited by a chunk."""

import hashlib
import sqlite3


def stored_excerpt_id(
    db: sqlite3.Connection, document_id: str, index: int, locator: str, start: int, end: int, text: str,
) -> str | None:
    suffix = f":{start}-{end}"
    if index < 0 or start < 0 or end <= start or end - start != len(text) or not locator.endswith(suffix):
        return None
    source_locator = locator[:-len(suffix)]
    rows = db.execute(
        """SELECT excerpt_id FROM rag_excerpt WHERE document_id=?
           AND COALESCE(NULLIF(locator,''),?)=?
           AND substr(excerpt_text,?,?)=?""",
        (document_id, f"excerpt:{index}", source_locator, start + 1, end - start, text),
    )
    ids = {row[0] for row in rows}
    if len(ids) == 1:
        return ids.pop()
    # Identical passages may appear at more than one index within the same source.
    digest = hashlib.sha256(f"{document_id}:{index}".encode()).hexdigest()[:28]
    indexed = ids & {f"ex_{digest}", f"mde_{digest}"}
    return indexed.pop() if len(indexed) == 1 else None
