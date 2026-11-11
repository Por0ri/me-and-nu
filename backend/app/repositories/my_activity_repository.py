"""'내 활동' 목록 읽기: 본인의 좋아요 · 본 기록과 그 글 중 지금 공개 중인 것만."""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Content, ContentReaction, ContentSource, Tap, Topic, UserEvent
from app.repositories.carousel_repository import public_content_condition


def _has_source():
    return select(ContentSource.content_source_id).where(ContentSource.content_id == Content.content_id).exists()


def _active_tap(user_id: int, topic_id: int | None) -> list:
    conditions = [Tap.user_id == user_id, Tap.deleted_at.is_(None), Topic.is_active.is_(True)]
    if topic_id is not None:
        conditions.append(Tap.topic_id == topic_id)
    return conditions


async def list_liked_contents(
    db: AsyncSession,
    user_id: int,
    *,
    topic_id: int | None,
    after: tuple[datetime, int] | None,
    limit: int,
) -> list[tuple[datetime, Content]]:
    """좋아요 누른 최신순. 취소한 글과 지금 공개 중이 아닌 글은 뺍니다."""
    liked_at = func.coalesce(ContentReaction.liked_at, ContentReaction.updated_at)
    statement = (
        select(liked_at.label("liked_at"), Content)
        .select_from(ContentReaction)
        .join(Content, Content.content_id == ContentReaction.content_id)
        .join(Tap, Tap.tap_id == ContentReaction.tap_id)
        .join(Topic, Topic.topic_id == Tap.topic_id)
        .where(
            ContentReaction.user_id == user_id,
            ContentReaction.is_liked.is_(True),
            Content.topic_id == Tap.topic_id,
            public_content_condition(),
            _has_source(),
            *_active_tap(user_id, topic_id),
        )
    )
    if after is not None:
        statement = statement.where(tuple_(liked_at, Content.content_id) < after)
    statement = statement.order_by(liked_at.desc(), Content.content_id.desc()).limit(limit)
    return [(row.liked_at, row.Content) for row in (await db.execute(statement)).all()]


async def list_view_events(
    db: AsyncSession, user_id: int, *, topic_id: int | None, since: datetime
) -> list[tuple[int, str, dict, datetime]]:
    """`since` 뒤의 상세 열람 · 카드 넘김 기록 (자동 시험 기록은 뺌). 끝까지 넘겼는지는 서비스가 봅니다."""
    statement = (
        select(UserEvent.content_id, UserEvent.event_type, UserEvent.event_value, UserEvent.occurred_at)
        .join(Tap, Tap.tap_id == UserEvent.tap_id)
        .join(Topic, Topic.topic_id == Tap.topic_id)
        .where(
            UserEvent.user_id == user_id,
            UserEvent.event_type.in_(("open", "card_swipe")),
            UserEvent.data_source != "automated_test",
            UserEvent.occurred_at >= since,
            *_active_tap(user_id, topic_id),
        )
    )
    return [tuple(row) for row in (await db.execute(statement)).all()]


async def get_visible_contents(db: AsyncSession, content_ids: Iterable[int]) -> dict[int, Content]:
    ids = list(content_ids)
    if not ids:
        return {}
    statement = select(Content).where(Content.content_id.in_(ids), public_content_condition(), _has_source())
    return {content.content_id: content for content in (await db.scalars(statement)).all()}
