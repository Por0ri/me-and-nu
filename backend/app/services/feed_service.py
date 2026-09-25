from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.repositories import catalog_repository as catalog_repo
from app.repositories import content_repository as content_repo
from app.schemas.feed import (
    ChannelInfo,
    ContentDetail,
    DetailReaction,
    FeedCard,
    FeedResponse,
    FeedSection,
    MyReaction,
    ShareInfo,
)
from app.services.cursor import decode_cursor, encode_cursor


def preference_value(value: str | None) -> str | None:
    return {"O": "positive", "X": "negative"}.get(value)


def content_notices(content) -> list[dict[str, str]]:
    notices = []
    if content.judgment_status == "needs_review":
        notices.append({"code": "REVIEW_REQUIRED", "message": "검토 필요"})
    basis = content.ai_judgment_basis if isinstance(content.ai_judgment_basis, dict) else {}
    if basis.get("preview") is True:
        notices.append({"code": "DEV_PREVIEW", "message": "개발용 미리보기"})
    return notices


async def require_active_tap(db: AsyncSession, user_id: int, topic_id: int):
    topic = await catalog_repo.get_active_topic(db, topic_id)
    tap = await catalog_repo.get_tap(db, user_id, topic_id)
    if topic is None or tap is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "활성 Topic을 찾을 수 없습니다.")
    return tap


async def _source_or_none(db: AsyncSession, content_id: int):
    result = await content_repo.get_primary_source(db, content_id)
    if result is None:
        return None
    source, site = result
    name = site.media_name if site else (source.source_title or "출처 미상")
    return source.source_url, name


async def _state(db: AsyncSession, user_id: int, tap_id: int, content_id: int):
    reaction = await content_repo.get_reaction(db, user_id, tap_id, content_id)
    saved = await content_repo.get_saved_item(db, user_id, tap_id, content_id)
    if saved is not None and saved.status == "deleted":
        saved = None
    return reaction, saved


async def get_feed(
    db: AsyncSession,
    user_id: int,
    topic_id: int,
    *,
    subtopic_id: int | None,
    cursor: str | None,
    limit: int,
) -> FeedResponse:
    tap = await require_active_tap(db, user_id, topic_id)
    selected_subtopic = None
    if subtopic_id is not None:
        selected_subtopic = await catalog_repo.get_subtopic(db, subtopic_id)
        if selected_subtopic is None or selected_subtopic.topic_id != topic_id:
            raise ApiError(422, "SUBTOPIC_TOPIC_MISMATCH", "Subtopic이 Topic에 속하지 않습니다.")
    context = {"v": 1, "userId": user_id, "topicId": topic_id, "subtopicId": subtopic_id}
    after = None
    if cursor is not None:
        payload = decode_cursor(cursor, expected=context)
        try:
            stamp = datetime.fromisoformat(payload["publishedAt"])
            content_id = payload["contentId"]
            if stamp.tzinfo is None or not isinstance(content_id, int) or isinstance(content_id, bool) or content_id <= 0:
                raise ValueError("bad position")
            after = (stamp, content_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError(422, "INVALID_CURSOR", "유효하지 않은 cursor입니다.") from exc
    rows = await content_repo.list_public_contents(
        db, topic_id, subtopic_id=subtopic_id, after=after, limit=limit + 1
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    cards: list[FeedCard] = []
    for content in page:
        source = await _source_or_none(db, content.content_id)
        if source is None:
            continue
        reaction, saved = await _state(db, user_id, tap.tap_id, content.content_id)
        cards.append(
            FeedCard(
                id=content.content_id,
                topic_id=topic_id,
                title=content.title,
                production_type=content.production_type,
                summary=content.summary,
                image_url=content.image_url,
                source_name=source[1],
                source_url=source[0],
                published_at=content.published_at,
                saved=saved is not None,
                saved_item_id=saved.saved_item_id if saved else None,
                is_promotional=content.is_sponsored,
                notices=content_notices(content),
                my_reaction=MyReaction(
                    liked=bool(reaction and reaction.is_liked),
                    preference=preference_value(reaction.ox_feedback) if reaction else None,
                ),
            )
        )
    next_cursor = None
    if has_more and page:
        last = page[-1]
        stamp = last.published_at or last.created_at
        next_cursor = encode_cursor(
            {**context, "publishedAt": stamp.isoformat(), "contentId": last.content_id}
        )
    sections = []
    if cards:
        sections = [
            FeedSection(
                id=f"subtopic:{subtopic_id}" if selected_subtopic else "recommended",
                type="subtopic" if selected_subtopic else "recommended",
                title=selected_subtopic.subtopic_name if selected_subtopic else "추천 콘텐츠",
                subtopic_id=subtopic_id,
                contents=cards,
            )
        ]
    empty_reason = None
    if not cards and cursor is None:
        empty_reason = "NO_CONTENT_IN_SUBTOPIC" if subtopic_id else "NO_CONTENT_IN_TOPIC"
    return FeedResponse(
        selected_topic_id=topic_id,
        sections=sections,
        next_cursor=next_cursor,
        empty_reason=empty_reason,
    )


async def get_content_detail(
    db: AsyncSession, user_id: int, topic_id: int, content_id: int
) -> ContentDetail:
    tap = await require_active_tap(db, user_id, topic_id)
    content = await content_repo.get_public_content(db, content_id, topic_id)
    if content is None:
        raise ApiError(404, "CONTENT_NOT_FOUND", "콘텐츠를 찾을 수 없습니다.")
    source = await _source_or_none(db, content_id)
    if source is None:
        raise ApiError(404, "CONTENT_NOT_FOUND", "콘텐츠 출처를 찾을 수 없습니다.")
    reaction, saved = await _state(db, user_id, tap.tap_id, content_id)
    channel = None
    if content.creator_channel_id is not None:
        row = await content_repo.get_channel(db, content.creator_channel_id)
        if row and row.channel_status == "active" and row.topic_id == topic_id:
            channel = ChannelInfo(
                channel_id=row.creator_channel_id,
                name=row.channel_name,
                topic_id=row.topic_id,
            )
    is_original_ai = content.production_type == "ai" and content.source_draft_id is not None
    return ContentDetail(
        content_id=content_id,
        topic_id=topic_id,
        title=content.title,
        production_type=content.production_type,
        source_url=source[0],
        publisher=source[1],
        published_at=content.published_at,
        display_mode="full_body" if is_original_ai else "link_excerpt",
        excerpt=content.summary,
        body=content.body if is_original_ai else None,
        content_type=content.content_type,
        subtopic_ids=await content_repo.list_content_subtopic_ids(db, content_id),
        notices=content_notices(content),
        is_promotional=content.is_sponsored,
        share=ShareInfo(url=source[0]),
        my_reaction=DetailReaction(
            liked=bool(reaction and reaction.is_liked),
            preference=preference_value(reaction.ox_feedback) if reaction else None,
            bookmarked=saved is not None,
        ),
        channel=channel,
    )
