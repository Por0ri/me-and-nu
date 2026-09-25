from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import ApiError
from app.services import catalog_service, reaction_service
from app.services.cursor import decode_cursor, encode_cursor


def test_cursor_rejects_reuse_for_another_user_or_topic():
    cursor = encode_cursor({"v": 1, "userId": 1, "topicId": 2, "subtopicId": None})
    with pytest.raises(ApiError) as exc:
        decode_cursor(cursor, expected={"v": 1, "userId": 2, "topicId": 2})
    assert exc.value.status_code == 422
    assert exc.value.code == "INVALID_CURSOR"


@pytest.mark.asyncio
async def test_subtopic_cursor_rejects_empty_and_boolean_position(monkeypatch):
    monkeypatch.setattr(catalog_service.repo, "get_active_topic", AsyncMock(return_value=object()))
    monkeypatch.setattr(catalog_service.repo, "list_subtopics", AsyncMock())
    for cursor in (
        "",
        encode_cursor({"v": 1, "topicId": 2, "q": None, "parent": None, "after": True}),
    ):
        with pytest.raises(ApiError) as exc:
            await catalog_service.subtopics(
                object(), 2, q=None, parent_subtopic_id=None, cursor=cursor, limit=20
            )
        assert exc.value.code == "INVALID_CURSOR"
    catalog_service.repo.list_subtopics.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelling_preference_keeps_like(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock(), delete=AsyncMock())
    reaction = SimpleNamespace(ox_feedback="O", is_liked=True, liked_at=object())
    monkeypatch.setattr(reaction_service, "require_active_tap", AsyncMock(return_value=SimpleNamespace(tap_id=7)))
    get_reaction = AsyncMock(return_value=reaction)
    monkeypatch.setattr(reaction_service.repo, "get_reaction", get_reaction)

    await reaction_service.clear_preference(db, user_id=11, topic_id=2, content_id=301)

    assert reaction.ox_feedback is None
    assert reaction.is_liked is True
    db.delete.assert_not_awaited()
    db.commit.assert_awaited_once()
    get_reaction.assert_awaited_once_with(db, 11, 7, 301)


@pytest.mark.asyncio
async def test_cancelling_like_keeps_preference(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock(), delete=AsyncMock())
    reaction = SimpleNamespace(ox_feedback="X", is_liked=True, liked_at=object())
    monkeypatch.setattr(reaction_service, "require_active_tap", AsyncMock(return_value=SimpleNamespace(tap_id=7)))
    monkeypatch.setattr(reaction_service.repo, "get_reaction", AsyncMock(return_value=reaction))

    await reaction_service.clear_like(db, user_id=11, topic_id=2, content_id=301)

    assert reaction.ox_feedback == "X"
    assert reaction.is_liked is False
    assert reaction.liked_at is None
    db.delete.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_bookmark_can_be_removed_after_content_is_hidden(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())
    saved = SimpleNamespace(status="kept")
    monkeypatch.setattr(reaction_service, "require_active_tap", AsyncMock(return_value=SimpleNamespace(tap_id=7)))
    monkeypatch.setattr(reaction_service.repo, "get_saved_item", AsyncMock(return_value=saved))
    get_visible_content = AsyncMock(side_effect=AssertionError("delete must not require visible content"))
    monkeypatch.setattr(reaction_service.repo, "get_visible_content", get_visible_content)

    await reaction_service.clear_bookmark(db, user_id=11, topic_id=2, content_id=301)

    assert saved.status == "deleted"
    get_visible_content.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reaction_rejects_content_from_other_topic(monkeypatch):
    db = object()
    monkeypatch.setattr(reaction_service, "require_active_tap", AsyncMock(return_value=SimpleNamespace(tap_id=7)))
    monkeypatch.setattr(reaction_service.repo, "get_visible_content", AsyncMock(return_value=SimpleNamespace(topic_id=3)))

    with pytest.raises(ApiError) as exc:
        await reaction_service._target(db, user_id=11, topic_id=2, content_id=301)
    assert exc.value.status_code == 422
    assert exc.value.code == "VALIDATION_ERROR"
