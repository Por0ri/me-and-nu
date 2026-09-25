from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_csrf, require_pending_onboarding
from app.core.security import set_session_cookie
from app.db.session import get_db
from app.schemas.common import COMMON_ERRORS
from app.schemas.onboarding import OnboardingRequest, OnboardingResponse
from app.services.onboarding_service import complete_onboarding

router = APIRouter(prefix="/onboarding", tags=["온보딩"])


@router.post(
    "",
    status_code=201,
    response_model=OnboardingResponse,
    summary="회원 온보딩 일괄 완료",
    description="onboarding_pending 세션과 X-CSRF-Token이 필요합니다. 프로필, 동의 이력, 최초 관심사, 기본 알림 설정, 활성 세션을 한 DB 트랜잭션으로 저장합니다. 완료 후 세션 ID가 교체되므로 GET /auth/session을 다시 호출하세요.",
    responses={
        **COMMON_ERRORS,
        201: {"description": "온보딩 완료"},
        404: {"description": "Topic 또는 이미지 없음", "content": {"application/json": {"example": {"code": "TOPIC_NOT_FOUND", "message": "Topic을 찾을 수 없습니다."}}}},
        409: {"description": "중복 온보딩 또는 정책 버전 충돌", "content": {"application/json": {"example": {"code": "POLICY_VERSION_CONFLICT", "message": "정책 버전이 변경되었습니다. 정책을 다시 조회해 주세요."}}}},
    },
)
async def onboarding(
    payload: OnboardingRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_pending_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
) -> OnboardingResponse:
    result, raw_token = await complete_onboarding(db, context=context, payload=payload)
    set_session_cookie(response, raw_token)
    return result
