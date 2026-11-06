"""마이페이지 '내 활동': 좋아요한 콘텐츠 · 최근 본 콘텐츠 목록과 다음 페이지(cursor) 규칙."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import Content
from app.repositories import content_repository
from app.repositories import my_activity_repository as repo
from app.schemas.my_activity import LikedContentItem, LikedContentsResponse, RecentViewsResponse, ViewedContentItem
from app.services.cursor import decode_cursor, encode_cursor
from app.services.feed_service import require_active_tap

# 최근 본 목록 기간 (11/6 결정). 기록은 지우지 않고 목록에서만 뺍니다 (추천 모델은 계속 씀).
RECENT_VIEW_DAYS = 30


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment   # SQLite 시험 DB 는 시간대를 잃음


def counts_as_viewed(kind: str, value: dict) -> bool:
    """상세 화면을 연 것, 또는 홈 카드를 마지막 장까지 넘긴 것만 '본 글'로 칩니다."""
    if kind == "open":
        return True
    index, count = value.get("card_index"), value.get("card_count")
    return kind == "card_swipe" and bool(index) and bool(count) and index >= count


def _position(cursor: str | None, context: dict, key: str) -> tuple[datetime, int] | None:
    if cursor is None:
        return None
    payload = decode_cursor(cursor, expected=context)
    try:
        stamp = datetime.fromisoformat(payload[key])
        content_id = payload["contentId"]
        if (stamp.tzinfo is None or not isinstance(content_id, int)
                or isinstance(content_id, bool) or content_id <= 0):
            raise ValueError("bad position")
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError(422, "INVALID_CURSOR", "유효하지 않은 cursor입니다.") from exc
    return stamp, content_id


async def _card(db: AsyncSession, content: Content) -> dict | None:
    """저장 목록과 같은 카드 칸. 목록을 읽은 뒤 출처가 지워졌으면 출처를 지어내지 않고 뺍니다."""
    source = await content_repository.get_primary_source(db, content.content_id)
    if source is None:
        return None
    source_row, site = source
    return {
        "content_id": content.content_id,
        "title": content.title,
        "topic_id": content.topic_id,
        "summary": content.summary,
        "image_url": content.image_url,
        "source_name": site.media_name if site else source_row.source_title,
        "production_type": content.production_type,
        "published_at": content.published_at,
        "subtopic_names": await content_repository.list_content_subtopic_names(db, content.content_id),
    }


async def get_liked_contents(
    db: AsyncSession, user_id: int, *, topic_id: int | None, cursor: str | None, limit: int
) -> LikedContentsResponse:
    if topic_id is not None:
        await require_active_tap(db, user_id, topic_id)
    context = {"v": 1, "list": "likes", "userId": user_id, "topicId": topic_id}
    after = _position(cursor, context, "likedAt")
    rows = await repo.list_liked_contents(db, user_id, topic_id=topic_id, after=after, limit=limit + 1)
    page = rows[:limit]
    items = []
    for liked_at, content in page:
        card = await _card(db, content)
        if card is not None:
            items.append(LikedContentItem(**card, liked_at=_utc(liked_at)))
    next_cursor = None
    if len(rows) > limit and page:
        last_at, last = page[-1]
        next_cursor = encode_cursor({**context, "likedAt": _utc(last_at).isoformat(), "contentId": last.content_id})
    return LikedContentsResponse(items=items, next_cursor=next_cursor)


async def get_recent_views(
    db: AsyncSession,
    user_id: int,
    *,
    topic_id: int | None,
    cursor: str | None,
    limit: int,
    now: datetime | None = None,
) -> RecentViewsResponse:
    if topic_id is not None:
        await require_active_tap(db, user_id, topic_id)
    context = {"v": 1, "list": "recentViews", "userId": user_id, "topicId": topic_id}
    after = _position(cursor, context, "viewedAt")
    since = (now or datetime.now(timezone.utc)) - timedelta(days=RECENT_VIEW_DAYS)

    last_seen: dict[int, datetime] = {}   # 글마다 마지막으로 본 시각 (같은 글은 한 번만)
    for content_id, kind, value, at in await repo.list_view_events(db, user_id, topic_id=topic_id, since=since):
        if counts_as_viewed(kind, value or {}):
            at = _utc(at)
            if content_id not in last_seen or at > last_seen[content_id]:
                last_seen[content_id] = at
    ordered = sorted(((at, content_id) for content_id, at in last_seen.items()), reverse=True)
    if after is not None:
        ordered = [position for position in ordered if position < after]
    visible = await repo.get_visible_contents(db, [content_id for _, content_id in ordered])
    page = [(at, visible[content_id]) for at, content_id in ordered if content_id in visible][: limit + 1]

    items = []
    for viewed_at, content in page[:limit]:
        card = await _card(db, content)
        if card is not None:
            items.append(ViewedContentItem(**card, viewed_at=viewed_at))
    next_cursor = None
    if len(page) > limit:
        last_at, last = page[limit - 1]
        next_cursor = encode_cursor({**context, "viewedAt": last_at.isoformat(), "contentId": last.content_id})
    return RecentViewsResponse(items=items, next_cursor=next_cursor)
