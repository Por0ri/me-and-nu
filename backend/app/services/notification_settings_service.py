"""Persist preferences only. Does not create, filter or delete notifications."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import NotificationSetting
from app.schemas.notification_settings import NotificationSettingsPatch, NotificationSettingsResponse


def _missing() -> ApiError:
    return ApiError(409, "NOTIFICATION_SETTINGS_NOT_INITIALIZED", "알림 설정이 준비되지 않았어요. 잠시 후 다시 확인해 주세요.")


async def get_settings(db: AsyncSession, user_id: int) -> NotificationSettingsResponse:
    row = await db.scalar(select(NotificationSetting).where(NotificationSetting.user_id == user_id))
    if row is None:
        raise _missing()
    return NotificationSettingsResponse.model_validate(row)


async def patch_settings(
    db: AsyncSession, user_id: int, payload: NotificationSettingsPatch,
) -> NotificationSettingsResponse:
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    try:
        # Compare-and-swap in one SQL statement: concurrent callers cannot both win.
        # Every accepted PATCH advances version, including a same-value request.
        row = await db.scalar(
            update(NotificationSetting)
            .where(NotificationSetting.user_id == user_id,
                   NotificationSetting.version == payload.version,
                   NotificationSetting.version < 2147483647)
            .values(**changes, version=NotificationSetting.version + 1)
            .returning(NotificationSetting)
        )
        if row is None:
            exists = await db.scalar(select(NotificationSetting.user_id).where(NotificationSetting.user_id == user_id))
            if exists is None:
                raise _missing()
            raise ApiError(409, "NOTIFICATION_SETTINGS_CONFLICT", "설정이 변경되었어요. 현재 설정을 다시 불러와 주세요.")
        result = NotificationSettingsResponse.model_validate(row)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise
