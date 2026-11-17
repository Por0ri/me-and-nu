"""API-068/069, separate from API-107/108/109 notification inbox."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.schemas.notification_settings import NotificationSettingsPatch, NotificationSettingsResponse
from app.services import notification_settings_service

router = APIRouter(prefix="/users/me/notification-settings", tags=["알림 설정"])
errors = {**COMMON_ERRORS, 409: {"model": ErrorResponse, "description": "NOTIFICATION_SETTINGS_CONFLICT 또는 NOTIFICATION_SETTINGS_NOT_INITIALIZED"}}


@router.get("", response_model=NotificationSettingsResponse, responses=errors,
            summary="내 내부 알림 설정 조회 (API-068)",
            description="PM 현재 구현 기준: 전체 ON, 야간 OFF. 설정 조회만 수행하며 필수 운영 공지와 기존 알림은 유지합니다.")
async def get_notification_settings(
    user: Annotated[UserAccount, Depends(require_onboarding)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationSettingsResponse:
    return await notification_settings_service.get_settings(db, user.user_id)


@router.patch("", response_model=NotificationSettingsResponse, responses=errors,
              summary="내 내부 알림 설정 변경 (API-069)",
              description="현재 version과 하나 이상의 boolean을 보냅니다. 성공한 PATCH마다 version이 증가합니다. "
                          "야간은 Asia/Seoul 21:00 이상~08:00 미만이며, 현재는 설정 저장만 제공합니다. "
                          "선택적 내부 알림 생성/표시 적용과 외부 푸시는 제공하지 않습니다.")
async def patch_notification_settings(
    payload: NotificationSettingsPatch,
    user: Annotated[UserAccount, Depends(require_onboarding)],
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationSettingsResponse:
    return await notification_settings_service.patch_settings(db, user.user_id, payload)
