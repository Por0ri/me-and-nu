from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.core.config import settings
from app.models import Tap, UserEvent
from app.repositories import content_repository as repo
from app.schemas.reaction import BookmarkResponse, LikeResponse, PreferenceResponse
from app.services.feed_service import require_active_tap


async def _lock_reactions(db: AsyncSession, user_id: int, tap_id: int) -> None:
    # Lock the parent even when no reaction row exists yet. Concurrent retries
    # then observe the committed state and cannot emit the same transition twice.
    await db.scalar(select(Tap.tap_id).where(Tap.tap_id == tap_id, Tap.user_id == user_id).with_for_update())


def _record_transition(db: AsyncSession, user_id: int, tap_id: int, content_id: int, event_type: str) -> None:
    now = datetime.now(timezone.utc)
    db.add(UserEvent(
        client_event_id=uuid4(), user_id=user_id, tap_id=tap_id, content_id=content_id,
        event_type=event_type, event_value={"origin": "server_reaction", "schemaVersion": 1},
        occurred_at=now, received_at=now, data_source=settings.event_data_source,
    ))


async def _target(db: AsyncSession, user_id: int, topic_id: int, content_id: int):
    tap = await require_active_tap(db, user_id, topic_id)
    content = await repo.get_visible_content(db, content_id)
    if content is None:
        raise ApiError(404, "CONTENT_NOT_FOUND", "콘텐츠를 찾을 수 없습니다.")
    if content.topic_id != topic_id:
        raise ApiError(422, "VALIDATION_ERROR", "콘텐츠가 요청 Topic에 속하지 않습니다.")
    return tap, content


async def set_preference(
    db: AsyncSession, user_id: int, topic_id: int, content_id: int, value: str
) -> PreferenceResponse:
    tap, _ = await _target(db, user_id, topic_id, content_id)
    tap_id = tap.tap_id
    code = "O" if value == "positive" else "X"
    for attempt in range(2):
        await _lock_reactions(db, user_id, tap_id)
        reaction = await repo.get_reaction(db, user_id, tap_id, content_id)
        if reaction is None:
            reaction = repo.add_reaction(db, user_id, tap_id, content_id)
        if reaction.ox_feedback != code:
            _record_transition(db, user_id, tap_id, content_id,
                               "preference_positive" if code == "O" else "preference_negative")
            reaction.ox_feedback = code
        try:
            await db.commit()
            break
        except IntegrityError:
            await db.rollback()
            if attempt:
                raise ApiError(409, "STATE_CONFLICT", "반응 저장 충돌이 발생했습니다.")
    return PreferenceResponse(content_id=content_id, topic_id=topic_id, value=value)


async def clear_preference(db: AsyncSession, user_id: int, topic_id: int, content_id: int) -> None:
    tap = await require_active_tap(db, user_id, topic_id)
    await _lock_reactions(db, user_id, tap.tap_id)
    reaction = await repo.get_reaction(db, user_id, tap.tap_id, content_id)
    if reaction is not None and reaction.ox_feedback is not None:
        _record_transition(db, user_id, tap.tap_id, content_id, "preference_cancel")
        reaction.ox_feedback = None
        if not reaction.is_liked:
            await db.delete(reaction)
        await db.commit()


async def set_like(db: AsyncSession, user_id: int, topic_id: int, content_id: int) -> LikeResponse:
    tap, _ = await _target(db, user_id, topic_id, content_id)
    tap_id = tap.tap_id
    for attempt in range(2):
        await _lock_reactions(db, user_id, tap_id)
        reaction = await repo.get_reaction(db, user_id, tap_id, content_id)
        if reaction is None:
            reaction = repo.add_reaction(db, user_id, tap_id, content_id)
        if not reaction.is_liked:
            _record_transition(db, user_id, tap_id, content_id, "like")
            reaction.is_liked = True
        if reaction.liked_at is None:
            reaction.liked_at = datetime.now(timezone.utc)
        try:
            await db.commit()
            break
        except IntegrityError:
            await db.rollback()
            if attempt:
                raise ApiError(409, "STATE_CONFLICT", "좋아요 저장 충돌이 발생했습니다.")
    return LikeResponse(content_id=content_id)


async def clear_like(db: AsyncSession, user_id: int, topic_id: int, content_id: int) -> None:
    tap = await require_active_tap(db, user_id, topic_id)
    await _lock_reactions(db, user_id, tap.tap_id)
    reaction = await repo.get_reaction(db, user_id, tap.tap_id, content_id)
    if reaction is not None and reaction.is_liked:
        _record_transition(db, user_id, tap.tap_id, content_id, "like_cancel")
        reaction.is_liked = False
        reaction.liked_at = None
        if reaction.ox_feedback is None:
            await db.delete(reaction)
        await db.commit()


async def set_bookmark(
    db: AsyncSession, user_id: int, topic_id: int, content_id: int
) -> BookmarkResponse:
    tap, _ = await _target(db, user_id, topic_id, content_id)
    tap_id = tap.tap_id
    tags = await repo.list_content_subtopic_names(db, content_id)
    for attempt in range(2):
        await _lock_reactions(db, user_id, tap_id)
        saved = await repo.get_saved_item(db, user_id, tap_id, content_id)
        if saved is None:
            saved = repo.add_saved_item(db, user_id, tap_id, content_id, tags)
            _record_transition(db, user_id, tap_id, content_id, "bookmark")
        elif saved.status == "deleted":
            _record_transition(db, user_id, tap_id, content_id, "bookmark")
            saved.status = "kept"
            saved.saved_at = datetime.now(timezone.utc)
            saved.saved_tags = tags
        try:
            await db.commit()
            await db.refresh(saved)
            break
        except IntegrityError:
            await db.rollback()
            if attempt:
                raise ApiError(409, "STATE_CONFLICT", "북마크 저장 충돌이 발생했습니다.")
    return BookmarkResponse(
        saved_item_id=saved.saved_item_id,
        content_id=content_id,
        topic_id=topic_id,
        tags=saved.saved_tags or [],
        resurface_enabled=saved.status == "kept",
        saved_at=saved.saved_at,
    )


async def clear_bookmark(db: AsyncSession, user_id: int, topic_id: int, content_id: int) -> None:
    tap = await require_active_tap(db, user_id, topic_id)
    await _lock_reactions(db, user_id, tap.tap_id)
    saved = await repo.get_saved_item(db, user_id, tap.tap_id, content_id)
    if saved is not None and saved.status != "deleted":
        _record_transition(db, user_id, tap.tap_id, content_id, "bookmark_cancel")
        saved.status = "deleted"
        await db.commit()
