"""Resolve provider identities without linking accounts by email.

The callback owns the outer transaction and session issuance. Account creation
and missing-email updates use savepoints so uniqueness races remain recoverable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models.user import UserAccount
from app.services.withdrawal_service import in_grace_period

if TYPE_CHECKING:
    from app.services.oauth_provider import OAuthIdentity

_EMAIL = TypeAdapter(EmailStr)


def _require_loginable(user: UserAccount) -> UserAccount:
    """활성 계정과 탈퇴 유예 중인 계정만 통과합니다. 유예 중이면 콜백이 복구 대기 세션을 줍니다."""
    if user.account_status != "active" and not in_grace_period(user):
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


def _is_email_unique_violation(error: IntegrityError) -> bool:
    original = error.orig
    # Production uses psycopg; SQLite supports the isolated service tests.
    if getattr(original, "sqlstate", None) == "23505":
        return getattr(getattr(original, "diag", None), "constraint_name", None) == "uq_user_account_email"
    return (
        getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
        and str(original) == "UNIQUE constraint failed: index 'uq_user_account_email'"
    )


async def _reuse_identity(
    db: AsyncSession, user: UserAccount, identity: OAuthIdentity,
) -> UserAccount:
    _require_loginable(user)
    if user.email and user.email.strip():
        return user
    if not isinstance(identity.email, str) or not identity.email.strip():
        return user
    try:
        email = str(_EMAIL.validate_python(identity.email.strip())).lower()
    except ValidationError:
        # Optional profile enrichment must not reject an existing identity.
        return user
    if await _email_exists(db, email):
        return user

    updating_email = False
    try:
        async with db.begin_nested():
            # Assign only after the savepoint's automatic pre-flush has finished.
            updating_email = True
            user.email = email
            await db.flush()
    except IntegrityError as error:
        if not updating_email or not _is_email_unique_violation(error):
            raise
        # Another account claimed this email. Keep login available, and eagerly
        # reload attributes expired by rollback before the async callback reads them.
        await db.refresh(user)
    return user


async def resolve_oauth_user(db: AsyncSession, identity: OAuthIdentity) -> UserAccount:
    existing = await _find_identity(db, identity)
    if existing is not None:
        return await _reuse_identity(db, existing, identity)

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
                return await _reuse_identity(db, existing, identity)
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
            return await _reuse_identity(db, existing, identity)
        if email is not None and await _email_exists(db, email):
            raise _email_conflict() from None
        raise
    return user
