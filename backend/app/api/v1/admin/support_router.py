"""Read-only report/inquiry endpoints under a distinct support permission."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_deps import require_admin_support
from app.api.admin_search import read_admin_search
from app.core.exceptions import ApiError
from app.db.session import get_db
from app.schemas.admin_support import (
    AdminSupportDetail, AdminSupportPage, AdminSupportQuery, SupportKind, SupportPeriod, SupportStatus,
)
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.services import admin_support_service as service

router = APIRouter(prefix="/admin", tags=["관리자 신고·문의"], dependencies=[Depends(require_admin_support)],
    responses={**COMMON_ERRORS, 403: {"model": ErrorResponse, "description": "관리자 신고·문의 조회 권한 없음"},
               503: {"model": ErrorResponse, "description": "인증·권한 검증 또는 신고·문의 DB 조회 불가"}})
Database = Annotated[AsyncSession, Depends(get_db)]
Search = Annotated[str, Depends(read_admin_search)]
RecordId = Annotated[int, Path(gt=0, le=9223372036854775807)]


def _private(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Cookie, X-Admin-Search"


def _unavailable():
    return ApiError(503, "ADMIN_SUPPORT_UNAVAILABLE", "신고·문의 내역을 조회할 수 없습니다. 잠시 후 다시 시도해 주세요.")


@router.get("/reports", response_model=AdminSupportPage)
async def reports(
    db: Database, response: Response, search: Search,
    kind: Annotated[SupportKind, Query()] = "all", status: Annotated[SupportStatus, Query()] = "all",
    period: Annotated[SupportPeriod, Query()] = "7d", page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 5,
):
    """One stable union page. Statistics use period/kind/search before status filtering.

    Search is supplied only through X-Admin-Search, never through a URL query.
    received→waiting; reviewing→reviewing; resolved/rejected/answered/closed→completed.
    rawStatus preserves these distinct terminal states. No body or reporter identity is listed.
    """
    _private(response)
    try:
        return await service.list_support(db, AdminSupportQuery(kind=kind, status=status, period=period,
            q=search, page=page, page_size=page_size))
    except SQLAlchemyError:
        raise _unavailable() from None


@router.get("/reports/{reportId}", response_model=AdminSupportDetail, responses={404: {"model": ErrorResponse}})
async def report_detail(reportId: RecordId, db: Database, response: Response):
    _private(response)
    try:
        return await service.get_report(db, reportId)
    except SQLAlchemyError:
        raise _unavailable() from None


@router.get("/inquiries", response_model=AdminSupportPage)
async def inquiries(
    db: Database, response: Response, search: Search,
    kind: Annotated[Literal["all", "service", "bug"], Query()] = "all",
    status: Annotated[SupportStatus, Query()] = "all", period: Annotated[SupportPeriod, Query()] = "7d",
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 5,
):
    _private(response)
    try:
        return await service.list_support(db, AdminSupportQuery(kind=kind, status=status, period=period,
            q=search, page=page, page_size=page_size), only_inquiries=True)
    except SQLAlchemyError:
        raise _unavailable() from None


@router.get("/inquiries/{inquiryId}", response_model=AdminSupportDetail, responses={404: {"model": ErrorResponse}})
async def inquiry_detail(inquiryId: RecordId, db: Database, response: Response):
    _private(response)
    try:
        return await service.get_inquiry(db, inquiryId)
    except SQLAlchemyError:
        raise _unavailable() from None
