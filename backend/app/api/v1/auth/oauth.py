"""Backend-owned OAuth code exchange and opaque session issuance."""

from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_optional_auth_context, require_csrf
from app.api.session_store import get_session_store
from app.core.config import settings
from app.core.exceptions import ApiError
from app.core.redis import get_redis
from app.core.security import hash_token, set_session_cookie
from app.db.session import get_db
from app.repositories.auth_session_repository import (
    create_auth_session,
    discard_uncommitted_session,
    find_auth_session,
    revoke_auth_session,
    utcnow,
)
from app.repositories.oauth_attempt_repository import (
    consume_oauth_attempt,
    create_oauth_attempt,
)
from app.schemas.common import COMMON_ERRORS
from app.schemas.oauth import OAuthLoginRequest, OAuthLoginResponse
from app.services.oauth_account_service import resolve_oauth_user
from app.services.oauth_provider import OAuthProviderClient

router = APIRouter(prefix="/auth", tags=["인증·세션"])


async def get_oauth_provider_client() -> AsyncIterator[OAuthProviderClient]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0), follow_redirects=False, trust_env=False
    ) as client:
        yield OAuthProviderClient(client)


def _require_provider(provider: str) -> None:
    if provider not in {"kakao", "naver"}:
        raise ApiError(422, "UNSUPPORTED_PROVIDER", "지원하지 않는 로그인 방식입니다.")
    if not getattr(settings, f"{provider}_oauth_enabled"):
        raise ApiError(503, "AUTH_UNAVAILABLE", "해당 소셜 로그인이 아직 준비되지 않았습니다.")


def _redirect(path: str, *, error: str | None = None) -> RedirectResponse:
    # Neither request headers nor user-supplied return URLs choose a destination.
    origin = settings.oauth_frontend_origin.rstrip("/")
    # Backend-only verification needs no frontend deployment. The existing
    # session endpoint shows the result using the HttpOnly cookie.
    target = f"{origin}{path}" if origin else "/api/v1/auth/session"
    if error:
        target += "?" + urlencode({"error": error})
    return RedirectResponse(
        target,
        status_code=303,
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


def _failure(code: str) -> RedirectResponse:
    allowed = {
        "AUTH_CANCELLED", "AUTH_FAILED", "AUTH_UNAVAILABLE",
        "AUTH_ACCOUNT_EXISTS", "AUTH_ACCOUNT_UNAVAILABLE",
    }
    return _redirect("/pivot/login", error=code if code in allowed else "AUTH_FAILED")


async def _rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except SQLAlchemyError:
        # A disconnected database must not mask the fixed callback failure.
        pass


@router.post(
    "/login/{provider}",
    response_model=OAuthLoginResponse,
    dependencies=[Depends(require_csrf)],
    summary="카카오·네이버 로그인 시작",
    description=(
        "먼저 /auth/session에서 익명 세션과 CSRF 토큰을 받으세요. "
        "authorizationUrl로 브라우저를 이동합니다. 현재 purpose=signin만 지원하며 "
        "reauth/restore는 후속 계정 기능에서 연결합니다."
    ),
    responses={
        **COMMON_ERRORS,
        429: {"description": "현재 브라우저의 로그인 시도 제한"},
        503: {"description": "공급자 미설정 또는 인증 저장소 장애"},
    },
)
async def start_login(
    provider: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    context: Annotated[AuthContext | None, Depends(get_optional_auth_context)],
    provider_client: Annotated[OAuthProviderClient, Depends(get_oauth_provider_client)],
    payload: OAuthLoginRequest | None = None,
) -> OAuthLoginResponse:
    _require_provider(provider)
    if context is None:
        raise ApiError(401, "AUTH_REQUIRED", "먼저 세션 상태를 조회해 주세요.")
    if context.is_dev_bypass:
        raise ApiError(503, "AUTH_UNAVAILABLE", "실제 소셜 로그인에서는 개발용 인증 우회를 꺼 주세요.")
    if payload is not None and payload.purpose != "signin":
        raise ApiError(422, "UNSUPPORTED_AUTH_PURPOSE", "재인증·탈퇴 복구는 아직 지원하지 않습니다.")
    state = await create_oauth_attempt(
        db,
        provider=provider,
        browser_session_hash=hash_token(context.raw_token),
        ttl_seconds=settings.oauth_state_ttl_seconds,
    )
    return OAuthLoginResponse(
        authorization_url=provider_client.build_authorization_url(provider, state)
    )


@router.get(
    "/login/{provider}/callback",
    response_class=RedirectResponse,
    status_code=303,
    summary="소셜 로그인 콜백·세션 발급",
    description=(
        "OAuth 공급자가 호출하는 주소입니다. 시작 브라우저·state·만료를 확인한 뒤 "
        "서비스 세션을 발급하고 303으로 이동합니다. 실패도 고정 오류 코드만 전달하며 "
        "인가 코드·토큰·공급자 오류 본문을 브라우저로 반환하지 않습니다."
    ),
)
async def login_callback(
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[Redis | None, Depends(get_redis)],
    provider_client: Annotated[OAuthProviderClient, Depends(get_oauth_provider_client)],
) -> RedirectResponse:
    new_session = None
    store = None
    try:
        _require_provider(provider)
        params = request.query_params
        # Reject ambiguous or oversized callback input before any provider call.
        if any(len(params.getlist(key)) > 1 for key in ("code", "state", "error")):
            return _failure("AUTH_FAILED")
        state = params.get("state", "")
        raw_cookie = request.cookies.get(settings.session_cookie_name, "")
        if not state or len(state) > 256 or not raw_cookie or len(raw_cookie) > 256:
            return _failure("AUTH_FAILED")
        store = get_session_store(redis_client)
        previous = await find_auth_session(db, raw_cookie, store=store)
        if previous is None:
            return _failure("AUTH_FAILED")
        valid = await consume_oauth_attempt(
            db, provider=provider, browser_session_hash=hash_token(raw_cookie), raw_state=state
        )
        if not valid:
            return _failure("AUTH_FAILED")
        # The state is committed as consumed before cancellation/code exchange.
        error = params.get("error")
        code = params.get("code", "")
        if error:
            if code:
                return _failure("AUTH_FAILED")
            return _failure("AUTH_CANCELLED" if error == "access_denied" else "AUTH_FAILED")
        if not code or len(code) > 4096:
            return _failure("AUTH_FAILED")
        identity = await provider_client.exchange_identity(provider, code, state)
        if identity.provider != provider:
            return _failure("AUTH_FAILED")
        user = await resolve_oauth_user(db, identity)
        user.last_login_at = utcnow()
        complete = user.onboarding_completed_at is not None
        new_session, raw_token = await create_auth_session(
            db,
            user_id=user.user_id,
            session_state="active" if complete else "onboarding_pending",
            store=store,
        )
        await revoke_auth_session(previous, store=store)
        await db.commit()
        response = _redirect("/pivot/home" if complete else "/pivot/consent")
        set_session_cookie(response, raw_token)
        return response
    except ApiError as exc:
        await _rollback(db)
        await discard_uncommitted_session(new_session, store=store)
        if exc.status_code >= 500:
            return _failure("AUTH_UNAVAILABLE")
        return _failure(exc.code)
    except SQLAlchemyError:
        await _rollback(db)
        await discard_uncommitted_session(new_session, store=store)
        return _failure("AUTH_UNAVAILABLE")
