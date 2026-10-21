"""PostgreSQL OAuth state storage, independent of the browser session backend."""

from datetime import datetime, timedelta, timezone
import secrets

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.core.security import hash_token
from app.models.oauth_attempt import OAuthAttempt


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        # A disconnected database must not replace the neutral public error.
        pass


def _unavailable() -> ApiError:
    return ApiError(503, "AUTH_UNAVAILABLE", "로그인 요청을 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.")


async def create_oauth_attempt(
    db: AsyncSession,
    *,
    provider: str,
    browser_session_hash: str,
    ttl_seconds: int,
    limit: int = 10,
    window_seconds: int = 600,
) -> str:
    """Commit a random state, allowing at most ``limit`` attempts per browser.

    A PostgreSQL transaction advisory lock serializes the count and insert for
    this browser, including attempts with a different OAuth provider. Consumed
    attempts still count until the rate window ends.
    """
    if provider not in {"kakao", "naver"}:
        raise ValueError("Unsupported OAuth provider")
    if ttl_seconds <= 0 or limit <= 0 or window_seconds <= 0:
        raise ValueError("OAuth attempt limits must be positive")
    if len(browser_session_hash) != 64 or any(c not in "0123456789abcdef" for c in browser_session_hash):
        raise ValueError("A SHA-256 browser session hash is required")

    # Advisory locks accept a signed bigint; hash collisions only serialize
    # unrelated browsers, since the actual count uses the complete hash.
    lock_key = int(browser_session_hash[:16], 16)
    if lock_key >= 1 << 63:
        lock_key -= 1 << 64

    try:
        await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
        now = _utcnow()
        window_start = now - timedelta(seconds=window_seconds)
        count = await db.execute(
            select(func.count()).select_from(OAuthAttempt).where(
                OAuthAttempt.browser_session_hash == browser_session_hash,
                OAuthAttempt.created_at > window_start,
            )
        )
        if count.scalar_one() >= limit:
            raise ApiError(429, "RATE_LIMITED", "로그인 요청이 많습니다. 잠시 후 다시 시도해 주세요.")

        await db.execute(
            delete(OAuthAttempt).where(
                OAuthAttempt.expires_at <= now,
                OAuthAttempt.created_at <= window_start,
            ).execution_options(synchronize_session=False)
        )
        raw_state = secrets.token_urlsafe(32)
        db.add(OAuthAttempt(
            state_hash=hash_token(raw_state),
            browser_session_hash=browser_session_hash,
            provider=provider,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        ))
        await db.commit()
        return raw_state
    except ApiError:
        await _rollback(db)
        raise
    except Exception:
        await _rollback(db)
        raise _unavailable() from None


async def consume_oauth_attempt(
    db: AsyncSession,
    *,
    provider: str,
    browser_session_hash: str,
    raw_state: str,
) -> bool:
    """Atomically consume and commit state before any provider network call."""
    if not raw_state or len(raw_state) > 256:
        return False
    now = _utcnow()
    try:
        result = await db.execute(
            update(OAuthAttempt)
            .where(
                OAuthAttempt.state_hash == hash_token(raw_state),
                OAuthAttempt.provider == provider,
                OAuthAttempt.browser_session_hash == browser_session_hash,
                OAuthAttempt.consumed_at.is_(None),
                OAuthAttempt.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(OAuthAttempt.state_hash)
            .execution_options(synchronize_session=False)
        )
        consumed = result.scalar_one_or_none() is not None
        await db.commit()
        return consumed
    except Exception:
        await _rollback(db)
        raise _unavailable() from None
