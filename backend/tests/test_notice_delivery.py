"""Delivery eligibility, atomicity and full-body inbox compatibility."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models import Notice, Notification
from app.services import notification_service
from app.services.notice_delivery_service import publish_notice

NOW = datetime(2026, 11, 23, 3, tzinfo=timezone.utc)


class Adapter:
    def __init__(self, session): self.session = session
    def get_bind(self): return self.session.get_bind()
    async def execute(self, statement): return self.session.execute(statement)
    async def scalar(self, statement): return self.session.scalar(statement)
    async def flush(self): self.session.flush()
    async def commit(self): self.session.commit()


@pytest.fixture
def storage():
    engine = create_engine("sqlite://")
    with engine.begin() as db:
        db.exec_driver_sql("CREATE TABLE user_account (user_id INTEGER PRIMARY KEY, account_status TEXT, onboarding_completed_at TIMESTAMP, withdrawn_at TIMESTAMP)")
        db.exec_driver_sql("CREATE TABLE content (content_id INTEGER PRIMARY KEY, image_url TEXT)")
        db.exec_driver_sql("INSERT INTO user_account VALUES (1,'active','2026-01-01',NULL),(2,'warned','2026-01-01',NULL),(3,'active',NULL,NULL),(4,'suspended','2026-01-01',NULL),(5,'withdrawn','2026-01-01','2026-11-01')")
    Notice.__table__.create(engine)
    Notification.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db, Adapter(db)
    engine.dispose()


def notice(number, *, status="draft", scheduled_at=None):
    return Notice(notice_id=number, title="같은 제목", body="전문" * 1100,
        status=status, scheduled_at=scheduled_at, created_by_admin_id=1, updated_by_admin_id=1,
        created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(days=1))


@pytest.mark.asyncio
async def test_publication_delivers_once_per_eligible_member_and_notice(storage):
    db, adapter = storage
    first, second = notice(1), notice(2)
    db.add_all([first, second]); db.flush()
    assert await publish_notice(adapter, first, NOW) == 2
    assert await publish_notice(adapter, first, NOW) == 0
    assert await publish_notice(adapter, second, NOW) == 2
    db.commit()
    rows = db.scalars(select(Notification).order_by(Notification.notice_id, Notification.user_id)).all()
    assert [(row.notice_id, row.user_id) for row in rows] == [(1, 1), (1, 2), (2, 1), (2, 2)]
    assert all(len(row.body) == 500 for row in rows)
    assert first.status == second.status == "published" and first.published_at == NOW


@pytest.mark.asyncio
async def test_future_schedule_cannot_send_early_and_caller_rollback_removes_deliveries(storage):
    db, adapter = storage
    future = notice(1, status="scheduled", scheduled_at=NOW + timedelta(minutes=1))
    db.add(future); db.commit()
    with pytest.raises(ValueError, match="not arrived"):
        await publish_notice(adapter, future, NOW)
    assert not db.scalars(select(Notification)).all()
    assert future.status == "scheduled"
    await publish_notice(adapter, future, NOW + timedelta(minutes=1))
    db.rollback()
    db.expire_all()
    assert db.get(Notice, 1).status == "scheduled"
    assert not db.scalars(select(Notification)).all()


@pytest.mark.asyncio
async def test_owner_inbox_reads_full_published_body_and_keeps_legacy_notices(storage, monkeypatch):
    db, adapter = storage
    record = notice(1)
    db.add(record); db.flush()
    await publish_notice(adapter, record, NOW)
    db.add(Notification(user_id=1, notification_type="system_notice", title="기존 공지", body="기존 본문", created_at=NOW))
    db.commit()
    monkeypatch.setattr(notification_service, "_ensure_welcome_notice", AsyncMock())
    page = await notification_service.list_notifications(adapter, 1, unread_only=False, cursor=None, limit=20)
    assert sorted(item.body for item in page.items) == sorted([record.body, "기존 본문"])
    assert all(item.notification_id in {row.notification_id for row in db.scalars(
        select(Notification).where(Notification.user_id == 1))} for item in page.items)
    assert not (await notification_service.list_notifications(adapter, 3, unread_only=False, cursor=None, limit=20)).items
