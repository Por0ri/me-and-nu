"""API-107·108·109 notification inbox."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.schemas.notification import (
    NotificationReadRequest,
    NotificationReadResponse,
    NotificationsResponse,
    UnreadCountResponse,
)
from app.services import notification_service

router = APIRouter(prefix="/users/me/notifications", tags=["알림"])


@router.get(
    "",
    response_model=NotificationsResponse,
    summary="내 알림 목록 조회 (API-107)",
    description="최신순으로 반환합니다. 첫 페이지를 열 때 내 분야의 최근 30일 새 글과 환영 공지를 알림으로 채웁니다.",
    responses={**COMMON_ERRORS, 422: {"model": ErrorResponse, "description": "VALIDATION_ERROR 또는 INVALID_CURSOR"}},
)
async def list_notifications(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
    unread_only: Annotated[bool, Query(alias="unreadOnly")] = False,
    cursor: Annotated[str | None, Query(description="이전 응답의 nextCursor")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> NotificationsResponse:
    return await notification_service.list_notifications(
        db, current_user.user_id, unread_only=unread_only, cursor=cursor, limit=limit
    )


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="내 미확인 알림 개수 (API-108)",
    responses=COMMON_ERRORS,
)
async def get_unread_count(
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UnreadCountResponse:
    return await notification_service.unread_count(db, current_user.user_id)


@router.patch(
    "/{notificationId}",
    response_model=NotificationReadResponse,
    summary="내 알림 읽음 처리 (API-109)",
    description="isRead는 true만 받습니다. 이미 읽은 알림도 200을 반환합니다.",
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "NOTIFICATION_NOT_FOUND"}},
)
async def mark_notification_read(
    request: NotificationReadRequest,
    current_user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
    notification_id: Annotated[int, Path(alias="notificationId", gt=0)],
) -> NotificationReadResponse:
    return await notification_service.mark_read(db, current_user.user_id, notification_id)
