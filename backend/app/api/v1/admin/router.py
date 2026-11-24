"""Administrative reads protected by a real session and current DB grant."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_deps import require_admin
from app.api.deps import AuthContext
from app.db.session import get_db
from app.schemas.admin_content import AdminContentDetail, AdminContentListResponse, AdminContentQuery
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.services import admin_content_service
from app.services.admin_review_eligibility_service import get_review_eligibility
from app.services.admin_review_permission_provider import AdminReviewPermissionProvider, get_admin_review_permission_provider

router = APIRouter(
    prefix="/admin/contents",
    tags=["관리자 콘텐츠 조회"],
    dependencies=[Depends(require_admin)],
    responses={
        **COMMON_ERRORS,
        403: {"model": ErrorResponse, "description": "ADMIN_REQUIRED: 관리자 조회 권한 없음"},
        503: {"model": ErrorResponse, "description": "인증 또는 관리자 권한 검증 불가"},
        500: {"model": ErrorResponse, "description": "조회 실패"},
    },
)


@router.get("", response_model=AdminContentListResponse)
async def list_contents(
    query: Annotated[AdminContentQuery, Query()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminContentListResponse:
    """전체 또는 needs_review 대기함. 대기함은 active, pending_review 순 우선 조회."""
    return await admin_content_service.list_contents(db, query)


@router.get(
    "/{contentId}", response_model=AdminContentDetail,
    responses={404: {"model": ErrorResponse, "description": "CONTENT_NOT_FOUND"}},
)
async def get_content(
    content_id: Annotated[int, Path(alias="contentId", gt=0, le=9223372036854775807)],
    db: Annotated[AsyncSession, Depends(get_db)],
    context: Annotated[AuthContext, Depends(require_admin)],
    permissions: Annotated[AdminReviewPermissionProvider, Depends(get_admin_review_permission_provider)],
) -> AdminContentDetail:
    detail = await admin_content_service.get_content(db, content_id)
    detail.review = await get_review_eligibility(
        db, user_id=context.user.user_id, content_id=content_id, permissions=permissions,
    )
    return detail
