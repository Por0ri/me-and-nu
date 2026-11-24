"""Report/inquiry reads and separately authorized, audited write transactions."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, Security
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_deps import require_admin_support
from app.api.deps import AuthContext, csrf_header_scheme, require_csrf
from app.api.admin_search import read_admin_search
from app.core.exceptions import ApiError
from app.db.session import AsyncSessionLocal, get_db
from app.schemas.admin_support import (
    AdminSupportDetail, AdminSupportPage, AdminSupportQuery, SupportKind, SupportPeriod, SupportStatus,
)
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.services import admin_support_service as service
from app.services import admin_support_action_service as actions
from app.services.admin_support_permission_provider import AdminSupportPermissionProvider, get_admin_support_permission_provider
from app.schemas.admin_support_action import (
    AdminInquiryAnswerRequest, AdminReportActionRequest, AdminSupportActionResponse, AdminSupportScope,
)

router = APIRouter(prefix="/admin", tags=["관리자 신고·문의"], dependencies=[Depends(require_admin_support)],
    responses={**COMMON_ERRORS, 403: {"model": ErrorResponse, "description": "관리자 신고·문의 조회 권한 없음"},
               503: {"model": ErrorResponse, "description": "인증·권한 검증 또는 신고·문의 DB 조회 불가"}})
Database = Annotated[AsyncSession, Depends(get_db)]
Search = Annotated[str, Depends(read_admin_search)]
RecordId = Annotated[int, Path(gt=0, le=9223372036854775807)]
Actor = Annotated[AuthContext, Depends(require_admin_support)]
Permissions = Annotated[AdminSupportPermissionProvider, Depends(get_admin_support_permission_provider)]


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
async def report_detail(reportId: RecordId, db: Database, response: Response, actor: Actor, permissions: Permissions):
    _private(response)
    try:
        detail = await service.get_report(db, reportId)
        detail.actions = await actions.get_actions(db, user_id=actor.user.user_id, record_type="report",
                                                  raw_status=detail.raw_status, permissions=permissions)
        return detail
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
async def inquiry_detail(inquiryId: RecordId, db: Database, response: Response, actor: Actor, permissions: Permissions):
    _private(response)
    try:
        detail = await service.get_inquiry(db, inquiryId)
        detail.actions = await actions.get_actions(db, user_id=actor.user.user_id, record_type="inquiry",
                                                  raw_status=detail.raw_status, permissions=permissions)
        return detail
    except SQLAlchemyError:
        raise _unavailable() from None


async def require_support_csrf(
    request: Request, context: Actor,
    token: Annotated[str | None, Security(csrf_header_scheme)],
) -> AuthContext:
    await require_csrf(request, context, token)
    return context


async def get_support_write_db():
    # Authentication reads must not own the write transaction.
    async with AsyncSessionLocal() as db:
        yield db


async def expected_support_scope(
    request: Request,
    actor: Annotated[str, Header(alias="X-Admin-Support-Actor", min_length=1, max_length=19, pattern=r"^[1-9][0-9]*$")],
    grant: Annotated[str, Header(alias="X-Admin-Support-Grant", min_length=1, max_length=19, pattern=r"^[1-9][0-9]*$")],
) -> AdminSupportScope:
    if (len(request.headers.getlist("X-Admin-Support-Actor")) != 1
            or len(request.headers.getlist("X-Admin-Support-Grant")) != 1
            or int(actor) > 9223372036854775807 or int(grant) > 9223372036854775807):
        raise ApiError(422, "VALIDATION_ERROR", "처리 요청의 계정과 권한 정보를 확인해 주세요.")
    return AdminSupportScope(actor_user_id=actor, admin_account_id=grant)


WriteActor = Annotated[AuthContext, Depends(require_support_csrf)]
WriteDatabase = Annotated[AsyncSession, Depends(get_support_write_db)]
ExpectedScope = Annotated[AdminSupportScope, Depends(expected_support_scope)]


@router.post("/reports/{reportId}/actions", response_model=AdminSupportActionResponse,
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
async def report_action(reportId: RecordId, payload: AdminReportActionRequest, actor: WriteActor,
                        db: WriteDatabase, permissions: Permissions, expected_scope: ExpectedScope):
    return await actions.apply_report_action(db, reportId, payload, user_id=actor.user.user_id,
                                             permissions=permissions, expected_scope=expected_scope)


@router.post("/inquiries/{inquiryId}/answer", response_model=AdminSupportActionResponse,
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
async def inquiry_answer(inquiryId: RecordId, payload: AdminInquiryAnswerRequest, actor: WriteActor,
                         db: WriteDatabase, permissions: Permissions, expected_scope: ExpectedScope):
    return await actions.answer_inquiry(db, inquiryId, payload, user_id=actor.user.user_id,
                                        permissions=permissions, expected_scope=expected_scope)
