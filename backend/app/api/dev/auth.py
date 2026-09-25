"""Local-only email/password entry points replacing OAuth during V1 testing."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_optional_auth_context, require_csrf
from app.core.exceptions import ApiError
from app.core.security import set_session_cookie
from app.db.session import get_db
from app.repositories.auth_session_repository import create_auth_session, revoke_auth_session, utcnow
from app.schemas.auth import LoginRequest, LoginResponse, RegisterRequest, UserResponse
from app.schemas.common import COMMON_ERRORS
from app.services.user_service import EmailAlreadyRegisteredError, authenticate_user, register_user

router = APIRouter(prefix="/auth", tags=["개발용 인증"])

LOCAL_ONLY = "로컬 통합 테스트 전용입니다. ENABLE_DEV_API=false이면 경로와 OpenAPI 문서에서 사라집니다."


@router.post(
    "/register",
    status_code=201,
    response_model=UserResponse,
    summary="개발용 회원 생성",
    description=LOCAL_ONLY + " 먼저 GET /auth/session으로 익명 쿠키와 CSRF 토큰을 받으세요.",
    responses={**COMMON_ERRORS, 409: {"description": "이미 가입된 이메일", "content": {"application/json": {"example": {"code": "EMAIL_ALREADY_REGISTERED", "message": "이미 가입된 이메일입니다."}}}}},
)
async def register(
    register_request: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[None, Depends(require_csrf)],
) -> UserResponse:
    try:
        user = await register_user(db, register_request)
    except EmailAlreadyRegisteredError as exc:
        raise ApiError(409, "EMAIL_ALREADY_REGISTERED", "이미 가입된 이메일입니다.") from exc
    return UserResponse(
        id=user.user_id,
        email=user.email,
        nickname=user.nickname,
        account_type=user.signup_channel,
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="개발용 로그인",
    description=LOCAL_ONLY + " 로그인 전 GET /auth/session에서 받은 CSRF 토큰을 보내고, 로그인 후 세션을 재조회하세요.",
    responses={**COMMON_ERRORS},
)
async def login(
    login_request: LoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    context: Annotated[AuthContext | None, Depends(get_optional_auth_context)],
    _: Annotated[None, Depends(require_csrf)],
) -> LoginResponse:
    user = await authenticate_user(db, email=str(login_request.email), password=login_request.password)
    if user is None or user.account_status != "active":
        raise ApiError(401, "AUTH_REQUIRED", "이메일 또는 비밀번호가 올바르지 않습니다.")

    if context is not None:
        revoke_auth_session(context.session)
    state = "active" if user.onboarding_completed_at is not None else "onboarding_pending"
    _, raw_token = await create_auth_session(db, user_id=user.user_id, session_state=state)
    user.last_login_at = utcnow()
    await db.commit()
    set_session_cookie(response, raw_token)
    return LoginResponse(
        message="로그인되었습니다.",
        user=UserResponse(
            id=user.user_id,
            email=user.email,
            nickname=user.nickname,
            account_type=user.signup_channel,
        ),
    )
