"""Queries for API-033 activity events and recommendation exposure."""

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Content, RecommendationItem, RecommendationRound, UserEvent
from app.repositories.carousel_repository import public_content_condition


async def visible_content_topics(db: AsyncSession, content_ids: Collection[int]) -> dict[int, int]:
    """Map each currently public content to its Topic."""
    if not content_ids:
        return {}
    rows = await db.execute(
        select(Content.content_id, Content.topic_id).where(
            Content.content_id.in_(content_ids), public_content_condition()
        )
    )
    return {content_id: topic_id for content_id, topic_id in rows.all()}


async def recommendation_item_owners(
    db: AsyncSession, item_ids: Collection[int]
) -> dict[int, tuple[int, int]]:
    """Map each recommendation item to the (tap_id, content_id) it was made for."""
    if not item_ids:
        return {}
    rows = await db.execute(
        select(
            RecommendationItem.recommendation_item_id,
            RecommendationRound.tap_id,
            RecommendationItem.content_id,
        )
        .join(
            RecommendationRound,
            RecommendationRound.recommendation_round_id == RecommendationItem.recommendation_round_id,
        )
        .where(RecommendationItem.recommendation_item_id.in_(item_ids))
    )
    return {item_id: (tap_id, content_id) for item_id, tap_id, content_id in rows.all()}


async def insert_events(db: AsyncSession, rows: list[dict]) -> int:
    """Insert in batch order; a client_event_id that is already stored is skipped."""
    if not rows:
        return 0
    statement = (
        insert(UserEvent)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[UserEvent.client_event_id])
        .returning(UserEvent.user_event_id)
    )
    return len((await db.execute(statement)).scalars().all())


async def mark_impressed(db: AsyncSession, first_seen: dict[int, datetime]) -> None:
    """Fill impressed_at once; a later impression never moves it."""
    for item_id, seen_at in first_seen.items():
        await db.execute(
            update(RecommendationItem)
            .where(
                RecommendationItem.recommendation_item_id == item_id,
                RecommendationItem.impressed_at.is_(None),
            )
            .values(impressed_at=seen_at)
        )
