"""콘텐츠 신고(API-070)와 마이페이지 문의 접수.

처리 결과를 보여 주는 API(071)와 관리자 화면은 아직 없어서, 접수된 것은 DB 에서 직접 확인합니다.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import ContentReport, Inquiry
from app.repositories.content_repository import get_visible_content
from app.schemas.report import InquiryCreateRequest, InquiryResponse, ReportCreateRequest, ReportResponse

DUPLICATE_REPORT = "이미 신고한 콘텐츠입니다."


async def create_content_report(db: AsyncSession, *, user_id: int, payload: ReportCreateRequest) -> ReportResponse:
    if await get_visible_content(db, payload.target_id) is None:
        raise ApiError(404, "CONTENT_NOT_FOUND", "콘텐츠를 찾을 수 없습니다.")
    already = await db.scalar(
        select(ContentReport.report_id).where(
            ContentReport.user_id == user_id, ContentReport.content_id == payload.target_id
        )
    )
    if already is not None:
        raise ApiError(409, "REPORT_DUPLICATE", DUPLICATE_REPORT)
    report = ContentReport(
        user_id=user_id, content_id=payload.target_id, reason_code=payload.reason_code, detail=payload.detail
    )
    db.add(report)
    try:
        await db.flush()
        report_id = report.report_id
        await db.commit()
    except IntegrityError:   # 같은 회원이 같은 글을 동시에 두 번 보낸 경우
        await db.rollback()
        raise ApiError(409, "REPORT_DUPLICATE", DUPLICATE_REPORT) from None
    return ReportResponse(report_id=report_id, status="received")


async def create_inquiry(db: AsyncSession, *, user_id: int, payload: InquiryCreateRequest) -> InquiryResponse:
    inquiry = Inquiry(user_id=user_id, inquiry_type=payload.inquiry_type, detail=payload.detail)
    db.add(inquiry)
    await db.flush()
    inquiry_id = inquiry.inquiry_id
    await db.commit()
    return InquiryResponse(inquiry_id=inquiry_id, status="received")
