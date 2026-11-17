"""My Page taste edit (temporary): read and replace the onboarding answers of an active user.

Saving drops the user's today/yesterday recommendation rounds, so the keep-on batch
(model/scripts/daily_recommend.py --every) treats the user like a new signup and
rebuilds today's round within seconds. Behavior logs stay; only their round link is
cleared (recommendation_item ON DELETE SET NULL), the same as a `--replace` rerun.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import Entity, OnboardingAnswersRecord, RecommendationRound, TapEntity, TapPreferenceTag, UserAccount
from app.repositories import catalog_repository as catalog_repo
from app.repositories.onboarding_repository import find_active_topic, lock_user
from app.schemas.onboarding import OnboardingAnswersV2, OnboardingWork
from app.schemas.taste import TasteResponse
from app.services.onboarding_catalog_service import validate_v2_selection

KST = timezone(timedelta(hours=9))


async def _tap(db: AsyncSession, user_id: int, topic_id: int):
    topic = await find_active_topic(db, topic_id)
    tap = await catalog_repo.get_tap(db, user_id, topic_id) if topic is not None else None
    if tap is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.")
    return tap


async def get_taste(db: AsyncSession, user_id: int, topic_id: int) -> TasteResponse:
    tap = await _tap(db, user_id, topic_id)
    works = (await db.execute(
        select(Entity).join(TapEntity, (TapEntity.entity_id == Entity.entity_id) & (TapEntity.topic_id == Entity.topic_id))
        .where(TapEntity.tap_id == tap.tap_id).order_by(Entity.entity_id)
    )).scalars().all()
    tag_ids = (await db.execute(
        select(TapPreferenceTag.tag_id).where(TapPreferenceTag.tap_id == tap.tap_id).order_by(TapPreferenceTag.tag_id)
    )).scalars().all()
    record = await db.scalar(select(OnboardingAnswersRecord).where(OnboardingAnswersRecord.tap_id == tap.tap_id))
    return TasteResponse(
        topic_id=topic_id,
        works=[OnboardingWork(
            entity_id=row.entity_id, name=row.name, aliases=row.aliases, image_url=row.image_url,
            release_year=row.release_year, media_format=row.media_format, selectable=bool(row.is_active),
        ) for row in works],
        preference_tag_ids=list(tag_ids),
        consumption_type=record.consumption_type if record is not None else None,
    )


async def update_taste(
    db: AsyncSession, user: UserAccount, topic_id: int, answers: OnboardingAnswersV2, *, now: datetime | None = None,
) -> TasteResponse:
    try:
        await lock_user(db, user.user_id)
        tap = await _tap(db, user.user_id, topic_id)
        await validate_v2_selection(db, topic_id, answers)
        # Re-insert every row: the chips' created_at marks the edit for the feed's first-round wait.
        await db.execute(delete(TapEntity).where(TapEntity.tap_id == tap.tap_id))
        await db.execute(delete(TapPreferenceTag).where(TapPreferenceTag.tap_id == tap.tap_id))
        for entity_id in sorted(answers.favorite_entity_ids):
            db.add(TapEntity(tap_id=tap.tap_id, entity_id=entity_id, topic_id=topic_id, source="onboarding"))
        for tag_id in sorted(answers.preference_tag_ids):
            db.add(TapPreferenceTag(tap_id=tap.tap_id, tag_id=tag_id, topic_id=topic_id))
        record = await db.scalar(select(OnboardingAnswersRecord).where(OnboardingAnswersRecord.tap_id == tap.tap_id))
        if record is None:
            db.add(OnboardingAnswersRecord(tap_id=tap.tap_id, schema_version=2, q1_choice=None, q2_choice=None,
                                           consumption_type=answers.consumption_type))
        else:
            record.schema_version, record.q1_choice, record.q2_choice = 2, None, None
            record.consumption_type = answers.consumption_type
        today = (now or datetime.now(KST)).astimezone(KST).date()
        await db.execute(delete(RecommendationRound).where(
            RecommendationRound.tap_id == tap.tap_id,
            RecommendationRound.service_date.in_([today, today - timedelta(days=1)]),
        ))
        await db.flush()
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return await get_taste(db, user.user_id, topic_id)
