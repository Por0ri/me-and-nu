"""API-041 saved list and cursor contract."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.repositories import bookmark_repository as repo
from app.repositories import content_repository
from app.schemas.bookmark import SavedBookmarkItem, SavedBookmarksResponse
from app.services.cursor import decode_cursor, encode_cursor
from app.services.feed_service import require_active_tap


async def get_saved_bookmarks(
    db: AsyncSession,
    user_id: int,
    *,
    topic_id: int | None,
    q: str | None,
    cursor: str | None,
    limit: int,
) -> SavedBookmarksResponse:
    if topic_id is not None:
        await require_active_tap(db, user_id, topic_id)

    normalized_q = q.strip() if q else None
    normalized_q = normalized_q or None
    context = {"v": 1, "userId": user_id, "topicId": topic_id, "q": normalized_q}
    after = None
    if cursor is not None:
        payload = decode_cursor(cursor, expected=context)
        try:
            stamp = datetime.fromisoformat(payload["savedAt"])
            saved_item_id = payload["savedItemId"]
            if (stamp.tzinfo is None or not isinstance(saved_item_id, int)
                    or isinstance(saved_item_id, bool) or saved_item_id <= 0):
                raise ValueError("bad position")
            after = stamp, saved_item_id
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError(422, "INVALID_CURSOR", "유효하지 않은 cursor입니다.") from exc

    rows = await repo.list_saved_bookmarks(
        db, user_id, topic_id=topic_id, q=normalized_q, after=after, limit=limit + 1
    )
    page = rows[:limit]
    items = []
    for saved, content in page:
        # The repository already requires a source; a missing source after a
        # concurrent deletion must not produce invented attribution.
        source = await content_repository.get_primary_source(db, content.content_id)
        if source is None:
            continue
        source_row, site = source
        items.append(SavedBookmarkItem(
            saved_item_id=saved.saved_item_id,
            content_id=content.content_id,
            title=content.title,
            topic_id=content.topic_id,
            tags=saved.saved_tags or [],
            resurface_enabled=saved.status == "kept",
            summary=content.summary,
            image_url=content.image_url,
            source_name=site.media_name if site else source_row.source_title,
        ))
    next_cursor = None
    if len(rows) > limit and page:
        last_saved = page[-1][0]
        next_cursor = encode_cursor({
            **context,
            "savedAt": last_saved.saved_at.isoformat(),
            "savedItemId": last_saved.saved_item_id,
        })
    return SavedBookmarksResponse(items=items, next_cursor=next_cursor)
