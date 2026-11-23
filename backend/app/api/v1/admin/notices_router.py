"""Current-grant notice reads and CSRF-protected audited authoring."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, Security
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_deps import require_admin_notices
from app.api.admin_search import read_admin_search
from app.api.deps import AuthContext, csrf_header_scheme, require_csrf
from app.core.exceptions import ApiError
from app.db.session import AsyncSessionLocal, get_db
from app.schemas.admin_notice import (
    AdminNoticeActionRequest, AdminNoticeCreateRequest, AdminNoticeDetail,
    AdminNoticePage, AdminNoticeQuery, AdminNoticeScope, AdminNoticeWriteResponse,
)
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.services import admin_notice_service as service
from app.services.admin_notice_permission_provider import (
    AdminNoticePermissionProvider, get_admin_notice_permission_provider,
)

router = APIRouter(prefix="/admin/notices", tags=["관리자 알림·공지"],
    dependencies=[Depends(require_admin_notices)], responses={**COMMON_ERRORS,
        503: {"model": ErrorResponse, "description": "인증·권한 또는 공지 저장소 확인 불가"}})
Database = Annotated[AsyncSession, Depends(get_db)]
Actor = Annotated[AuthContext, Depends(require_admin_notices)]
Permissions = Annotated[AdminNoticePermissionProvider, Depends(get_admin_notice_permission_provider)]
NoticeId = Annotated[int, Path(gt=0, le=9223372036854775807)]


def _private(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Cookie, X-Admin-Search"


def _unavailable():
    return ApiError(503, "ADMIN_NOTICE_UNAVAILABLE", "공지를 조회할 수 없습니다. 잠시 후 다시 시도해 주세요.")


@router.get("", response_model=AdminNoticePage)
async def notices(db: Database, response: Response, actor: Actor, permissions: Permissions,
    search: Annotated[str, Depends(read_admin_search)],
    status: Annotated[Literal["all", "draft", "scheduled", "published"], Query()] = "all",
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 7,
):
    """Statistics use title search before status filtering. readCount means read recipients.

    Search is percent-encoded in X-Admin-Search, never a URL query. Capability
    scope binds a future retry; it never replaces the authenticated DB grant.
    """
    _private(response)
    try:
        result = await service.list_notices(db, AdminNoticeQuery(status=status, q=search, page=page, page_size=page_size))
        result.capabilities = await service.capabilities(db, user_id=actor.user.user_id, permissions=permissions)
        return result
    except SQLAlchemyError:
        raise _unavailable() from None


@router.get("/{noticeId}", response_model=AdminNoticeDetail, responses={404: {"model": ErrorResponse}})
async def notice_detail(noticeId: NoticeId, db: Database, response: Response, actor: Actor, permissions: Permissions):
    _private(response)
    try:
        result = await service.get_notice(db, noticeId)
        result.actions = await service.capabilities(db, user_id=actor.user.user_id,
                                                   permissions=permissions, status=result.status)
        return result
    except SQLAlchemyError:
        raise _unavailable() from None


async def require_notice_csrf(request: Request, context: Actor,
    token: Annotated[str | None, Security(csrf_header_scheme)],
) -> AuthContext:
    await require_csrf(request, context, token)
    return context


async def get_notice_write_db():
    async with AsyncSessionLocal() as db:
        yield db


async def expected_notice_scope(request: Request,
    actor: Annotated[str, Header(alias="X-Admin-Notice-Actor", min_length=1, max_length=19, pattern=r"^[1-9][0-9]*$")],
    grant: Annotated[str, Header(alias="X-Admin-Notice-Grant", min_length=1, max_length=19, pattern=r"^[1-9][0-9]*$")],
) -> AdminNoticeScope:
    if (len(request.headers.getlist("X-Admin-Notice-Actor")) != 1
            or len(request.headers.getlist("X-Admin-Notice-Grant")) != 1
            or int(actor) > 9223372036854775807 or int(grant) > 9223372036854775807):
        raise ApiError(422, "VALIDATION_ERROR", "공지 요청의 계정과 권한 정보를 확인해 주세요.")
    return AdminNoticeScope(actor_user_id=actor, admin_account_id=grant)


WriteActor = Annotated[AuthContext, Depends(require_notice_csrf)]
WriteDatabase = Annotated[AsyncSession, Depends(get_notice_write_db)]
ExpectedScope = Annotated[AdminNoticeScope, Depends(expected_notice_scope)]


@router.post("", response_model=AdminNoticeWriteResponse, responses={409: {"model": ErrorResponse}})
async def create_notice(payload: AdminNoticeCreateRequest, actor: WriteActor, db: WriteDatabase,
                        permissions: Permissions, expected_scope: ExpectedScope):
    """Persist draft, publish immediately, or schedule; all success responses are 200.

    A lost response is retried with the same requestId, complete body and scope.
    The persisted audit returns the original result while that grant is current.
    """
    return await service.create_notice(db, payload, user_id=actor.user.user_id,
                                       permissions=permissions, expected_scope=expected_scope)


@router.post("/{noticeId}/actions", response_model=AdminNoticeWriteResponse,
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
async def notice_action(noticeId: NoticeId, payload: AdminNoticeActionRequest, actor: WriteActor,
                        db: WriteDatabase, permissions: Permissions, expected_scope: ExpectedScope):
    """Edit/publish/schedule a draft, or cancel a schedule back to draft.

    Published notices are immutable. Copy expectedUpdatedAt verbatim from GET,
    including microseconds and timezone offset; a conflict requires fresh detail.
    """
    return await service.apply_notice_action(db, noticeId, payload, user_id=actor.user.user_id,
                                             permissions=permissions, expected_scope=expected_scope)
