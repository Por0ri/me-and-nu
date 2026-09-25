from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS
from app.schemas.policy import ConsentHistoryResponse, ConsentPatchRequest, ConsentPatchResponse
from app.services.policy_service import get_user_consents, patch_user_consents

router = APIRouter(prefix="/users/me/consents", tags=["사용자"])


@router.get(
    "",
    response_model=ConsentHistoryResponse,
    summary="내 최신 동의 내역 조회",
    description="완료된 로그인 세션이 필요합니다. 동의 항목별 최신 이력과 서버 기록 시각을 반환합니다.",
    responses=COMMON_ERRORS,
)
async def get_consents(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserAccount, Depends(require_onboarding)],
) -> ConsentHistoryResponse:
    return await get_user_consents(db, user.user_id)


@router.patch(
    "",
    response_model=ConsentPatchResponse,
    summary="내 선택 동의 변경",
    description="활성 정책 버전만 허용하며 필수 동의 철회는 거절합니다. 변경 이력은 새 행에 기록합니다. X-CSRF-Token이 필요합니다.",
    responses={**COMMON_ERRORS, 409: {"description": "정책 버전 충돌", "content": {"application/json": {"example": {"code": "POLICY_VERSION_CONFLICT", "message": "정책 버전이 변경되었습니다. 정책을 다시 조회해 주세요."}}}}},
)
async def patch_consents(
    payload: ConsentPatchRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
) -> ConsentPatchResponse:
    return await patch_user_consents(db, user_id=user.user_id, items=payload.items)
