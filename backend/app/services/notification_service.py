"""알림함 (API-107 목록, API-108 미확인 개수, API-109 읽음 처리)과 알림 만들기.

알림함은 운영공지(system_notice)와 새 글 알림(topic_new_post)을 보여 준다. 그 밖의 유형은
저장된 상태로 보존하되 목록, 미확인 개수, 읽음 처리에서는 제외한다.

알림은 서버 명령(app.jobs.create_notifications)으로만 만든다. 만드는 함수는 commit 하지 않고 부른 쪽이 한다.
- 새 글 알림: 지난 24시간 안에 공개된 글의 대표 작품(연결 순서 1)을 온보딩 Q1에서 고른 회원에게 보낸다.
  하루 최대 3개, 같은 작품은 하루 1개, 최신 글 먼저. 알림을 끈 회원, 이미 열어 본 글, '그만 볼래요' 글은 뺀다.
- 운영공지: 알림 설정과 상관없이 활동 중인 회원 전부(또는 지정한 회원)에게 보낸다.
  같은 제목·본문 공지가 이미 있는 회원은 건너뛰어서 명령을 두 번 돌려도 한 번만 생긴다.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import and_, exists, func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import (
    Content,
    ContentEntityLink,
    ContentReaction,
    Entity,
    Notification,
    NotificationSetting,
    Tap,
    TapEntity,
    UserAccount,
    UserEvent,
)
from app.repositories.carousel_repository import public_content_condition
from app.schemas.notification import (
    NotificationItem,
    NotificationReadResponse,
    NotificationsResponse,
    UnreadCountResponse,
)
from app.services.cursor import decode_cursor, encode_cursor

NOTIFICATION_LOCK = 7_000_000_000  # 다른 advisory lock과 겹치지 않게 띄운 값
CREATE_LOCK = NOTIFICATION_LOCK - 1  # 알림 만들기 명령끼리 동시에 돌지 않게 (회원별 잠금과 겹치지 않음)
WELCOME_TITLE = "환영 공지"
WELCOME_BODY = "me;nu에 오신 걸 환영해요. 서비스 운영에 관한 공지를 여기서 알려드릴게요."

NOTICE = "system_notice"
NEW_POST = "topic_new_post"
INBOX_TYPES = (NOTICE, NEW_POST)
NEW_POST_WINDOW = timedelta(hours=24)
DAILY_NEW_POSTS = 3
ACTIVE_STATUSES = ("active", "warned")
TITLE_MAX, BODY_MAX = 100, 500
KST = timezone(timedelta(hours=9))


async def _ensure_welcome_notice(db: AsyncSession, user_id: int) -> None:
    # 홈의 미확인 개수와 알림 목록이 동시에 불러도 한 번만 채우도록 사용자 단위로 잠근다.
    await db.execute(text("select pg_advisory_xact_lock(:key)"), {"key": NOTIFICATION_LOCK + user_id})
    # 운영공지가 먼저 와 있어도 환영 공지는 따로 한 번 생기도록 제목으로 찾는다.
    has_welcome = await db.scalar(
        select(Notification.notification_id).where(
            Notification.user_id == user_id,
            Notification.notification_type == NOTICE,
            Notification.title == WELCOME_TITLE,
        ).limit(1)
    )
    if has_welcome is None:
        user = await db.get(UserAccount, user_id)
        db.add(Notification(
            user_id=user_id,
            notification_type=NOTICE,
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
            Notification.notification_type.in_(INBOX_TYPES),
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
            Notification.notification_type.in_(INBOX_TYPES),
            Notification.read_at.is_(None),
        )
    )
    return UnreadCountResponse(unread_count=count or 0)


async def mark_read(db: AsyncSession, user_id: int, notification_id: int) -> NotificationReadResponse:
    notification = await db.scalar(
        select(Notification).where(
            Notification.notification_id == notification_id,
            Notification.user_id == user_id,
            Notification.notification_type.in_(INBOX_TYPES),
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


# ── 알림 만들기 (서버 명령 전용) ──────────────────────────────────────────


@dataclass(frozen=True)
class NewPost:
    """한 회원에게 보낼 수 있는 새 글 하나."""

    user_id: int
    content_id: int
    topic_id: int
    work_id: int
    work_name: str
    title: str
    published_at: datetime


def new_post_title(work_name: str) -> str:
    """‘작품 이름’ 새 글. DB 제목 100자를 넘으면 작품 이름을 줄인다."""
    name = " ".join(work_name.split())
    room = TITLE_MAX - len("‘’ 새 글")
    if len(name) > room:
        name = name[: room - 1] + "…"
    return f"‘{name}’ 새 글"


def pick_new_posts(
    posts: Iterable[NewPost], *, sent_today: int = 0, works_today: Iterable[int] = (),
    limit: int = DAILY_NEW_POSTS,
) -> list[NewPost]:
    """한 회원의 후보 글 → 이번에 보낼 글. 최신 글 먼저, 같은 작품은 하루 1개,
    오늘 이미 보낸 것까지 합쳐 하루 limit 개."""
    picked: list[NewPost] = []
    works = set(works_today)
    for post in sorted(posts, key=lambda p: (p.published_at, p.content_id), reverse=True):
        if sent_today + len(picked) >= limit:
            break
        if post.work_id in works:
            continue
        works.add(post.work_id)
        picked.append(post)
    return picked


def kst_day_start(moment: datetime) -> datetime:
    return datetime.combine(moment.astimezone(KST).date(), time.min, tzinfo=KST)


def _active_member():
    return and_(
        UserAccount.account_status.in_(ACTIVE_STATUSES),
        UserAccount.onboarding_completed_at.is_not(None),
        UserAccount.withdrawn_at.is_(None),
    )


async def _new_post_candidates(db: AsyncSession, since: datetime, until: datetime) -> list[NewPost]:
    opened = exists().where(
        UserEvent.tap_id == Tap.tap_id,
        UserEvent.content_id == Content.content_id,
        UserEvent.event_type == "open",
    )
    disliked = exists().where(
        ContentReaction.user_id == Tap.user_id,
        ContentReaction.tap_id == Tap.tap_id,
        ContentReaction.content_id == Content.content_id,
        ContentReaction.ox_feedback == "X",
    )
    notified = exists().where(
        Notification.user_id == Tap.user_id,
        Notification.notification_type == NEW_POST,
        Notification.content_id == Content.content_id,
    )
    rows = await db.execute(
        select(
            Tap.user_id, Content.content_id, Content.topic_id, Entity.entity_id, Entity.name,
            Content.title, Content.published_at,
        )
        .select_from(Content)
        .join(ContentEntityLink, and_(
            ContentEntityLink.content_id == Content.content_id, ContentEntityLink.sort_order == 1,
        ))
        .join(Entity, Entity.entity_id == ContentEntityLink.entity_id)
        .join(TapEntity, and_(TapEntity.entity_id == Entity.entity_id, TapEntity.topic_id == Content.topic_id))
        .join(Tap, and_(
            Tap.tap_id == TapEntity.tap_id, Tap.topic_id == Content.topic_id, Tap.deleted_at.is_(None),
        ))
        .join(UserAccount, and_(UserAccount.user_id == Tap.user_id, _active_member()))
        .join(NotificationSetting, and_(
            NotificationSetting.user_id == Tap.user_id, NotificationSetting.in_app_enabled.is_(True),
        ))
        .where(
            public_content_condition(),
            Content.published_at > since,
            Content.published_at <= until,
            ~opened,
            ~disliked,
            ~notified,
        )
    )
    return [NewPost(*row) for row in rows.all()]


async def _sent_today(db: AsyncSession, user_ids: list[int], day_start: datetime) -> dict[int, list[int | None]]:
    """회원별로 오늘(한국 시간) 이미 보낸 새 글 알림의 대표 작품 목록."""
    if not user_ids:
        return {}
    rows = await db.execute(
        select(Notification.user_id, ContentEntityLink.entity_id)
        .outerjoin(ContentEntityLink, and_(
            ContentEntityLink.content_id == Notification.content_id, ContentEntityLink.sort_order == 1,
        ))
        .where(
            Notification.user_id.in_(user_ids),
            Notification.notification_type == NEW_POST,
            Notification.created_at >= day_start,
        )
    )
    sent: dict[int, list[int | None]] = {}
    for user_id, work_id in rows.all():
        sent.setdefault(user_id, []).append(work_id)
    return sent


async def create_new_post_notifications(
    db: AsyncSession, *, apply: bool, now: datetime | None = None, window: timedelta = NEW_POST_WINDOW,
) -> dict:
    """지난 window 동안 공개된 글로 새 글 알림을 만든다. apply=False 면 개수만 센다."""
    now = now or datetime.now(timezone.utc)
    since = now - window
    await db.execute(text("select pg_advisory_xact_lock(:key)"), {"key": CREATE_LOCK})
    candidates = await _new_post_candidates(db, since, now)
    by_user: dict[int, list[NewPost]] = {}
    for post in candidates:
        by_user.setdefault(post.user_id, []).append(post)
    sent = await _sent_today(db, list(by_user), kst_day_start(now))
    picked: list[NewPost] = []
    for user_id, posts in by_user.items():
        works = sent.get(user_id, [])
        picked += pick_new_posts(
            posts, sent_today=len(works), works_today={work for work in works if work is not None},
        )
    created = 0
    if apply and picked:
        result = await db.execute(
            pg_insert(Notification)
            .values([
                {
                    "user_id": post.user_id,
                    "notification_type": NEW_POST,
                    "title": new_post_title(post.work_name),
                    "body": " ".join(post.title.split())[:BODY_MAX],
                    "topic_id": post.topic_id,
                    "content_id": post.content_id,
                    "created_at": now,
                }
                for post in picked
            ])
            .on_conflict_do_nothing(
                index_elements=["user_id", "notification_type", "content_id"],
                index_where=Notification.content_id.is_not(None),
            )
            .returning(Notification.notification_id)
        )
        created = len(result.all())
    return {
        "mode": "apply" if apply else "preview",
        "since": since.isoformat(),
        "until": now.isoformat(),
        "posts": len({post.content_id for post in candidates}),
        "members": len(by_user),
        "candidates": len(candidates),
        "selected": len(picked),
        "created": created,
    }


async def publish_notice(
    db: AsyncSession, *, title: str, body: str, apply: bool,
    user_ids: Iterable[int] | None = None, now: datetime | None = None,
) -> dict:
    """운영공지를 활동 중인 회원 전부(user_ids 가 있으면 그 회원만)에게 만든다. 알림 설정은 보지 않는다."""
    title, body = title.strip(), body.strip()
    if not title or len(title) > TITLE_MAX:
        raise ValueError(f"공지 제목은 1~{TITLE_MAX}자여야 합니다.")
    if not body or len(body) > BODY_MAX:
        raise ValueError(f"공지 본문은 1~{BODY_MAX}자여야 합니다.")
    if title == WELCOME_TITLE:
        raise ValueError(f"'{WELCOME_TITLE}' 제목은 자동 환영 공지 전용입니다.")
    now = now or datetime.now(timezone.utc)
    wanted = None if user_ids is None else set(user_ids)
    await db.execute(text("select pg_advisory_xact_lock(:key)"), {"key": CREATE_LOCK})
    members = select(UserAccount.user_id).where(_active_member())
    if wanted is not None:
        members = members.where(UserAccount.user_id.in_(wanted))
    targets = set((await db.execute(members)).scalars().all())
    already: set[int] = set()
    if targets:
        already = set((await db.execute(
            select(Notification.user_id).where(
                Notification.user_id.in_(targets),
                Notification.notification_type == NOTICE,
                Notification.title == title,
                Notification.body == body,
            )
        )).scalars().all())
    new = sorted(targets - already)
    if apply and new:
        await db.execute(pg_insert(Notification).values([
            {"user_id": user_id, "notification_type": NOTICE, "title": title, "body": body, "created_at": now}
            for user_id in new
        ]))
    return {
        "mode": "apply" if apply else "preview",
        "targets": len(targets),
        "already": len(already),
        "selected": len(new),
        "created": len(new) if apply else 0,
        "not_eligible_user_ids": sorted(wanted - targets) if wanted is not None else [],
    }
