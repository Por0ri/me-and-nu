"""Due publication against real migrated PostgreSQL, including races and retry."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.jobs import publish_scheduled_notices as job
from app.core.exceptions import ApiError
from app.models import AdminAccount, AdminActionLog, Notice, Notification, UserAccount
from app.schemas.admin_notice import AdminNoticeActionRequest, AdminNoticeScope
from app.services.admin_notice_service import apply_notice_action
from app.services.admin_notice_permission_provider import DatabaseAdminNoticePermissionProvider
from test_admin_postgres_integration import STAMP, admin_pg, run_async, sessions  # noqa: F401


def seed(pg):
    pg.migrate()
    with Session(pg.engine) as db, db.begin():
        for uid, status, onboarded in ((1, "active", True), (2, "warned", True), (3, "suspended", True), (4, "active", False)):
            db.add(UserAccount(user_id=uid, nickname=f"notice-synthetic-{uid}", auth_provider="kakao",
                provider_user_id=f"notice-synthetic-{uid}", account_status=status,
                onboarding_completed_at=STAMP if onboarded else None))
        db.flush()
        db.add(AdminAccount(admin_account_id=1, user_id=1, role="operator")); db.flush()
        for number, status, due in ((1, "scheduled", STAMP), (2, "scheduled", STAMP + timedelta(days=1)), (3, "draft", None)):
            db.add(Notice(notice_id=number, title="합성 공지", body="상세 본문" * 200, status=status,
                scheduled_at=due, created_by_admin_id=1, updated_by_admin_id=1,
                created_at=STAMP - timedelta(days=1), updated_at=STAMP - timedelta(days=1)))


def counts(pg):
    with pg.engine.connect() as db:
        return (db.scalar(text("SELECT count(*) FROM notice WHERE status='published'")),
                db.scalar(text("SELECT count(*) FROM notification WHERE notice_id IS NOT NULL")),
                db.scalar(text("SELECT count(*) FROM admin_action_log WHERE action_type='notice.dispatch'")))


async def run(pg, **kwargs):
    return await job.run(expected_database=pg.url.database,
                         database_url=pg.url.render_as_string(hide_password=False), now=STAMP, **kwargs)


def test_preview_due_boundary_and_safe_repeat(admin_pg):
    seed(admin_pg)
    assert run_async(run(admin_pg, apply=False)) == {"mode": "preview", "due": 1, "published": 0, "deliveries": 0}
    assert counts(admin_pg) == (0, 0, 0)
    assert run_async(run(admin_pg, apply=True))["published"] == 1
    assert counts(admin_pg) == (1, 2, 1)
    assert run_async(run(admin_pg, apply=True))["published"] == 0
    assert counts(admin_pg) == (1, 2, 1)
    with Session(admin_pg.engine) as db:
        row = db.get(Notice, 1)
        assert row.scheduled_at == STAMP and row.published_at >= STAMP
        audit = db.scalars(select(AdminActionLog)).one()
        assert audit.after_value["execution"] == "scheduled_job"
        assert row.body not in str(audit.after_value) + audit.rationale


def test_overlapping_jobs_deliver_and_audit_once(admin_pg):
    seed(admin_pg)
    async def race():
        return await asyncio.gather(run(admin_pg, apply=True), run(admin_pg, apply=True))
    reports = run_async(race())
    assert sum(report["published"] for report in reports) == 1
    assert counts(admin_pg) == (1, 2, 1)


def test_audit_failure_rolls_back_publication_and_retry_catches_up(admin_pg, monkeypatch):
    seed(admin_pg)
    original = job.append_audit
    async def fail(*args, **kwargs):
        raise RuntimeError("simulated audit outage")
    monkeypatch.setattr(job, "append_audit", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        run_async(run(admin_pg, apply=True))
    assert counts(admin_pg) == (0, 0, 0)
    monkeypatch.setattr(job, "append_audit", original)
    receipt = run_async(job.run(expected_database=admin_pg.url.database, apply=True,
        database_url=admin_pg.url.render_as_string(hide_password=False), now=STAMP + timedelta(days=2)))
    assert receipt["published"] == 2 and counts(admin_pg) == (2, 4, 2)
    with Session(admin_pg.engine) as db:
        assert db.get(Notice, 3).status == "draft"


def test_cancel_and_dispatch_race_has_one_consistent_outcome(admin_pg):
    seed(admin_pg)
    async def race():
        async with sessions(admin_pg) as factory:
            async def cancel():
                async with factory() as db:
                    return await apply_notice_action(db, 1, AdminNoticeActionRequest(
                        request_id=uuid4(), action="cancel_schedule", expected_status="scheduled",
                        expected_updated_at=STAMP - timedelta(days=1)), user_id=1,
                        permissions=DatabaseAdminNoticePermissionProvider(),
                        expected_scope=AdminNoticeScope(actor_user_id="1", admin_account_id="1"), now=STAMP)
            return await asyncio.gather(cancel(), run(admin_pg, apply=True), return_exceptions=True)
    cancel, dispatch = run_async(race())
    assert not isinstance(dispatch, Exception)
    with Session(admin_pg.engine) as db:
        record = db.get(Notice, 1)
        if record.status == "draft":
            assert not isinstance(cancel, Exception)
            assert dispatch["published"] == 0
            assert counts(admin_pg) == (0, 0, 0)
            assert db.scalar(select(func.count()).select_from(AdminActionLog).where(
                AdminActionLog.action_type == "notice.cancel_schedule")) == 1
        else:
            assert record.status == "published"
            assert isinstance(cancel, ApiError) and cancel.status_code == 409
            assert dispatch["published"] == 1
            assert counts(admin_pg) == (1, 2, 1)
