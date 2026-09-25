from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentHistory, Policy
from app.repositories.auth_session_repository import utcnow


async def get_current_policies(db: AsyncSession) -> dict[str, Policy]:
    result = await db.execute(
        select(Policy)
        .where(Policy.is_active.is_(True))
        .where(or_(Policy.effective_at.is_(None), Policy.effective_at <= utcnow()))
        .order_by(Policy.policy_type, Policy.effective_at.desc(), Policy.policy_id.desc())
    )
    current: dict[str, Policy] = {}
    for policy in result.scalars():
        current.setdefault(policy.policy_type, policy)
    return current


async def get_latest_consents(db: AsyncSession, user_id: int) -> dict[str, ConsentHistory]:
    result = await db.execute(
        select(ConsentHistory)
        .where(ConsentHistory.user_id == user_id)
        .order_by(ConsentHistory.consent_type, ConsentHistory.created_at.desc(), ConsentHistory.consent_history_id.desc())
    )
    latest: dict[str, ConsentHistory] = {}
    for row in result.scalars():
        latest.setdefault(row.consent_type, row)
    return latest


def add_consent_history(
    db: AsyncSession,
    *,
    user_id: int,
    consent_type: str,
    policy_version: str,
    agreed: bool,
    was_agreed: bool = False,
) -> ConsentHistory:
    now = utcnow()
    row = ConsentHistory(
        user_id=user_id,
        consent_type=consent_type,
        policy_version=policy_version,
        is_agreed=agreed,
        agreed_at=now if agreed else None,
        revoked_at=now if not agreed and was_agreed else None,
        created_at=now,
    )
    db.add(row)
    return row
