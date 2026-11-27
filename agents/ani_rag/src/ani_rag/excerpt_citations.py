"""Resolve both generations of excerpt coordinates against the preserved passage."""

from collections.abc import Sequence
from functools import lru_cache
import hashlib

from ani_rag.models import EvidenceExcerpt
from ani_rag.storage.excerpt_order import excerpt_index_digest


@lru_cache(maxsize=256)
def legacy_excerpt_order(document_id: str, count: int) -> tuple[int, ...]:
    """Map a former sidecar position (sorted excerpt ID) to the original position."""
    return tuple(sorted(range(count), key=lambda index: excerpt_index_digest(document_id, index)))


def cited_excerpt_index(
    document_id: str, excerpts: Sequence[EvidenceExcerpt], index: int | None,
    locator: str, start: int, end: int, text: str,
) -> int | None:
    """Return the original ordinal only if exactly one possible passage agrees."""
    if index is None or not 0 <= index < len(excerpts) or not 0 <= start < end or end - start != len(text):
        return None
    matches = []
    for original in {index, legacy_excerpt_order(document_id, len(excerpts))[index]}:
        excerpt = excerpts[original]
        base = excerpt.locator or f"excerpt:{index}"
        if locator == f"{base}:{start}-{end}" and excerpt.text[start:end] == text:
            matches.append(original)
    return matches[0] if len(matches) == 1 else None


def chunk_digest(document_id: str, kind: str, index: int | None, locator: str, start: int, text: str) -> str:
    identity = f"{document_id}\n{kind}\n{index}\n{locator}\n{start}\n{text}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
