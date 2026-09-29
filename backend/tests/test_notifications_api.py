from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.exceptions import ApiError
from app.main import create_app
from app.schemas.notification import NotificationReadRequest
from app.services import notification_service


def test_notification_routes_are_registered():
    paths = create_app(enable_dev_api=False).openapi()["paths"]
    assert set(paths["/api/v1/users/me/notifications"]) == {"get"}
    assert set(paths["/api/v1/users/me/notifications/unread-count"]) == {"get"}
    assert set(paths["/api/v1/users/me/notifications/{notificationId}"]) == {"patch"}


def test_read_request_accepts_only_true():
    assert NotificationReadRequest(is_read=True).is_read is True
    with pytest.raises(ValidationError):
        NotificationReadRequest(is_read=False)


@pytest.mark.asyncio
async def test_mark_read_sets_read_at_once():
    notification = SimpleNamespace(notification_id=7, read_at=None)
    db = SimpleNamespace(scalar=AsyncMock(return_value=notification), commit=AsyncMock())
    first = await notification_service.mark_read(db, 1, 7)
    assert first.is_read is True and first.read_at is not None
    stamp = first.read_at
    second = await notification_service.mark_read(db, 1, 7)
    assert second.read_at == stamp
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_read_of_other_users_notification_is_not_found():
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    with pytest.raises(ApiError) as error:
        await notification_service.mark_read(db, 1, 7)
    assert error.value.code == "NOTIFICATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_invalid_cursor_is_rejected(monkeypatch):
    monkeypatch.setattr(notification_service, "decode_cursor", lambda *_, **__: {"createdAt": "bad"})
    with pytest.raises(ApiError) as error:
        await notification_service.list_notifications(
            SimpleNamespace(), 1, unread_only=False, cursor="x", limit=20
        )
    assert error.value.code == "INVALID_CURSOR"
