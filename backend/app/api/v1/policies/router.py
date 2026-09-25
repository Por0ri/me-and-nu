from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.policy import PoliciesResponse
from app.services.policy_service import get_policies

router = APIRouter(prefix="/policies", tags=["정책"])


@router.get(
    "",
    response_model=PoliciesResponse,
    summary="현재 동의 정책 조회",
    description="비로그인 공개 API입니다. 온보딩 화면에 실제로 표시한 각 policyVersion을 그대로 제출해야 합니다. 로컬 테스트 정책은 seed 명령으로 준비합니다.",
    responses={200: {"description": "활성 정책 목록", "content": {"application/json": {"example": {"consentItems": [{"type": "terms", "policyVersion": "v1", "required": True, "text": "로컬 테스트용 이용약관"}], "aiNotice": "AI 기능은 현재 로컬 V1 테스트 범위에 포함되지 않습니다.", "chatRetention": {"days": None, "status": "undecided"}}}}}},
)
async def list_policies(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PoliciesResponse:
    return await get_policies(db)
