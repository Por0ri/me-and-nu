from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import require_onboarding
from app.db.session import get_db
from app.main import create_app
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


@pytest.fixture(autouse=True)
def _stub_subtopic_names(monkeypatch):
    monkeypatch.setattr(feed_service.content_repo, "list_content_subtopic_names", AsyncMock(return_value=["액션"]))
    monkeypatch.setattr(feed_service.content_repo, "list_content_sources", AsyncMock(return_value=[]))


def _mock_detail_dependencies(monkeypatch, content):
    monkeypatch.setattr(feed_service, "require_active_tap", AsyncMock(return_value=SimpleNamespace(tap_id=7)))
    monkeypatch.setattr(feed_service.content_repo, "get_public_content", AsyncMock(return_value=content))
    monkeypatch.setattr(feed_service, "_source_or_none", AsyncMock(return_value=("https://example.com/source", "출처")))
    monkeypatch.setattr(feed_service, "_state", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(feed_service.content_repo, "list_content_subtopic_ids", AsyncMock(return_value=[12]))


@pytest.mark.parametrize("creator_channel_id", [None, 91])
def test_published_original_ai_detail_exposes_full_body_without_channel(monkeypatch, creator_channel_id):
    content = _content(production_type="ai", source_draft_id=88)
    content.creator_channel_id = creator_channel_id
    _mock_detail_dependencies(monkeypatch, content)
    monkeypatch.setattr(
        feed_service,
        "_state",
        AsyncMock(return_value=(SimpleNamespace(is_liked=True, ox_feedback="O"), SimpleNamespace(saved_item_id=5))),
    )
    app = create_app(enable_dev_api=False)

    async def override_db():
        yield object()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_onboarding] = lambda: SimpleNamespace(user_id=1)
    with TestClient(app) as client:
        result = client.get("/api/v1/contents/301?topicId=1")

    assert result.status_code == 200
    response = result.json()
    assert "channel" not in response
    assert response["contentId"] == 301
    assert response["topicId"] == 1
    assert response["title"] == content.title
    assert response["productionType"] == "ai"
    assert response["displayMode"] == "full_body"
    assert response["body"] == "첫 문단\n<script>alert('plain text')</script>"
    assert response["excerpt"] == "영화 글의 요약"
    assert response["sourceUrl"] == "https://example.com/source"
    assert response["publisher"] == "출처"
    assert response["share"]["url"] == "https://example.com/source"
    assert response["myReaction"] == {"liked": True, "preference": "positive", "bookmarked": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("image_url", [None, "/local-content/the-host-still.jpg"])
async def test_detail_exposes_same_image_url_as_feed(monkeypatch, image_url):
    content = _content(production_type="ai", source_draft_id=88)
    content.image_url = image_url
    _mock_detail_dependencies(monkeypatch, content)
    monkeypatch.setattr(feed_service.content_repo, "list_public_contents", AsyncMock(return_value=[content]))

    feed = await feed_service.get_feed(
        object(), user_id=1, topic_id=1, subtopic_id=None, cursor=None, limit=20
    )
    detail = await feed_service.get_content_detail(
        object(), user_id=1, topic_id=1, content_id=301
    )

    card = feed.model_dump(by_alias=True)["sections"][0]["contents"][0]
    assert card["imageUrl"] == image_url
    assert detail.model_dump(by_alias=True)["imageUrl"] == image_url


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
