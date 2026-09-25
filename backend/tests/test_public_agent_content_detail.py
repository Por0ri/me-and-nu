from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import feed_service


def _content(*, production_type: str, source_draft_id: int | None):
    return SimpleNamespace(
        content_id=301,
        topic_id=1,
        production_type=production_type,
        source_draft_id=source_draft_id,
        title="Agent가 작성한 영화 글",
        summary="영화 글의 요약",
        body="첫 문단\n<script>alert('plain text')</script>",
        image_url=None,
        published_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        is_sponsored=False,
        content_type="movie_review",
        creator_channel_id=None,
        judgment_status="confirmed",
        ai_judgment_basis=None,
    )


def _mock_detail_dependencies(monkeypatch, content):
    monkeypatch.setattr(feed_service, "require_active_tap", AsyncMock(return_value=SimpleNamespace(tap_id=7)))
    monkeypatch.setattr(feed_service.content_repo, "get_public_content", AsyncMock(return_value=content))
    monkeypatch.setattr(feed_service, "_source_or_none", AsyncMock(return_value=("https://example.com/source", "출처")))
    monkeypatch.setattr(feed_service, "_state", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(feed_service.content_repo, "list_content_subtopic_ids", AsyncMock(return_value=[12]))


@pytest.mark.asyncio
async def test_published_original_ai_content_exposes_full_body(monkeypatch):
    _mock_detail_dependencies(monkeypatch, _content(production_type="ai", source_draft_id=88))

    detail = await feed_service.get_content_detail(object(), user_id=1, topic_id=1, content_id=301)

    response = detail.model_dump(by_alias=True)
    assert response["productionType"] == "ai"
    assert response["displayMode"] == "full_body"
    assert response["body"] == "첫 문단\n<script>alert('plain text')</script>"
    assert response["excerpt"] == "영화 글의 요약"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("production_type", "source_draft_id"),
    [("human", None), ("human", 88), ("hybrid", 88), ("ai", None)],
)
async def test_non_original_ai_content_remains_excerpt_only(monkeypatch, production_type, source_draft_id):
    _mock_detail_dependencies(
        monkeypatch,
        _content(production_type=production_type, source_draft_id=source_draft_id),
    )

    detail = await feed_service.get_content_detail(object(), user_id=1, topic_id=1, content_id=301)

    assert detail.display_mode == "link_excerpt"
    assert detail.body is None
    assert detail.excerpt == "영화 글의 요약"


@pytest.mark.asyncio
async def test_feed_card_labels_agent_generated_content(monkeypatch):
    content = _content(production_type="ai", source_draft_id=88)
    monkeypatch.setattr(feed_service, "require_active_tap", AsyncMock(return_value=SimpleNamespace(tap_id=7)))
    monkeypatch.setattr(feed_service.content_repo, "list_public_contents", AsyncMock(return_value=[content]))
    monkeypatch.setattr(feed_service, "_source_or_none", AsyncMock(return_value=("https://example.com/source", "출처")))
    monkeypatch.setattr(feed_service, "_state", AsyncMock(return_value=(None, None)))

    feed = await feed_service.get_feed(
        object(), user_id=1, topic_id=1, subtopic_id=None, cursor=None, limit=20
    )

    card = feed.model_dump(by_alias=True)["sections"][0]["contents"][0]
    assert card["productionType"] == "ai"
    assert card["id"] == 301


@pytest.mark.asyncio
async def test_preview_notice_appears_in_feed_and_detail(monkeypatch):
    content = _content(production_type="ai", source_draft_id=88)
    content.judgment_status = "needs_review"
    content.ai_judgment_basis = {"preview": True}
    _mock_detail_dependencies(monkeypatch, content)
    monkeypatch.setattr(feed_service.content_repo, "list_public_contents", AsyncMock(return_value=[content]))

    feed = await feed_service.get_feed(
        object(), user_id=1, topic_id=1, subtopic_id=None, cursor=None, limit=20
    )
    detail = await feed_service.get_content_detail(
        object(), user_id=1, topic_id=1, content_id=301
    )

    expected = [
        {"code": "REVIEW_REQUIRED", "message": "검토 필요"},
        {"code": "DEV_PREVIEW", "message": "개발용 미리보기"},
    ]
    assert feed.model_dump(by_alias=True)["sections"][0]["contents"][0]["notices"] == expected
    assert detail.model_dump(by_alias=True)["notices"] == expected
