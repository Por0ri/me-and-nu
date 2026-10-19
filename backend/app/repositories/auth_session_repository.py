"""Storage operations for opaque browser sessions."""

from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ApiError
from app.core.security import create_session_id, csrf_token_for_session, hash_token
from app.models import AuthSession
from app.repositories.redis_session_store import RedisSessionStore

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_auth_session(
    db: AsyncSession,
    *,
    user_id: int | None,
    session_state: str,
    store: RedisSessionStore | None = None,
) -> tuple[AuthSession, str]:
    raw_token = create_session_id()
    row = AuthSession(
        user_id=user_id,
        session_token_hash=hash_token(raw_token),
        session_state=session_state,
        csrf_token_hash=hash_token(csrf_token_for_session(raw_token)),
        created_at=utcnow(),
        expires_at=utcnow() + timedelta(minutes=settings.session_expire_minutes),
    )
    if store is not None:
        await store.save(row)
    else:
        db.add(row)
        await db.flush()
    return row, raw_token


async def find_auth_session(
    db: AsyncSession, raw_token: str, *, store: RedisSessionStore | None = None
) -> AuthSession | None:
    if store is not None:
        return await store.find(raw_token)
    result = await db.execute(
        select(AuthSession).where(AuthSession.session_token_hash == hash_token(raw_token))
    )
    row = result.scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= utcnow():
        return None
    now = utcnow()
    accessed = row.last_accessed_at
    if accessed is None or (accessed.replace(tzinfo=timezone.utc) if accessed.tzinfo is None else accessed) <= now - timedelta(minutes=1):
        row.last_accessed_at = now
        await db.commit()
    return row


async def revoke_auth_session(
    row: AuthSession, *, store: RedisSessionStore | None = None
) -> None:
    if store is not None:
        await store.revoke(row)
    row.revoked_at = utcnow()


async def discard_uncommitted_session(
    row: AuthSession | None, *, store: RedisSessionStore | None
) -> None:
    if row is not None and store is not None:
        try:
            await store.revoke(row)
        except ApiError:
            logger.warning("Could not remove an unissued Redis session; it will expire automatically")
