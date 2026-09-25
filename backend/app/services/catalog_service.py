from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.repositories import catalog_repository as repo
from app.schemas.catalog import (
    CreateMyTopicRequest,
    MySubtopicItem,
    MySubtopicsResponse,
    MyTopicItem,
    MyTopicResponse,
    MyTopicsResponse,
    SubtopicItem,
    SubtopicSubscriptionResponse,
    SubtopicsResponse,
    TopicItem,
    TopicsResponse,
)
from app.services.cursor import decode_cursor, encode_cursor


def topic_item(topic) -> TopicItem:
    return TopicItem(id=topic.topic_id, code=topic.topic_code, name=topic.topic_name)


def subtopic_item(subtopic) -> SubtopicItem:
    return SubtopicItem(
        subtopic_id=subtopic.subtopic_id,
        topic_id=subtopic.topic_id,
        name=subtopic.subtopic_name,
        parent_subtopic_id=subtopic.parent_subtopic_id,
    )


async def topics(db: AsyncSession, q: str | None) -> TopicsResponse:
    rows = await repo.list_active_topics(db, q)
    return TopicsResponse(topics=[topic_item(row) for row in rows])


async def subtopics(
    db: AsyncSession,
    topic_id: int,
    *,
    q: str | None,
    parent_subtopic_id: int | None,
    cursor: str | None,
    limit: int,
) -> SubtopicsResponse:
    if await repo.get_active_topic(db, topic_id) is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.")
    normalized_q = q.strip() if q else None
    context = {"v": 1, "topicId": topic_id, "q": normalized_q, "parent": parent_subtopic_id}
    after_id = None
    if cursor is not None:
        payload = decode_cursor(cursor, expected=context)
        after_id = payload.get("after")
        if not isinstance(after_id, int) or isinstance(after_id, bool) or after_id <= 0:
            raise ApiError(422, "INVALID_CURSOR", "유효하지 않은 cursor입니다.")
    rows = await repo.list_subtopics(
        db,
        topic_id,
        q=normalized_q,
        parent_subtopic_id=parent_subtopic_id,
        after_id=after_id,
        limit=limit + 1,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = encode_cursor({**context, "after": page[-1].subtopic_id}) if has_more else None
    return SubtopicsResponse(items=[subtopic_item(row) for row in page], next_cursor=next_cursor)


async def subtopic_detail(db: AsyncSession, subtopic_id: int) -> SubtopicItem:
    row = await repo.get_subtopic(db, subtopic_id)
    if row is None or await repo.get_active_topic(db, row.topic_id) is None:
        raise ApiError(404, "NOT_FOUND", "Subtopic을 찾을 수 없습니다.")
    return subtopic_item(row)


async def my_topic_item(db: AsyncSession, tap, topic) -> MyTopicItem:
    subscriptions = await repo.list_tap_subtopics(db, tap.tap_id)
    return MyTopicItem(
        id=topic.topic_id,
        code=topic.topic_code,
        name=topic.topic_name,
        subtopic_ids=[subtopic.subtopic_id for _, subtopic in subscriptions],
        status="deleted" if tap.deleted_at else "active",
        deleted_at=tap.deleted_at,
    )


async def my_topics(db: AsyncSession, user_id: int, include_deleted: bool) -> MyTopicsResponse:
    taps = await repo.list_taps(db, user_id, include_deleted)
    return MyTopicsResponse(
        topics=[await my_topic_item(db, tap, topic) for tap, topic in taps]
    )


async def create_my_topic(
    db: AsyncSession, user_id: int, request: CreateMyTopicRequest
) -> MyTopicResponse:
    topic = await repo.get_active_topic(db, request.topic_id)
    if topic is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.")
    previous = await repo.get_tap(db, user_id, request.topic_id, include_deleted=True)
    if previous is not None:
        code = "TOPIC_RESTORATION_REQUIRED" if previous.deleted_at else "TOPIC_ALREADY_ACTIVE"
        raise ApiError(409, code, "이미 추가되었거나 복구가 필요한 Topic입니다.")
    for subtopic_id in request.subtopic_ids:
        subtopic = await repo.get_subtopic(db, subtopic_id)
        if subtopic is None or subtopic.topic_id != request.topic_id:
            raise ApiError(422, "SUBTOPIC_TOPIC_MISMATCH", "Subtopic이 Topic에 속하지 않습니다.")
    try:
        tap = repo.add_tap(db, user_id, request.topic_id)
        await db.flush()
        for subtopic_id in request.subtopic_ids:
            repo.add_tap_subtopic(db, tap.tap_id, subtopic_id)
        await db.commit()
        await db.refresh(tap)
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "TOPIC_ALREADY_ACTIVE", "이미 활성화된 Topic입니다.") from exc
    return MyTopicResponse(topic=await my_topic_item(db, tap, topic))


async def my_subtopics(db: AsyncSession, user_id: int, topic_id: int) -> MySubtopicsResponse:
    tap = await repo.get_tap(db, user_id, topic_id)
    if tap is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "활성 Topic을 찾을 수 없습니다.")
    rows = await repo.list_tap_subtopics(db, tap.tap_id)
    return MySubtopicsResponse(
        items=[
            MySubtopicItem(
                subtopic_id=subtopic.subtopic_id,
                name=subtopic.subtopic_name,
                order=index,
                subscribed_at=association.subscribed_at,
            )
            for index, (association, subtopic) in enumerate(rows, start=1)
        ]
    )


async def set_subtopic_subscription(
    db: AsyncSession, user_id: int, topic_id: int, subtopic_id: int
) -> SubtopicSubscriptionResponse:
    tap = await repo.get_tap(db, user_id, topic_id)
    if tap is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "활성 Topic을 찾을 수 없습니다.")
    subtopic = await repo.get_subtopic(db, subtopic_id)
    if subtopic is None or subtopic.topic_id != topic_id:
        raise ApiError(422, "SUBTOPIC_TOPIC_MISMATCH", "Subtopic이 Topic에 속하지 않습니다.")
    if await repo.get_tap_subtopic(db, tap.tap_id, subtopic_id) is None:
        tap_id = tap.tap_id
        try:
            repo.add_tap_subtopic(db, tap_id, subtopic_id)
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            if await repo.get_tap_subtopic(db, tap_id, subtopic_id) is None:
                raise ApiError(409, "STATE_CONFLICT", "구독 저장 충돌이 발생했습니다.") from exc
    return SubtopicSubscriptionResponse(topic_id=topic_id, subtopic_id=subtopic_id)


async def clear_subtopic_subscription(
    db: AsyncSession, user_id: int, topic_id: int, subtopic_id: int
) -> None:
    tap = await repo.get_tap(db, user_id, topic_id)
    if tap is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "활성 Topic을 찾을 수 없습니다.")
    association = await repo.get_tap_subtopic(db, tap.tap_id, subtopic_id)
    if association is not None:
        await db.delete(association)
        await db.commit()
