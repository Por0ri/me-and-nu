"""알림함 (API-107 목록, API-108 미확인 개수, API-109 읽음 처리).

1차 알림함은 운영공지(system_notice)만 제공한다. 기존 다른 유형의 알림은
저장된 상태로 보존하되 목록, 미확인 개수, 읽음 처리에서는 제외한다.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import Content, Notification, UserAccount
from app.schemas.notification import (
    NotificationItem,
    NotificationReadResponse,
    NotificationsResponse,
    UnreadCountResponse,
)
from app.services.cursor import decode_cursor, encode_cursor

NOTIFICATION_LOCK = 7_000_000_000  # 다른 advisory lock과 겹치지 않게 띄운 값
WELCOME_TITLE = "환영 공지"
WELCOME_BODY = "me;nu에 오신 걸 환영해요. 서비스 운영에 관한 공지를 여기서 알려드릴게요."


async def _ensure_welcome_notice(db: AsyncSession, user_id: int) -> None:
    # 홈의 미확인 개수와 알림 목록이 동시에 불러도 한 번만 채우도록 사용자 단위로 잠근다.
    await db.execute(text("select pg_advisory_xact_lock(:key)"), {"key": NOTIFICATION_LOCK + user_id})
    has_welcome = await db.scalar(
        select(Notification.notification_id).where(
            Notification.user_id == user_id, Notification.notification_type == "system_notice"
        ).limit(1)
    )
    if has_welcome is None:
        user = await db.get(UserAccount, user_id)
        db.add(Notification(
            user_id=user_id,
            notification_type="system_notice",
            title=WELCOME_TITLE,
            body=WELCOME_BODY,
            created_at=(user.onboarding_completed_at if user else None) or datetime.now(timezone.utc),
        ))

    await db.commit()


async def list_notifications(
    db: AsyncSession, user_id: int, *, unread_only: bool, cursor: str | None, limit: int
) -> NotificationsResponse:
    context = {"v": 1, "userId": user_id, "unreadOnly": unread_only}
    after = None
    if cursor is not None:
        payload = decode_cursor(cursor, expected=context)
        try:
            after = (datetime.fromisoformat(payload["createdAt"]), int(payload["notificationId"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError(422, "INVALID_CURSOR", "유효하지 않은 cursor입니다.") from exc
    else:
        await _ensure_welcome_notice(db, user_id)

    statement = (
        select(Notification, Content.image_url)
        .outerjoin(Content, Content.content_id == Notification.content_id)
        .where(
            Notification.user_id == user_id,
            Notification.notification_type == "system_notice",
        )
    )
    if unread_only:
        statement = statement.where(Notification.read_at.is_(None))
    if after is not None:
        statement = statement.where(
            tuple_(Notification.created_at, Notification.notification_id) < after
        )
    rows = (
        await db.execute(
            statement.order_by(Notification.created_at.desc(), Notification.notification_id.desc())
            .limit(limit + 1)
        )
    ).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1][0]
        next_cursor = encode_cursor(
            {**context, "createdAt": last.created_at.isoformat(), "notificationId": last.notification_id}
        )
    return NotificationsResponse(
        items=[
            NotificationItem(
                notification_id=n.notification_id,
                type=n.notification_type,
                title=n.title,
                body=n.body,
                topic_id=n.topic_id,
                content_id=n.content_id,
                image_url=image_url,
                is_read=n.read_at is not None,
                read_at=n.read_at,
                created_at=n.created_at,
            )
            for n, image_url in page
        ],
        next_cursor=next_cursor,
    )


async def unread_count(db: AsyncSession, user_id: int) -> UnreadCountResponse:
    await _ensure_welcome_notice(db, user_id)
    count = await db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user_id,
            Notification.notification_type == "system_notice",
            Notification.read_at.is_(None),
        )
    )
    return UnreadCountResponse(unread_count=count or 0)


async def mark_read(db: AsyncSession, user_id: int, notification_id: int) -> NotificationReadResponse:
    notification = await db.scalar(
        select(Notification).where(
            Notification.notification_id == notification_id,
            Notification.user_id == user_id,
            Notification.notification_type == "system_notice",
        )
    )
    if notification is None:
        raise ApiError(404, "NOTIFICATION_NOT_FOUND", "알림을 찾을 수 없습니다.")
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        await db.commit()
    return NotificationReadResponse(
        notification_id=notification_id, is_read=True, read_at=notification.read_at
    )
