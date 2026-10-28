"""API-033: store a batch of activity events (log spec v0.2).

Events are append-only. A resent clientEventId is skipped, and an event whose
Topic, content or recommendation does not belong to the caller is rejected on
its own, so one stale card does not drop the rest of the batch.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.repositories import activity_repository as repo
from app.repositories import catalog_repository as catalog_repo
from app.schemas.activity import ActivityEventsRequest, ActivityEventsResponse


FUTURE_SKEW = timedelta(minutes=5)
MAX_AGE = timedelta(days=7)


def occurred_at(value: datetime, now: datetime) -> datetime:
    """Keep the device action time unless the device clock is clearly wrong."""
    return value if now - MAX_AGE <= value <= now + FUTURE_SKEW else now


async def _active_taps(db: AsyncSession, user_id: int, topic_ids: set[int]) -> dict[int, int | None]:
    taps = {}
    for topic_id in topic_ids:
        tap = None
        if await catalog_repo.get_active_topic(db, topic_id) is not None:
            tap = await catalog_repo.get_tap(db, user_id, topic_id)
        taps[topic_id] = tap.tap_id if tap is not None else None
    return taps


async def record_events(
    db: AsyncSession,
    user_id: int,
    request: ActivityEventsRequest,
    *,
    now: datetime | None = None,
) -> ActivityEventsResponse:
    now = now or datetime.now(timezone.utc)
    events, seen, duplicated = [], set(), 0
    for event in request.events:
        if event.client_event_id in seen:
            duplicated += 1
            continue
        seen.add(event.client_event_id)
        events.append(event)

    taps = await _active_taps(db, user_id, {event.topic_id for event in events})
    content_topics = await repo.visible_content_topics(db, {event.content_id for event in events})
    items = await repo.recommendation_item_owners(
        db, {event.recommendation_item_id for event in events if event.recommendation_item_id is not None}
    )

    rows, first_seen, rejected = [], {}, 0
    for event in events:
        tap_id = taps[event.topic_id]
        item_id = event.recommendation_item_id
        if (
            tap_id is None
            or content_topics.get(event.content_id) != event.topic_id
            or (item_id is not None and items.get(item_id) != (tap_id, event.content_id))
        ):
            rejected += 1
            continue
        at = occurred_at(event.occurred_at, now)
        rows.append({
            "client_event_id": event.client_event_id,
            "user_id": user_id,
            "tap_id": tap_id,
            "event_type": event.event_type,
            "content_id": event.content_id,
            "recommendation_item_id": item_id,
            "event_value": event.value.model_dump(exclude_none=True),
            "occurred_at": at,
            "received_at": now,
            "data_source": settings.event_data_source,
        })
        if event.event_type == "impression" and item_id is not None:
            first_seen[item_id] = min(at, first_seen.get(item_id, at))

    saved = await repo.insert_events(db, rows)
    await repo.mark_impressed(db, first_seen)
    await db.commit()
    return ActivityEventsResponse(
        saved=saved, duplicated=duplicated + len(rows) - saved, rejected=rejected
    )
