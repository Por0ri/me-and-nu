"""Publish a locked notice and its in-app deliveries in the caller's transaction."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, literal, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import Notice, Notification, UserAccount


async def lock_notice_publications(db) -> None:
    """Before actor/grant/notice locks: avoid recipient-FK/actor-lock deadlocks.

    The negative key cannot overlap the positive notification/user advisory
    keys. Notice authoring volume is low, so serialize publication transitions
    instead of taking a lock on every recipient in application code.
    """
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(-7100000001)"))


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def publish_notice(db, notice: Notice, now: datetime) -> int:
    """Caller locks Notice FOR UPDATE, authorizes the action, audits, and commits.

    The stable notice ID is the deduplication key; equal text on two different
    notices does not suppress either notice. A failure rolls back both the
    publication and all deliveries when the caller rolls back its transaction.
    """
    if now.tzinfo is None:
        raise ValueError("An offset-aware publication time is required")
    if notice.status == "published":
        return 0
    if notice.status not in {"draft", "scheduled"}:
        raise ValueError("The notice cannot be published")
    if notice.status == "scheduled" and (notice.scheduled_at is None or utc(notice.scheduled_at) > utc(now)):
        raise ValueError("The scheduled publication time has not arrived")
    moment = max(utc(now), utc(notice.updated_at) + timedelta(microseconds=1))
    notice.status = "published"
    notice.published_at = moment
    notice.updated_at = moment
    await db.flush()

    targets = select(
        UserAccount.user_id,
        literal("system_notice"),
        literal(notice.title),
        literal(notice.body[:500]),
        literal(notice.notice_id),
        literal(moment),
    ).where(
        UserAccount.account_status.in_(("active", "warned")),
        UserAccount.onboarding_completed_at.is_not(None),
        UserAccount.withdrawn_at.is_(None),
    )
    dialect = db.get_bind().dialect.name
    factory = pg_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else insert
    statement = factory(Notification).from_select(
        ["user_id", "notification_type", "title", "body", "notice_id", "created_at"], targets,
    )
    if dialect in {"postgresql", "sqlite"}:
        statement = statement.on_conflict_do_nothing(
            index_elements=["user_id", "notice_id"], index_where=Notification.notice_id.is_not(None),
        )
    else:
        raise ValueError("Notice delivery requires PostgreSQL or the isolated SQLite test adapter")
    result = await db.execute(statement.returning(Notification.notification_id))
    return len(result.all())
