"""RAG duplicate import and authenticated feed visibility in guarded local PG."""
from test_carousel_postgres import carousel_pg  # noqa: F401
from test_rag_staging import rag_reviewed_bundle  # noqa: F401
from test_independent_review_import_postgres import (
    test_independent_review_import_and_operator_approval_in_authenticated_feed as _scenario,
)


def test_rag_duplicate_pending_hidden_then_operator_approved_once(carousel_pg, rag_reviewed_bundle, monkeypatch):
    _scenario(carousel_pg, rag_reviewed_bundle, monkeypatch)
