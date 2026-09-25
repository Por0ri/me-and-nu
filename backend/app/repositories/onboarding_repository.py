from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subtopic, Topic, UserAccount


async def lock_user(db: AsyncSession, user_id: int) -> UserAccount | None:
    result = await db.execute(
        select(UserAccount).where(UserAccount.user_id == user_id).with_for_update()
    )
    return result.scalar_one_or_none()


async def find_active_topic(db: AsyncSession, topic_id: int) -> Topic | None:
    result = await db.execute(
        select(Topic).where(Topic.topic_id == topic_id, Topic.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def find_subtopics(db: AsyncSession, ids: list[int]) -> list[Subtopic]:
    result = await db.execute(select(Subtopic).where(Subtopic.subtopic_id.in_(ids)))
    return list(result.scalars())
