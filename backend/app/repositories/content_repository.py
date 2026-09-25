from datetime import datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Content,
    ContentReaction,
    ContentSource,
    ContentTag,
    CreatorChannel,
    SavedItem,
    SourceSite,
    Subtopic,
)


async def get_public_content(db: AsyncSession, content_id: int, topic_id: int) -> Content | None:
    return await db.scalar(
        select(Content).where(
            Content.content_id == content_id,
            Content.topic_id == topic_id,
            Content.status == "active",
            Content.deleted_at.is_(None),
        )
    )


async def get_visible_content(db: AsyncSession, content_id: int) -> Content | None:
    return await db.scalar(
        select(Content).where(
            Content.content_id == content_id,
            Content.status == "active",
            Content.deleted_at.is_(None),
        )
    )


async def list_public_contents(
    db: AsyncSession,
    topic_id: int,
    *,
    subtopic_id: int | None,
    after: tuple[datetime, int] | None,
    limit: int,
) -> list[Content]:
    sort_time = func.coalesce(Content.published_at, Content.created_at)
    statement = select(Content).where(
        Content.topic_id == topic_id,
        Content.status == "active",
        Content.deleted_at.is_(None),
        select(ContentSource.content_source_id)
        .where(ContentSource.content_id == Content.content_id)
        .exists(),
    )
    if subtopic_id is not None:
        statement = statement.where(
            select(ContentTag.content_tag_id)
            .where(
                ContentTag.content_id == Content.content_id,
                ContentTag.subtopic_id == subtopic_id,
            )
            .exists()
        )
    if after is not None:
        statement = statement.where(
            tuple_(sort_time, Content.content_id) < (after[0], after[1])
        )
    statement = statement.order_by(sort_time.desc(), Content.content_id.desc()).limit(limit)
    return list((await db.scalars(statement)).all())


async def get_primary_source(
    db: AsyncSession, content_id: int
) -> tuple[ContentSource, SourceSite | None] | None:
    statement = (
        select(ContentSource, SourceSite)
        .outerjoin(SourceSite, ContentSource.source_site_id == SourceSite.source_site_id)
        .where(ContentSource.content_id == content_id)
        .order_by(ContentSource.citation_order.asc().nullslast(), ContentSource.content_source_id)
        .limit(1)
    )
    return (await db.execute(statement)).first()


async def list_content_subtopic_ids(db: AsyncSession, content_id: int) -> list[int]:
    statement = select(ContentTag.subtopic_id).where(ContentTag.content_id == content_id)
    return list((await db.scalars(statement.order_by(ContentTag.subtopic_id))).all())


async def list_content_subtopic_names(db: AsyncSession, content_id: int) -> list[str]:
    statement = (
        select(Subtopic.subtopic_name)
        .join(ContentTag, ContentTag.subtopic_id == Subtopic.subtopic_id)
        .where(ContentTag.content_id == content_id)
        .order_by(Subtopic.subtopic_id)
    )
    return list((await db.scalars(statement)).all())


async def get_channel(db: AsyncSession, channel_id: int) -> CreatorChannel | None:
    return await db.get(CreatorChannel, channel_id)


async def get_reaction(
    db: AsyncSession, user_id: int, tap_id: int, content_id: int
) -> ContentReaction | None:
    statement = select(ContentReaction).where(
        ContentReaction.user_id == user_id,
        ContentReaction.tap_id == tap_id,
        ContentReaction.content_id == content_id,
    )
    return await db.scalar(statement)


async def get_saved_item(
    db: AsyncSession, user_id: int, tap_id: int, content_id: int
) -> SavedItem | None:
    statement = select(SavedItem).where(
        SavedItem.user_id == user_id,
        SavedItem.tap_id == tap_id,
        SavedItem.content_id == content_id,
    )
    return await db.scalar(statement.order_by(SavedItem.saved_item_id.desc()))


def add_reaction(db: AsyncSession, user_id: int, tap_id: int, content_id: int) -> ContentReaction:
    reaction = ContentReaction(
        user_id=user_id, tap_id=tap_id, content_id=content_id, is_liked=False
    )
    db.add(reaction)
    return reaction


def add_saved_item(
    db: AsyncSession, user_id: int, tap_id: int, content_id: int, tags: list[str]
) -> SavedItem:
    saved = SavedItem(
        user_id=user_id,
        tap_id=tap_id,
        content_id=content_id,
        status="kept",
        saved_tags=tags,
    )
    db.add(saved)
    return saved
