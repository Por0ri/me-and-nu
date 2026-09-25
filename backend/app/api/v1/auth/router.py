"""Public session status and logout endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, Security
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    csrf_header_scheme,
    get_optional_auth_context,
    require_csrf,
)
from app.core.config import settings
from app.core.exceptions import ApiError
from app.core.security import (
    csrf_token_for_session,
    delete_session_cookie,
    set_session_cookie,
)
from app.db.session import get_db
from app.repositories.auth_session_repository import create_auth_session, revoke_auth_session
from app.schemas.auth import SessionResponse, SessionUser
from app.schemas.common import COMMON_ERRORS

router = APIRouter(prefix="/auth", tags=["인증·세션"])


@router.get(
    "/session",
    response_model=SessionResponse,
    summary="현재 세션과 온보딩 상태 조회",
    description="비로그인도 200을 반환합니다. 최초 요청에는 익명 세션 쿠키와 CSRF 토큰을 발급합니다. 변경 요청에는 csrfToken을 X-CSRF-Token 헤더에 넣으세요. 캐시하지 마세요.",
    responses={200: {"description": "익명 또는 인증 세션의 상태"}},
)
async def get_session(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    context: Annotated[AuthContext | None, Depends(get_optional_auth_context)],
) -> SessionResponse:
    response.headers["Cache-Control"] = "no-store"
    if context is None:
        _, raw_token = await create_auth_session(db, user_id=None, session_state="anonymous")
        await db.commit()
        set_session_cookie(response, raw_token)
        return SessionResponse(
            authenticated=False,
            session_state="anonymous",
            user=None,
            onboarding_completed=False,
            csrf_token=csrf_token_for_session(raw_token),
        )

    session = context.session
    user = context.user
    if user is None:
        return SessionResponse(
            authenticated=False,
            session_state="anonymous",
            user=None,
            onboarding_completed=False,
            csrf_token=csrf_token_for_session(context.raw_token),
        )
    complete = user.onboarding_completed_at is not None
    return SessionResponse(
        authenticated=True,
        session_state=session.session_state,
        user=SessionUser(id=user.user_id, account_type=user.signup_channel) if complete else None,
        onboarding_completed=complete,
        csrf_token=csrf_token_for_session(context.raw_token),
    )


@router.post(
    "/logout",
    status_code=204,
    summary="현재 기기 로그아웃",
    description="유효 세션에는 X-CSRF-Token이 필요합니다. 세션 폐기 후 쿠키를 삭제합니다. 반복 호출도 204이며 응답 본문은 없습니다.",
    responses={**COMMON_ERRORS, 204: {"description": "로그아웃됨. 본문 없음"}},
)
async def logout(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    context: Annotated[AuthContext | None, Depends(get_optional_auth_context)],
    csrf_token: Annotated[str | None, Security(csrf_header_scheme)],
) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in {str(request.base_url).rstrip("/"), *settings.cors_origins}:
        raise ApiError(403, "CSRF_INVALID", "요청 출처가 허용되지 않습니다.")
    if context is not None:
        await require_csrf(request, context, csrf_token)
        revoke_auth_session(context.session)
        await db.commit()
    delete_session_cookie(response)
