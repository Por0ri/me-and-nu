from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subtopic, Tap, TapTopic, Topic


async def get_active_topic(db: AsyncSession, topic_id: int) -> Topic | None:
    return await db.scalar(
        select(Topic).where(Topic.topic_id == topic_id, Topic.is_active.is_(True))
    )


async def list_active_topics(db: AsyncSession, q: str | None) -> list[Topic]:
    statement = select(Topic).where(Topic.is_active.is_(True))
    if q:
        statement = statement.where(Topic.topic_name.ilike(f"%{q.strip()}%"))
    return list((await db.scalars(statement.order_by(Topic.sort_order, Topic.topic_id))).all())


async def get_subtopic(db: AsyncSession, subtopic_id: int) -> Subtopic | None:
    return await db.get(Subtopic, subtopic_id)


async def list_subtopics(
    db: AsyncSession,
    topic_id: int,
    *,
    q: str | None,
    parent_subtopic_id: int | None,
    after_id: int | None,
    limit: int,
) -> list[Subtopic]:
    statement = select(Subtopic).where(Subtopic.topic_id == topic_id)
    if q:
        statement = statement.where(Subtopic.subtopic_name.ilike(f"%{q.strip()}%"))
    if parent_subtopic_id is not None:
        statement = statement.where(Subtopic.parent_subtopic_id == parent_subtopic_id)
    if after_id is not None:
        statement = statement.where(Subtopic.subtopic_id > after_id)
    return list(
        (await db.scalars(statement.order_by(Subtopic.subtopic_id).limit(limit))).all()
    )


async def get_tap(
    db: AsyncSession, user_id: int, topic_id: int, *, include_deleted: bool = False
) -> Tap | None:
    statement = select(Tap).where(Tap.user_id == user_id, Tap.topic_id == topic_id)
    if not include_deleted:
        statement = statement.where(Tap.deleted_at.is_(None))
    return await db.scalar(statement.order_by(Tap.tap_id.desc()))


async def list_taps(db: AsyncSession, user_id: int, include_deleted: bool) -> list[tuple[Tap, Topic]]:
    statement = select(Tap, Topic).join(Topic, Tap.topic_id == Topic.topic_id).where(
        Tap.user_id == user_id
    )
    if not include_deleted:
        statement = statement.where(Tap.deleted_at.is_(None))
    return list((await db.execute(statement.order_by(Topic.sort_order, Topic.topic_id))).all())


async def list_tap_subtopics(db: AsyncSession, tap_id: int) -> list[tuple[TapTopic, Subtopic]]:
    statement = (
        select(TapTopic, Subtopic)
        .join(Subtopic, TapTopic.subtopic_id == Subtopic.subtopic_id)
        .where(TapTopic.tap_id == tap_id)
        .order_by(TapTopic.subscribed_at, TapTopic.subtopic_id)
    )
    return list((await db.execute(statement)).all())


async def get_tap_subtopic(db: AsyncSession, tap_id: int, subtopic_id: int) -> TapTopic | None:
    return await db.get(TapTopic, (tap_id, subtopic_id))


def add_tap(db: AsyncSession, user_id: int, topic_id: int) -> Tap:
    tap = Tap(user_id=user_id, topic_id=topic_id)
    db.add(tap)
    return tap


def add_tap_subtopic(db: AsyncSession, tap_id: int, subtopic_id: int) -> TapTopic:
    item = TapTopic(tap_id=tap_id, subtopic_id=subtopic_id)
    db.add(item)
    return item
