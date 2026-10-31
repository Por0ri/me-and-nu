"""Reads for the recommended home feed: the day's ranked list and visit signals."""

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Content, ContentEntityLink, ContentReaction, ContentSource, RecommendationItem, RecommendationRound,
    UserAccount, UserEvent,
)
from app.repositories.carousel_repository import public_content_condition


def _visible(topic_id: int):
    """Same visibility as the newest-first feed: public, in the Topic, with a source."""
    return (
        Content.topic_id == topic_id,
        public_content_condition(),
        select(ContentSource.content_source_id)
        .where(ContentSource.content_id == Content.content_id)
        .exists(),
    )


async def latest_round(
    db: AsyncSession, tap_id: int, service_dates: list[date]
) -> RecommendationRound | None:
    """Newest round among the given service dates; the first date wins."""
    return await db.scalar(
        select(RecommendationRound)
        .where(RecommendationRound.tap_id == tap_id, RecommendationRound.service_date.in_(service_dates))
        .order_by(RecommendationRound.service_date.desc(), RecommendationRound.recommendation_round_id.desc())
        .limit(1)
    )


async def onboarding_completed_at(db: AsyncSession, user_id: int) -> datetime | None:
    return await db.scalar(select(UserAccount.onboarding_completed_at).where(UserAccount.user_id == user_id))


async def get_round(db: AsyncSession, round_id: int, tap_id: int) -> RecommendationRound | None:
    return await db.scalar(
        select(RecommendationRound).where(
            RecommendationRound.recommendation_round_id == round_id,
            RecommendationRound.tap_id == tap_id,
        )
    )


async def ranked_items(db: AsyncSession, round_id: int, topic_id: int) -> list[tuple[int, int, dict | None]]:
    """(content_id, recommendation_item_id, recommend_reason) of currently visible contents, by rank."""
    rows = await db.execute(
        select(
            RecommendationItem.content_id,
            RecommendationItem.recommendation_item_id,
            RecommendationItem.recommend_reason,
        )
        .join(Content, Content.content_id == RecommendationItem.content_id)
        .where(RecommendationItem.recommendation_round_id == round_id, *_visible(topic_id))
        .order_by(RecommendationItem.rank)
        .limit(100)
    )
    return [(content_id, item_id, reason) for content_id, item_id, reason in rows.all()]


async def latest_content_ids(db: AsyncSession, topic_id: int, as_of: datetime, limit: int) -> list[int]:
    """Visible contents published by the snapshot time, newest first."""
    sort_time = func.coalesce(Content.published_at, Content.created_at)
    rows = await db.execute(
        select(Content.content_id)
        .where(*_visible(topic_id), sort_time <= as_of)
        .order_by(sort_time.desc(), Content.content_id.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def opened_content_ids(db: AsyncSession, tap_id: int, as_of: datetime) -> set[int]:
    rows = await db.execute(
        select(UserEvent.content_id).distinct().where(
            UserEvent.tap_id == tap_id, UserEvent.event_type == "open", UserEvent.received_at <= as_of,
        )
    )
    return set(rows.scalars().all())


async def shown_content_ids(db: AsyncSession, tap_id: int, since: datetime, as_of: datetime) -> set[int]:
    rows = await db.execute(
        select(UserEvent.content_id).distinct().where(
            UserEvent.tap_id == tap_id, UserEvent.event_type == "impression",
            UserEvent.received_at >= since, UserEvent.received_at <= as_of,
        )
    )
    return set(rows.scalars().all())


async def negative_content_ids(db: AsyncSession, user_id: int, tap_id: int) -> set[int]:
    """Current dislikes; updated_at also changes on likes and is not history."""
    rows = await db.execute(
        select(ContentReaction.content_id).where(
            ContentReaction.user_id == user_id, ContentReaction.tap_id == tap_id,
            ContentReaction.ox_feedback == "X",
        )
    )
    return set(rows.scalars().all())


async def main_works(db: AsyncSession, content_ids: list[int]) -> dict[int, int]:
    """Main work (sort_order 1) of each content that has one."""
    if not content_ids:
        return {}
    rows = await db.execute(
        select(ContentEntityLink.content_id, ContentEntityLink.entity_id).where(
            ContentEntityLink.content_id.in_(content_ids), ContentEntityLink.sort_order == 1,
        )
    )
    return {content_id: entity_id for content_id, entity_id in rows.all()}


async def contents_by_id(db: AsyncSession, content_ids: list[int], topic_id: int) -> dict[int, Content]:
    if not content_ids:
        return {}
    rows = await db.scalars(select(Content).where(Content.content_id.in_(content_ids), *_visible(topic_id)))
    return {content.content_id: content for content in rows.all()}
