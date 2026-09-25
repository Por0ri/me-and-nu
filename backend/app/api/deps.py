"""Session, onboarding, and CSRF dependencies shared by V1 resources."""

from dataclasses import dataclass
from datetime import timedelta
import hmac
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyCookie, APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ApiError
from app.core.security import csrf_token_for_session, hash_token
from app.db.session import get_db
from app.models import AuthSession, UserAccount
from app.repositories.auth_session_repository import find_auth_session, utcnow
from app.repositories.user_repository import get_user_by_email, get_user_by_id

from app.seeds.dev_user import DEV_USER_AUTH_PROVIDER, DEV_USER_EMAIL, DEV_USER_PROVIDER_ID

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_DEV_BYPASS_TOKEN = "menu-local-dev-auth-bypass"

session_cookie_scheme = APIKeyCookie(
    name=settings.session_cookie_name,
    scheme_name="SessionCookie",
    description="HttpOnly 세션 쿠키. 개발용 로그인 후 같은 출처에서 자동 전송됩니다.",
    auto_error=False,
)
csrf_header_scheme = APIKeyHeader(
    name="X-CSRF-Token",
    scheme_name="CsrfToken",
    description="GET /api/v1/auth/session 응답의 csrfToken을 입력합니다.",
    auto_error=False,
)


@dataclass(frozen=True)
class AuthContext:
    session: AuthSession
    raw_token: str
    user: UserAccount | None
    is_dev_bypass: bool = False


def local_dev_auth_bypass_enabled(request: Request) -> bool:
    """Allow the explicit bypass only when both ends of the request are local."""
    return (
        settings.enable_dev_api
        and settings.enable_dev_auth_bypass
        and request.url.hostname in _LOOPBACK_HOSTS
        and request.client is not None
        and request.client.host in _LOOPBACK_HOSTS
    )


async def get_dev_auth_context(db: AsyncSession) -> AuthContext:
    user = await get_user_by_email(db, DEV_USER_EMAIL)
    if (
        user is None
        or user.email != DEV_USER_EMAIL
        or user.auth_provider != DEV_USER_AUTH_PROVIDER
        or user.provider_user_id != DEV_USER_PROVIDER_ID
        or user.hashed_password is not None
        or user.signup_channel != "consumer"
        or user.account_status != "active"
        or user.onboarding_completed_at is None
    ):
        raise ApiError(
            503,
            "DEV_USER_NOT_READY",
            "개발용 사용자가 준비되지 않았습니다. python -m app.seeds.dev_user를 실행해 주세요.",
        )
    session = AuthSession(
        user_id=user.user_id,
        session_state="active",
        csrf_token_hash=hash_token(csrf_token_for_session(_DEV_BYPASS_TOKEN)),
        expires_at=utcnow() + timedelta(minutes=settings.session_expire_minutes),
    )
    return AuthContext(
        session=session,
        raw_token=_DEV_BYPASS_TOKEN,
        user=user,
        is_dev_bypass=True,
    )


async def get_optional_auth_context(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    raw_token: Annotated[str | None, Security(session_cookie_scheme)],
) -> AuthContext | None:
    if local_dev_auth_bypass_enabled(request):
        return await get_dev_auth_context(db)
    if not raw_token:
        return None
    session = await find_auth_session(db, raw_token)
    if session is None:
        return None
    user = None
    if session.user_id is not None:
        user = await get_user_by_id(db, session.user_id)
        if user is None or user.account_status != "active":
            return None
    return AuthContext(session=session, raw_token=raw_token, user=user)


async def get_auth_context(
    context: Annotated[AuthContext | None, Depends(get_optional_auth_context)],
) -> AuthContext:
    if context is None or context.user is None:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
    return context


async def get_current_user(
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> UserAccount:
    assert context.user is not None
    return context.user


async def require_onboarding(
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> UserAccount:
    user = context.user
    assert user is not None
    if context.session.session_state != "active" or user.onboarding_completed_at is None:
        raise ApiError(403, "ONBOARDING_REQUIRED", "온보딩을 완료해 주세요.")
    return user


async def require_pending_onboarding(
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    if context.user is None:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
    if context.user.onboarding_completed_at is not None:
        raise ApiError(409, "ONBOARDING_ALREADY_COMPLETED", "온보딩이 이미 완료되었습니다.")
    if context.session.session_state != "onboarding_pending":
        raise ApiError(409, "STATE_CONFLICT", "현재 세션에서는 온보딩을 진행할 수 없습니다.")
    return context


async def require_csrf(
    request: Request,
    context: Annotated[AuthContext | None, Depends(get_optional_auth_context)],
    csrf_token: Annotated[str | None, Security(csrf_header_scheme)],
) -> None:
    origin = request.headers.get("origin")
    if origin:
        allowed = {str(request.base_url).rstrip("/"), *settings.cors_origins}
        if origin.rstrip("/") not in allowed:
            raise ApiError(403, "CSRF_INVALID", "요청 출처가 허용되지 않습니다.")
    if context is None:
        raise ApiError(401, "AUTH_REQUIRED", "세션이 필요합니다. 먼저 세션 상태를 조회해 주세요.")
    if context.is_dev_bypass:
        return
    if not csrf_token or not hmac.compare_digest(hash_token(csrf_token), context.session.csrf_token_hash):
        raise ApiError(403, "CSRF_INVALID", "CSRF 토큰이 올바르지 않습니다.")
    # The raw token should match the session cookie, even if a DB row were
    # accidentally populated with another token's hash.
    if not hmac.compare_digest(csrf_token, csrf_token_for_session(context.raw_token)):
        raise ApiError(403, "CSRF_INVALID", "CSRF 토큰이 올바르지 않습니다.")
