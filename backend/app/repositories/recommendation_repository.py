"""Reads for the home feed: the day's ranked list, the fallback candidates and visit signals."""

from datetime import date, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Content, ContentEntityLink, ContentReaction, ContentSource, RecommendationItem, RecommendationRound,
    TapPreferenceTag, UserAccount, UserEvent,
)
from app.repositories.carousel_repository import public_content_condition


def _visible(topic_id: int):
    """Same visibility as the newest-first feed: public, in the Topic, with a source."""
    return (
        Content.topic_id == topic_id,
        public_content_condition(),
        select(ContentSource.content_source_id)
        .where(ContentSource.content_id == Content.content_id)
        .exists(),
    )


async def latest_round(
    db: AsyncSession, tap_id: int, service_dates: list[date]
) -> RecommendationRound | None:
    """Newest round among the given service dates; the first date wins."""
    return await db.scalar(
        select(RecommendationRound)
        .where(RecommendationRound.tap_id == tap_id, RecommendationRound.service_date.in_(service_dates))
        .order_by(RecommendationRound.service_date.desc(), RecommendationRound.recommendation_round_id.desc())
        .limit(1)
    )


async def work_contents(db: AsyncSession, topic_id: int, entity_ids: list[int]) -> list[tuple[Content, int]]:
    """Visible contents linked to any of the works, newest first, each with its first linked work."""
    rows = (await db.execute(
        select(Content, ContentEntityLink.entity_id)
        .join(ContentEntityLink, ContentEntityLink.content_id == Content.content_id)
        .where(ContentEntityLink.entity_id.in_(entity_ids), *_visible(topic_id))
        .order_by(Content.published_at.desc().nulls_last(), Content.content_id.desc(), ContentEntityLink.sort_order)
    )).all()
    found, seen = [], set()
    for content, entity_id in rows:
        if content.content_id not in seen:
            seen.add(content.content_id)
            found.append((content, entity_id))
    return found


async def onboarding_completed_at(db: AsyncSession, user_id: int) -> datetime | None:
    return await db.scalar(select(UserAccount.onboarding_completed_at).where(UserAccount.user_id == user_id))


async def taste_edited_at(db: AsyncSession, tap_id: int) -> datetime | None:
    """Last save of the taste answers: a My Page edit re-inserts every chip row."""
    return await db.scalar(select(func.max(TapPreferenceTag.created_at)).where(TapPreferenceTag.tap_id == tap_id))


async def get_round(db: AsyncSession, round_id: int, tap_id: int) -> RecommendationRound | None:
    return await db.scalar(
        select(RecommendationRound).where(
            RecommendationRound.recommendation_round_id == round_id,
            RecommendationRound.tap_id == tap_id,
        )
    )


# 추천 순서를 앞에서 몇 위까지 읽나. 챗봇 · 홈 검색은 100위까지 그대로 (11/25 결정: 300위까지 보면 챗봇이 AI 에
# 넘기는 후보 순서가 바뀜). 피드만 feed_service.FEED_CARDS (300) 를 넘김
RANKED_LIMIT = 100


async def ranked_items(
    db: AsyncSession, round_id: int, topic_id: int, limit: int = RANKED_LIMIT,
) -> list[tuple[int, int, dict | None]]:
    """(content_id, recommendation_item_id, recommend_reason) of currently visible contents, by rank."""
    rows = await db.execute(
        select(
            RecommendationItem.content_id,
            RecommendationItem.recommendation_item_id,
            RecommendationItem.recommend_reason,
        )
        .join(Content, Content.content_id == RecommendationItem.content_id)
        .where(RecommendationItem.recommendation_round_id == round_id, *_visible(topic_id))
        .order_by(RecommendationItem.rank)
        .limit(limit)
    )
    return [(content_id, item_id, reason) for content_id, item_id, reason in rows.all()]


