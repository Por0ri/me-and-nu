from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.exceptions import ApiError
from app.main import create_app
from app.models import Notification
from app.schemas.notification import NotificationItem, NotificationReadRequest
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


def test_public_notification_type_is_system_notice_only():
    values = {
        "notification_id": 1,
        "title": "운영 공지",
        "body": "안내",
        "is_read": False,
        "created_at": datetime.now(timezone.utc),
    }
    assert NotificationItem(type="system_notice", **values).type == "system_notice"
    with pytest.raises(ValidationError):
        NotificationItem(type="topic_new_post", **values)


@pytest.fixture
def mixed_notification_db(monkeypatch):
    """Execute the service's real SQL against mixed notice types and owners."""
    engine = create_engine("sqlite://")
    Notification.__table__.create(engine)
    with engine.begin() as connection:
        # List queries only need these Content columns; the full content model is PostgreSQL-only.
        connection.exec_driver_sql("CREATE TABLE content (content_id INTEGER PRIMARY KEY, image_url TEXT)")
    with Session(engine, expire_on_commit=False) as session:
        now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        for notification_id, user_id, kind, is_read, minutes in [
            (1, 1, "system_notice", False, 0),
            (2, 1, "system_notice", True, 1),
            (3, 1, "system_notice", False, 2),
            (4, 1, "topic_new_post", False, 3),
            (5, 2, "system_notice", False, 4),
            (6, 1, "follow", False, 5),
            (7, 1, "system_notice", False, 2),
        ]:
            session.add(Notification(
                notification_id=notification_id,
                user_id=user_id,
                notification_type=kind,
                title=f"알림 {notification_id}",
                body="안내",
                created_at=now + timedelta(minutes=minutes),
                read_at=now if is_read else None,
            ))
        session.commit()
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=session.execute),
            scalar=AsyncMock(side_effect=session.scalar),
            commit=AsyncMock(side_effect=session.commit),
        )
        monkeypatch.setattr(notification_service, "_ensure_welcome_notice", AsyncMock())
        yield db, session
    engine.dispose()


@pytest.mark.asyncio
async def test_list_paginates_only_current_users_system_notices(mixed_notification_db):
    db, _ = mixed_notification_db
    first = await notification_service.list_notifications(
        db, 1, unread_only=False, cursor=None, limit=2
    )
    assert [item.notification_id for item in first.items] == [7, 3]
    assert all(item.type == "system_notice" for item in first.items)
    assert first.next_cursor is not None
    second = await notification_service.list_notifications(
        db, 1, unread_only=False, cursor=first.next_cursor, limit=2
    )
    assert [item.notification_id for item in second.items] == [2, 1]
    assert second.next_cursor is None


@pytest.mark.asyncio
async def test_unread_list_and_count_exclude_legacy_and_other_users_notices(mixed_notification_db):
    db, session = mixed_notification_db
    unread = await notification_service.list_notifications(
        db, 1, unread_only=True, cursor=None, limit=20
    )
    count = await notification_service.unread_count(db, 1)
    assert [item.notification_id for item in unread.items] == [7, 3, 1]
    assert count.unread_count == len(unread.items) == 3
    await notification_service.mark_read(db, 1, 3)
    assert (await notification_service.unread_count(db, 1)).unread_count == 2
    # Filtering never removes or marks legacy rows as read.
    stored = session.scalars(select(Notification)).all()
    assert len(stored) == 7
    assert session.get(Notification, 4).read_at is None
    assert session.get(Notification, 6).read_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("notification_id", [4, 5, 6, 999])
async def test_read_rejects_legacy_other_users_and_missing_notices(mixed_notification_db, notification_id):
    db, session = mixed_notification_db
    with pytest.raises(ApiError) as error:
        await notification_service.mark_read(db, 1, notification_id)
    assert error.value.code == "NOTIFICATION_NOT_FOUND"
    db.commit.assert_not_awaited()
    existing = session.get(Notification, notification_id)
    if existing is not None:
        assert existing.read_at is None


@pytest.mark.asyncio
async def test_welcome_generation_adds_only_a_system_notice():
    joined_at = datetime(2026, 10, 20, tzinfo=timezone.utc)
    db = SimpleNamespace(
        execute=AsyncMock(),
        scalar=AsyncMock(return_value=None),
        get=AsyncMock(return_value=SimpleNamespace(onboarding_completed_at=joined_at)),
        add=Mock(),
        commit=AsyncMock(),
    )
    await notification_service._ensure_welcome_notice(db, 1)
    db.add.assert_called_once()
    notice = db.add.call_args.args[0]
    assert notice.notification_type == "system_notice"
    assert notice.created_at == joined_at
    assert notice.content_id is None
    assert "새 글" not in notice.body
    # The sole SQL execution is the advisory lock, with no post-notification insertion.
    db.execute.assert_awaited_once()
    db.commit.assert_awaited_once()


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
