"""Resolve provider identities without linking accounts by email.

The callback owns the outer transaction and session issuance. This service only
flushes new accounts inside a savepoint so uniqueness races remain recoverable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models.user import UserAccount

if TYPE_CHECKING:
    from app.services.oauth_provider import OAuthIdentity

_EMAIL = TypeAdapter(EmailStr)


def _require_active(user: UserAccount) -> UserAccount:
    if user.account_status != "active":
        raise ApiError(403, "AUTH_ACCOUNT_UNAVAILABLE", "현재 로그인할 수 없는 계정입니다.")
    return user


def _email_conflict() -> ApiError:
    return ApiError(
        409,
        "AUTH_ACCOUNT_EXISTS",
        "이미 가입된 계정이 있습니다. 기존 가입 수단으로 로그인해 주세요.",
    )


async def _find_identity(db: AsyncSession, identity: OAuthIdentity) -> UserAccount | None:
    return await db.scalar(
        select(UserAccount)
        .where(
            UserAccount.auth_provider == identity.provider,
            UserAccount.provider_user_id == identity.provider_user_id,
        )
        .with_for_update()
    )


async def _email_exists(db: AsyncSession, email: str) -> bool:
    return await db.scalar(
        select(UserAccount.user_id).where(func.lower(UserAccount.email) == email).limit(1)
    ) is not None


async def resolve_oauth_user(db: AsyncSession, identity: OAuthIdentity) -> UserAccount:
    existing = await _find_identity(db, identity)
    if existing is not None:
        return _require_active(existing)

    email = None
    if identity.email and identity.email.strip():
        try:
            email = str(_EMAIL.validate_python(identity.email.strip())).lower()
        except ValidationError:
            raise ApiError(401, "AUTH_FAILED", "로그인 정보를 확인하지 못했습니다. 다시 시도해 주세요.") from None
        if await _email_exists(db, email):
            # The same identity may have appeared after the first lookup.
            existing = await _find_identity(db, identity)
            if existing is not None:
                return _require_active(existing)
            raise _email_conflict()

    user = UserAccount(
        auth_provider=identity.provider,
        provider_user_id=identity.provider_user_id,
        email=email,
        nickname=(identity.nickname or "").strip()[:50] or "새사용자",
        account_status="active",
        hashed_password=None,
    )
    try:
        async with db.begin_nested():
            db.add(user)
            await db.flush()
    except IntegrityError:
        # A concurrent callback may have created the identity after our lookup.
        existing = await _find_identity(db, identity)
        if existing is not None:
            return _require_active(existing)
        if email is not None and await _email_exists(db, email):
            raise _email_conflict() from None
        raise
    return user
