"""마이페이지 '문의 및 신고' 화면의 문의 접수 (명세 V1.1 에 없는 새 API)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS
from app.schemas.report import InquiryCreateRequest, InquiryResponse
from app.services.report_service import create_inquiry

router = APIRouter(prefix="/inquiries", tags=["신고·문의"])


@router.post(
    "",
    status_code=201,
    response_model=InquiryResponse,
    summary="마이페이지 문의 접수",
    description="마이페이지 '문의 및 신고' 화면용입니다. inquiryType 은 service(서비스 문의) · bug(버그/오류 신고)이고, detail 은 앞뒤 공백을 지운 1~500자로 꼭 필요합니다. 특정 글 신고는 POST /reports 를 씁니다. 답변 조회는 아직 없습니다. 탈퇴하면 문의도 같이 지워집니다. 온보딩을 마친 회원만 쓸 수 있고 X-CSRF-Token이 필요합니다.",
    responses=COMMON_ERRORS,
)
async def submit_inquiry(
    payload: InquiryCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
) -> InquiryResponse:
    return await create_inquiry(db, user_id=user.user_id, payload=payload)
