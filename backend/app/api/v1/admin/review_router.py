"""Review writes use real session/CSRF, current grants and a fresh transaction."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Request, Security
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_deps import require_admin
from app.api.deps import AuthContext, csrf_header_scheme, require_csrf
from app.core.exceptions import ApiError
from app.db.session import AsyncSessionLocal
from app.schemas.admin_content_review import AdminContentReviewRequest, AdminContentReviewResponse, AdminContentReviewScope
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.services.admin_content_review_service import review_content
from app.services.admin_review_permission_provider import (
    AdminReviewPermissionProvider, get_admin_review_permission_provider,
)


async def require_review_csrf(
    request: Request,
    context: Annotated[AuthContext, Depends(require_admin)],
    token: Annotated[str | None, Security(csrf_header_scheme)],
) -> AuthContext:
    # Reuse the CSRF checker with require_admin's real context; never resolve the
    # consumer dependency, which intentionally allows a local auth bypass.
    await require_csrf(request, context, token)
    return context


async def get_review_db():
    async with AsyncSessionLocal() as db:
        yield db


async def expected_review_scope(
    request: Request,
    actor: Annotated[str, Header(alias="X-Admin-Review-Actor", min_length=1, max_length=19, pattern=r"^[1-9][0-9]*$")],
    grant: Annotated[str, Header(alias="X-Admin-Review-Grant", min_length=1, max_length=19, pattern=r"^[1-9][0-9]*$")],
) -> AdminContentReviewScope:
    # Reject duplicate values even if their first values happen to be identical.
    if (len(request.headers.getlist("X-Admin-Review-Actor")) != 1
            or len(request.headers.getlist("X-Admin-Review-Grant")) != 1
            or int(actor) > 9223372036854775807 or int(grant) > 9223372036854775807):
        raise ApiError(422, "VALIDATION_ERROR", "검수 요청의 계정과 권한 정보를 확인해 주세요.")
    return AdminContentReviewScope(actor_user_id=int(actor), admin_account_id=int(grant))


router = APIRouter(prefix="/admin/contents", tags=["관리자 콘텐츠 검수"], responses={
    **COMMON_ERRORS, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
})


@router.post("/{contentId}/review", response_model=AdminContentReviewResponse)
async def submit_review(
    context: Annotated[AuthContext, Depends(require_review_csrf)],
    content_id: Annotated[int, Path(alias="contentId", gt=0, le=9223372036854775807)],
    request: AdminContentReviewRequest,
    db: Annotated[AsyncSession, Depends(get_review_db)],
    permissions: Annotated[AdminReviewPermissionProvider, Depends(get_admin_review_permission_provider)],
    expected_scope: Annotated[AdminContentReviewScope, Depends(expected_review_scope)],
):
    return await review_content(db, content_id, request, user_id=context.user.user_id,
        permissions=permissions, expected_scope=expected_scope)
