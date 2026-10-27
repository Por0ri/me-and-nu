"""LangGraph 노드 사이에서 전달하는 상태."""

from typing import Any, TypedDict


class ContentState(TypedDict, total=False):
    repository: Any
    request_id: str
    topic: str
    content_kind: str
    requested_content_type: str
    package: dict[str, Any]
    draft_cards: list[dict[str, Any]]
    offline_cards: list[dict[str, Any]]
    usage_records: list[dict[str, Any]]
    qa_report: list[dict[str, Any]]
    quality_report: dict[str, Any]
    review_fixture: dict[str, Any]
    language_review: dict[str, Any]
    review_mode: str
    quality_score: int
    needs_revision: bool
    revision_count: int
    review_failures: int
    revision_owner: str
    revision_owners: list[str]
    max_revision: int
    allow_llm: bool
    download_images: bool
    render_required: bool
    render_elapsed_seconds: float
    rendered_card_count: int
    visual_layout_plan_attempted: bool
    rendered_image_keys: dict[str, str | None]
    editorial_decision: str
    rejection_reason: str
    status: str
    trace: list[str]
