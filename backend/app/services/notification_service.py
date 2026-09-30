"""알림함 (API-107 목록, API-108 미확인 개수, API-109 읽음 처리).

지금은 글 발행 시점에 알림을 뿌리는 배치(Celery)가 없어서, 알림함을 열 때
빠진 알림을 먼저 채운다(_sync). 내 활성 분야에 최근 30일 안에 올라온 글과
가입 환영 공지가 대상이다. 배치가 생기면 _sync만 그쪽으로 옮기면 된다.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import Content, Notification, Tap, UserAccount
from app.repositories import content_repository as content_repo
from app.schemas.notification import (
    NotificationItem,
    NotificationReadResponse,
    NotificationsResponse,
    UnreadCountResponse,
)
from app.services.cursor import decode_cursor, encode_cursor

RECENT_DAYS = 30
NOTIFICATION_LOCK = 7_000_000_000  # 다른 advisory lock과 겹치지 않게 띄운 값
PER_TOPIC = 10
WELCOME_TITLE = "환영 공지"
WELCOME_BODY = "me;nu에 오신 걸 환영해요. 고른 분야의 새 글과 공지를 여기서 알려드릴게요."
# 피그마 알림 문구(noti page_01)
NEW_POST_BODY = "{author} 님이 새 게시글을 업로드했습니다. 지금 바로 확인해보세요."


async def _author_name(db: AsyncSession, content: Content) -> str:
    """AI가 쓴 글은 me;nu, 그 밖은 출처 매체 이름."""
    if content.production_type == "ai":
        return "me;nu"
    source = await content_repo.get_primary_source(db, content.content_id)
    if source is None:
        return "me;nu"
    row, site = source
    return (site.media_name if site else row.source_title) or "me;nu"


async def _sync(db: AsyncSession, user_id: int) -> None:
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

    since = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    topic_ids = (
        await db.scalars(select(Tap.topic_id).where(Tap.user_id == user_id, Tap.deleted_at.is_(None)))
    ).all()
    for topic_id in topic_ids:
        contents = await content_repo.list_public_contents(
            db, topic_id, subtopic_id=None, after=None, limit=PER_TOPIC
        )
        rows = [
            {
                "user_id": user_id,
                "notification_type": "topic_new_post",
                "title": content.title[:100],
                "body": NEW_POST_BODY.format(author=await _author_name(db, content)),
                "topic_id": topic_id,
                "content_id": content.content_id,
                "created_at": content.published_at or content.created_at,
            }
            for content in contents
            if (content.published_at or content.created_at) >= since
        ]
        if rows:
            await db.execute(
                insert(Notification)
                .values(rows)
                .on_conflict_do_nothing(
                    index_elements=["user_id", "notification_type", "content_id"],
                    index_where=Notification.content_id.is_not(None),
                )
            )
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
        await _sync(db, user_id)

    statement = (
        select(Notification, Content.image_url)
        .outerjoin(Content, Content.content_id == Notification.content_id)
        .where(Notification.user_id == user_id)
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
    await _sync(db, user_id)
    count = await db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user_id, Notification.read_at.is_(None)
        )
    )
    return UnreadCountResponse(unread_count=count or 0)


async def mark_read(db: AsyncSession, user_id: int, notification_id: int) -> NotificationReadResponse:
    notification = await db.scalar(
        select(Notification).where(
            Notification.notification_id == notification_id, Notification.user_id == user_id
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
