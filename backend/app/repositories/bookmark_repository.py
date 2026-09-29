"""Owner-scoped public saved-content reads."""

from datetime import datetime

from sqlalchemy import String, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Content, ContentSource, SavedItem, Tap, Topic


async def list_saved_bookmarks(
    db: AsyncSession,
    user_id: int,
    *,
    topic_id: int | None,
    q: str | None,
    after: tuple[datetime, int] | None,
    limit: int,
) -> list[tuple[SavedItem, Content]]:
    """Return only items whose source and public detail remain accessible.

    A saved item can survive a content visibility change; those rows are omitted
    from this list so their titles and summaries cannot disclose hidden content.
    """
    statement = (
        select(SavedItem, Content)
        .join(Content, Content.content_id == SavedItem.content_id)
        .join(Tap, Tap.tap_id == SavedItem.tap_id)
        .join(Topic, Topic.topic_id == Tap.topic_id)
        .where(
            SavedItem.user_id == user_id,
            Tap.user_id == user_id,
            Tap.deleted_at.is_(None),
            Topic.is_active.is_(True),
            Content.topic_id == Tap.topic_id,
            SavedItem.status.in_(("kept", "resurface_off")),
            Content.status == "active",
            Content.deleted_at.is_(None),
            select(ContentSource.content_source_id)
            .where(ContentSource.content_id == Content.content_id)
            .exists(),
        )
    )
    if topic_id is not None:
        statement = statement.where(Tap.topic_id == topic_id)
    if q is not None:
        # Filter before pagination: search covers all saved rows, not just a page.
        pattern = f"%{q}%"
        statement = statement.where(or_(
            Content.title.ilike(pattern),
            Content.summary.ilike(pattern),
            SavedItem.saved_tags.cast(String).ilike(pattern),
        ))
    if after is not None:
        statement = statement.where(
            tuple_(SavedItem.saved_at, SavedItem.saved_item_id) < after
        )
    statement = statement.order_by(
        SavedItem.saved_at.desc(), SavedItem.saved_item_id.desc()
    ).limit(limit)
    return list((await db.execute(statement)).all())
