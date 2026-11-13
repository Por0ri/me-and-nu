"""Test-only wiring until permission DB integration is approved. NOT included
by the read router, V1 router or runtime app; there is no enable switch.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Security
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_deps import require_admin
from app.api.deps import AuthContext, csrf_header_scheme, require_csrf
from app.db.session import AsyncSessionLocal
from app.schemas.admin_content_review import AdminContentReviewRequest, AdminContentReviewResponse
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
):
    return await review_content(db, content_id, request, user_id=context.user.user_id, permissions=permissions)