async def latest_content_ids(db: AsyncSession, topic_id: int, as_of: datetime, limit: int) -> list[int]:
    """Visible contents published by the snapshot time, newest first."""
    sort_time = func.coalesce(Content.published_at, Content.created_at)
    rows = await db.execute(
        select(Content.content_id)
        .where(*_visible(topic_id), sort_time <= as_of)
        .order_by(sort_time.desc(), Content.content_id.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def latest_typed_contents(
    db: AsyncSession, topic_id: int, as_of: datetime, per_type: int, *, exclude: set[int],
) -> list[tuple[int, str | None]]:
    """비상 목록 후보: 스냅샷 시각까지 공개된 보이는 글의 (content_id, content_type), 최신순. exclude 는 뺌.

    유형마다 새 글 per_type 개까지만 읽음. 묶음 (유형 여러 개) 마다 새 글 per_type 개는 그 유형들의 새 글
    per_type 개 안에 다 들어 있어서, 묶음을 돌아가며 per_type 장을 고르기엔 이걸로 충분함.
    """
    sort_time = func.coalesce(Content.published_at, Content.created_at)
    turn = func.row_number().over(
        partition_by=Content.content_type, order_by=(sort_time.desc(), Content.content_id.desc()))
    statement = select(
        Content.content_id, Content.content_type, sort_time.label("sort_time"), turn.label("turn"),
    ).where(*_visible(topic_id), sort_time <= as_of)
    if exclude:
        statement = statement.where(Content.content_id.not_in(sorted(exclude)))
    newest = statement.subquery()
    rows = await db.execute(
        select(newest.c.content_id, newest.c.content_type)
        .where(newest.c.turn <= per_type)
        .order_by(newest.c.sort_time.desc(), newest.c.content_id.desc())
    )
    return [(content_id, kind) for content_id, kind in rows.all()]


FULL_READ_SCROLL_PCT = 80   # 본문을 이만큼 이상 내렸으면 끝까지 읽은 글 (모델 content_v1.READ_FULL 0.8 과 같음)


async def fully_read_content_ids(db: AsyncSession, tap_id: int, since: datetime, as_of: datetime) -> set[int]:
    """Posts read to the end since `since`: the card deck swiped to its last card, or the body scrolled
    at least FULL_READ_SCROLL_PCT. A post opened but not finished stays a candidate (11/3 rule)."""
    value = UserEvent.event_value
    card_end = and_(
        UserEvent.event_type == "card_swipe",
        value["card_index"].as_integer() >= value["card_count"].as_integer(),
    )
    body_end = and_(UserEvent.event_type == "open", value["scroll_pct"].as_float() >= FULL_READ_SCROLL_PCT)
    rows = await db.execute(
        select(UserEvent.content_id).distinct().where(
            UserEvent.tap_id == tap_id, or_(card_end, body_end),
            UserEvent.occurred_at >= since, UserEvent.received_at <= as_of,
        )
    )
    return set(rows.scalars().all())


# 오늘 본 카드 = 그날 노출에서 카드가 반 이상 보인 시간 (visible_ms) 합이 이만큼 이상인 글. 화면은 1초부터 노출을
# 보내니까, 1초쯤 스쳐 간 카드는 맨 뒤로 안 보냄 (11/25 결정). visible_ms 가 없는 노출은 0 으로 침.
# 열었거나 카드를 넘긴 글은 시간과 상관없이 본 카드 (11/25 사용자 결정: 확실히 본 글)
SHOWN_VISIBLE_MS = 2000
SEEN_ACTIONS = ("open", "card_swipe")


async def shown_content_ids(db: AsyncSession, tap_id: int, since: datetime, as_of: datetime) -> set[int]:
    """Cards seen since `since`: impressions whose visible time adds up to SHOWN_VISIBLE_MS or more,
    and cards opened or swiped (certainly seen, whatever the time)."""
    visible = func.sum(UserEvent.event_value["visible_ms"].as_float())
    rows = await db.execute(
        select(UserEvent.content_id).where(
            UserEvent.tap_id == tap_id, UserEvent.event_type == "impression",
            UserEvent.received_at >= since, UserEvent.received_at <= as_of,
        ).group_by(UserEvent.content_id).having(visible >= SHOWN_VISIBLE_MS)
    )
    acted = await db.execute(
        select(UserEvent.content_id).distinct().where(
            UserEvent.tap_id == tap_id, UserEvent.event_type.in_(SEEN_ACTIONS), UserEvent.content_id.is_not(None),
            UserEvent.received_at >= since, UserEvent.received_at <= as_of,
        )
    )
    return set(rows.scalars().all()) | set(acted.scalars().all())


async def negative_content_ids(db: AsyncSession, user_id: int, tap_id: int) -> set[int]:
    """Current dislikes; updated_at also changes on likes and is not history."""
    rows = await db.execute(
        select(ContentReaction.content_id).where(
            ContentReaction.user_id == user_id, ContentReaction.tap_id == tap_id,
            ContentReaction.ox_feedback == "X",
        )
    )
    return set(rows.scalars().all())


async def main_works(db: AsyncSession, content_ids: list[int]) -> dict[int, int]:
    """Main work (sort_order 1) of each content that has one."""
    if not content_ids:
        return {}
    rows = await db.execute(
        select(ContentEntityLink.content_id, ContentEntityLink.entity_id).where(
            ContentEntityLink.content_id.in_(content_ids), ContentEntityLink.sort_order == 1,
        )
    )
    return {content_id: entity_id for content_id, entity_id in rows.all()}


async def contents_by_id(db: AsyncSession, content_ids: list[int], topic_id: int) -> dict[int, Content]:
    if not content_ids:
        return {}
    rows = await db.scalars(select(Content).where(Content.content_id.in_(content_ids), *_visible(topic_id)))
    return {content.content_id: content for content in rows.all()}
