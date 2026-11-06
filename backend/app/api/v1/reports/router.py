"""콘텐츠 신고 (API-070)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.schemas.report import ReportCreateRequest, ReportResponse
from app.services.report_service import DUPLICATE_REPORT, create_content_report

router = APIRouter(prefix="/reports", tags=["신고·문의"])


@router.post(
    "",
    status_code=201,
    response_model=ReportResponse,
    summary="콘텐츠 신고 접수",
    description="API-070. 지금은 콘텐츠(targetType=content)만 신고할 수 있습니다. reasonCode 는 wrong_info(잘못된 정보) · spoiler(스포일러) · rights(저작권 · 권리 침해) · inappropriate(불쾌하거나 부적절한 내용) · other(기타) 중 하나입니다. detail 은 선택이며 앞뒤 공백을 지운 500자 이하입니다. 한 회원이 같은 글은 한 번만 신고할 수 있고(409 REPORT_DUPLICATE), 공개 중이 아닌 글은 404 CONTENT_NOT_FOUND 입니다. 처리 결과 조회(API-071)는 아직 없습니다. 탈퇴해도 신고 기록은 글 검토용으로 남고 신고자만 비워집니다. 온보딩을 마친 회원만 쓸 수 있고 X-CSRF-Token이 필요합니다.",
    responses={
        **COMMON_ERRORS,
        404: {"model": ErrorResponse, "description": "공개 중인 콘텐츠가 아님", "content": {"application/json": {"example": {"code": "CONTENT_NOT_FOUND", "message": "콘텐츠를 찾을 수 없습니다."}}}},
        409: {"model": ErrorResponse, "description": "같은 글을 이미 신고함", "content": {"application/json": {"example": {"code": "REPORT_DUPLICATE", "message": DUPLICATE_REPORT}}}},
    },
)
async def report_content(
    payload: ReportCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
) -> ReportResponse:
    return await create_content_report(db, user_id=user.user_id, payload=payload)
