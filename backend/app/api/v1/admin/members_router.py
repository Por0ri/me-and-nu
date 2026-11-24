"""Masked member reads require a fresh explicit member capability."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_deps import require_admin_members
from app.api.admin_search import read_admin_search
from app.db.session import get_db
from app.schemas.admin_members import AdminMemberDetail, AdminMemberListResponse, AdminMemberQuery
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.services import admin_member_service

router = APIRouter(
    prefix="/admin/members", tags=["관리자 회원 조회"], dependencies=[Depends(require_admin_members)],
    responses={**COMMON_ERRORS, 403: {"model": ErrorResponse, "description": "관리자 회원 조회 권한 없음"},
               503: {"model": ErrorResponse, "description": "인증·권한 또는 회원 조회 불가"}},
)


@router.get("", response_model=AdminMemberListResponse)
async def list_members(query: Annotated[AdminMemberQuery, Query()],
                       search: Annotated[str, Depends(read_admin_search)],
                       db: Annotated[AsyncSession, Depends(get_db)]) -> AdminMemberListResponse:
    """검색어는 URL 대신 UTF-8 percent-encoded X-Admin-Search 헤더로 전달합니다."""
    return await admin_member_service.list_members(db, query, search)


@router.get("/{memberId}", response_model=AdminMemberDetail,
            responses={404: {"model": ErrorResponse, "description": "MEMBER_NOT_FOUND"}})
async def get_member(member_id: Annotated[int, Path(alias="memberId", gt=0, le=9223372036854775807)],
                     db: Annotated[AsyncSession, Depends(get_db)]) -> AdminMemberDetail:
    return await admin_member_service.get_member(db, member_id)
