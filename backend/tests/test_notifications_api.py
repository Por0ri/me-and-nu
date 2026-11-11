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


def test_public_notification_types_are_notice_new_post_and_recommended_only():
    values = {
        "notification_id": 1,
        "title": "운영 공지",
        "body": "안내",
        "is_read": False,
        "created_at": datetime.now(timezone.utc),
    }
    assert NotificationItem(type="system_notice", **values).type == "system_notice"
    assert NotificationItem(type="topic_new_post", **values).type == "topic_new_post"
    assert NotificationItem(type="recommended_post", **values).type == "recommended_post"
    for legacy in ("creator_new_post", "follow", "direct_message"):
        with pytest.raises(ValidationError):
            NotificationItem(type=legacy, **values)


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
async def test_list_paginates_only_current_users_notices_and_new_posts(mixed_notification_db):
    db, _ = mixed_notification_db
    first = await notification_service.list_notifications(
        db, 1, unread_only=False, cursor=None, limit=2
    )
    assert [item.notification_id for item in first.items] == [4, 7]
    assert [item.type for item in first.items] == ["topic_new_post", "system_notice"]
    assert first.next_cursor is not None
    second = await notification_service.list_notifications(
        db, 1, unread_only=False, cursor=first.next_cursor, limit=2
    )
    assert [item.notification_id for item in second.items] == [3, 2]
    third = await notification_service.list_notifications(
        db, 1, unread_only=False, cursor=second.next_cursor, limit=2
    )
    assert [item.notification_id for item in third.items] == [1]
    assert third.next_cursor is None


@pytest.mark.asyncio
async def test_unread_list_and_count_exclude_legacy_and_other_users_notices(mixed_notification_db):
    db, session = mixed_notification_db
    unread = await notification_service.list_notifications(
        db, 1, unread_only=True, cursor=None, limit=20
    )
    count = await notification_service.unread_count(db, 1)
    assert [item.notification_id for item in unread.items] == [4, 7, 3, 1]
    assert count.unread_count == len(unread.items) == 4
    await notification_service.mark_read(db, 1, 3)
    await notification_service.mark_read(db, 1, 4)
    assert (await notification_service.unread_count(db, 1)).unread_count == 2
    # Filtering never removes or marks legacy rows as read.
    stored = session.scalars(select(Notification)).all()
    assert len(stored) == 7
    assert session.get(Notification, 4).read_at is not None
    assert session.get(Notification, 6).read_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("notification_id", [5, 6, 999])
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
async def test_welcome_is_still_created_once_after_an_operational_notice():
    """운영공지가 환영 공지보다 먼저 와도 환영 공지는 한 번 생긴다."""
    engine = create_engine("sqlite://")
    Notification.__table__.create(engine)
    joined_at = datetime(2026, 10, 20, tzinfo=timezone.utc)
    with Session(engine, expire_on_commit=False) as session:
        session.add(Notification(notification_id=1, user_id=1, notification_type="system_notice",
                                 title="점검 안내", body="새벽 점검", created_at=joined_at + timedelta(days=1)))
        session.commit()

        async def execute(statement, params=None):
            if "pg_advisory_xact_lock" in str(statement):
                return None
            return session.execute(statement, params)

        def add(row):
            row.notification_id = row.notification_id or 2  # SQLite BIGINT 기본 키는 번호를 안 채운다
            session.add(row)

        db = SimpleNamespace(
            execute=execute,
            scalar=AsyncMock(side_effect=session.scalar),
            get=AsyncMock(return_value=SimpleNamespace(onboarding_completed_at=joined_at)),
            add=add,
            commit=AsyncMock(side_effect=session.commit),
        )
        await notification_service._ensure_welcome_notice(db, 1)
        await notification_service._ensure_welcome_notice(db, 1)
        titles = session.scalars(
            select(Notification.title).where(Notification.user_id == 1).order_by(Notification.created_at)
        ).all()
    engine.dispose()
    assert titles == [notification_service.WELCOME_TITLE, "점검 안내"]


def _post(content_id, work_id, hours_ago):
    return notification_service.NewPost(
        user_id=1, content_id=content_id, topic_id=1, work_id=work_id, work_name=f"작품 {work_id}",
        title=f"글 {content_id}", published_at=datetime(2026, 11, 10, 9, tzinfo=timezone.utc) - timedelta(hours=hours_ago),
    )


def test_new_posts_are_newest_first_one_per_work_and_three_a_day():
    posts = [_post(1, 10, 5), _post(2, 10, 1), _post(3, 20, 2), _post(4, 30, 3), _post(5, 40, 4)]
    assert [p.content_id for p in notification_service.pick_new_posts(posts)] == [2, 3, 4]


def test_new_posts_count_what_was_already_sent_today():
    posts = [_post(1, 10, 1), _post(2, 20, 2), _post(3, 30, 3)]
    picked = notification_service.pick_new_posts(posts, sent_today=2, works_today={10})
    assert [p.content_id for p in picked] == [2]
    assert notification_service.pick_new_posts(posts, sent_today=3) == []


def test_new_post_title_fits_the_title_column():
    assert notification_service.new_post_title(" 체인소   맨 ") == "‘체인소 맨’ 새 글"
    long = notification_service.new_post_title("가" * 300)
    assert len(long) == 100 and long.endswith("…’ 새 글")


@pytest.mark.asyncio
@pytest.mark.parametrize("title, body", [
    ("", "본문"), ("가" * 101, "본문"), ("점검", " "), ("점검", "가" * 501),
    (notification_service.WELCOME_TITLE, "본문"),
])
async def test_notice_rejects_bad_text_before_touching_the_database(title, body):
    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError):
        await notification_service.publish_notice(db, title=title, body=body, apply=True)
    db.execute.assert_not_awaited()


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
