"""Recover the source order encoded by the two RAG excerpt writers."""

import hashlib
import sqlite3
from collections.abc import Iterable, Mapping
from typing import TypeVar


_Row = TypeVar("_Row", bound=Mapping[str, object] | sqlite3.Row)


class ExcerptOrderError(ValueError):
    """Stored IDs do not describe one contiguous sequence for this document."""


def excerpt_index_digest(document_id: str, index: int) -> str:
    return hashlib.sha256(f"{document_id}:{index}".encode("utf-8")).hexdigest()[:28]


def ordered_excerpt_rows(document_id: str, rows: Iterable[_Row]) -> list[_Row]:
    """Restore insertion ordinals without relying on database row or hash order.

    Deletion of trailing rows preserves contiguous ordinals. Callers verifying
    completeness must also compare the reconstructed content hash to the stored hash.
    """
    by_id: dict[str, _Row] = {}
    for row in rows:
        excerpt_id = row["excerpt_id"]
        if not isinstance(excerpt_id, str) or excerpt_id in by_id:
            raise ExcerptOrderError(f"Invalid or duplicate excerpt ID in {document_id}")
        by_id[excerpt_id] = row
    if not by_id:
        return []

    prefix = next(iter(by_id)).partition("_")[0]
    if prefix not in {"ex", "mde"}:
        raise ExcerptOrderError(f"Unknown excerpt ID scheme in {document_id}")
    try:
        return [by_id[f"{prefix}_{excerpt_index_digest(document_id, index)}"] for index in range(len(by_id))]
    except KeyError as error:
        raise ExcerptOrderError(f"Incomplete or inconsistent excerpt IDs in {document_id}") from error
