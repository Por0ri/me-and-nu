"""Admin reads require a real session and a fresh server permission decision."""

from typing import Annotated

from fastapi import Depends, Security
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, session_cookie_scheme
from app.api.session_store import get_session_store
from app.core.exceptions import ApiError
from app.db.session import get_db
from app.repositories.auth_session_repository import find_auth_session
from app.repositories.redis_session_store import RedisSessionStore
from app.repositories.user_repository import get_user_by_id
from app.services.admin_permission_provider import (
    AdminPermissionProvider, get_admin_permission_provider,
)


async def require_admin(
    raw_token: Annotated[str | None, Security(session_cookie_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
    store: Annotated[RedisSessionStore | None, Depends(get_session_store)],
    permissions: Annotated[AdminPermissionProvider, Depends(get_admin_permission_provider)],
) -> AuthContext:
    if not raw_token:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")

    # Reuse session lookup (hash, expiry, revocation, configured session store)
    # directly: get_optional_auth_context intentionally supports a local bypass.
    try:
        session = await find_auth_session(db, raw_token, store=store)
        user = None
        if (session is not None and session.session_state == "active"
                and type(session.user_id) is int and session.user_id > 0):
            user = await get_user_by_id(db, session.user_id)
        authenticated = (user is not None and user.account_status == "active"
                         and user.user_id == session.user_id)
    except Exception:
        raise ApiError(503, "ADMIN_AUTH_UNAVAILABLE", "인증 정보를 확인할 수 없습니다.") from None
    if not authenticated:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")

    try:
        allowed = await permissions.can_read_contents(db, user_id=user.user_id)
    except Exception:
        raise ApiError(503, "ADMIN_PERMISSION_UNAVAILABLE", "관리자 권한을 확인할 수 없습니다.") from None
    if allowed is not True:
        raise ApiError(403, "ADMIN_REQUIRED", "관리자 권한이 필요합니다.")
    return AuthContext(session=session, raw_token=raw_token, user=user, session_store=store)
