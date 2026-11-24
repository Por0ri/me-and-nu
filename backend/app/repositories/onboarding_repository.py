from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TapConsumptionType, Topic, UserAccount


async def lock_user(db: AsyncSession, user_id: int) -> UserAccount | None:
    result = await db.execute(
        select(UserAccount)
        .where(UserAccount.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def find_active_topic(db: AsyncSession, topic_id: int) -> Topic | None:
    result = await db.execute(
        select(Topic).where(Topic.topic_id == topic_id, Topic.is_active.is_(True))
    )
    return result.scalar_one_or_none()


def add_consumption_types(db: AsyncSession, tap_id: int, types: Sequence[str]) -> None:
    """Q3 ranking rows in the order the member tapped them (rank 1 first)."""
    for rank, code in enumerate(types, start=1):
        db.add(TapConsumptionType(tap_id=tap_id, rank=rank, consumption_type=code))


async def load_consumption_types(db: AsyncSession, tap_id: int, first: str | None) -> list[str]:
    """Q3 ranking of a tap. first = onboarding_answers.consumption_type (rank 1).

    The production main code (same database) only rewrites that column when a member
    edits the taste, so the ranking rows count only while rank 1 still matches it;
    otherwise [first]. v1 answers (no first) have no ranking. The recommendation batch
    reads the same rule (model/algo/service.q3_answers).
    Known gap until production runs this code: if the older code changes rank 1 from A
    to B and back to A, the old ranking counts again (only the current value is compared).
    """
    if first is None:
        return []
    rows = (await db.execute(
        select(TapConsumptionType.rank, TapConsumptionType.consumption_type)
        .where(TapConsumptionType.tap_id == tap_id)
        .order_by(TapConsumptionType.rank)
    )).all()
    if rows and rows[0].rank == 1 and rows[0].consumption_type == first:
        return [row.consumption_type for row in rows]
    return [first]
