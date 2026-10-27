from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext
from app.core.exceptions import ApiError
from app.models import NotificationSetting, OnboardingAnswersRecord, Tap, TapEntity, TapPreferenceTag
from app.repositories.auth_session_repository import (
    create_auth_session,
    discard_uncommitted_session,
    revoke_auth_session,
    utcnow,
)
from app.repositories.onboarding_repository import find_active_topic, lock_user
from app.repositories.policy_repository import add_consent_history, get_current_policies
from app.schemas.onboarding import (
    ActiveTopic,
    OnboardingAnswers,
    OnboardingAnswersV2,
    OnboardingRequest,
    OnboardingResponse,
    OnboardingUser,
)
from app.services.policy_service import validate_consent_inputs
from app.services.onboarding_catalog_service import validate_v2_selection


async def complete_onboarding(
    db: AsyncSession,
    *,
    context: AuthContext,
    payload: OnboardingRequest,
) -> tuple[OnboardingResponse, str]:
    new_session = None
    try:
        user = await lock_user(db, context.session.user_id)
        if user is None:
            raise ApiError(401, "AUTH_REQUIRED", "사용자 정보를 찾을 수 없습니다.")
        if user.onboarding_completed_at is not None:
            raise ApiError(409, "ONBOARDING_ALREADY_COMPLETED", "온보딩이 이미 완료되었습니다.")
        policies = await get_current_policies(db)
        if not policies:
            raise ApiError(409, "STATE_CONFLICT", "활성 정책이 없습니다. 로컬 정책 seed를 실행해 주세요.")
        submitted = validate_consent_inputs(payload.consents, policies, require_all=True)

        topic = await find_active_topic(db, payload.topic_id)
        if topic is None:
            raise ApiError(404, "TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.")

        if isinstance(payload.answers, OnboardingAnswersV2):
            await validate_v2_selection(db, topic.topic_id, payload.answers)

        user.nickname = payload.nickname
        user.onboarding_completed_at = utcnow()
        for consent_type, item in submitted.items():
            add_consent_history(
                db,
                user_id=user.user_id,
                consent_type=consent_type,
                policy_version=item.policy_version,
                agreed=item.agreed,
            )
        tap = Tap(user_id=user.user_id, topic_id=topic.topic_id)
        db.add(tap)
        await db.flush()
        if isinstance(payload.answers, OnboardingAnswersV2):
            answers = OnboardingAnswersRecord(
                tap_id=tap.tap_id, schema_version=2,
                consumption_type=payload.answers.consumption_type,
                q1_choice=None, q2_choice=None,
            )
            for entity_id in sorted(payload.answers.favorite_entity_ids):
                db.add(TapEntity(
                    tap_id=tap.tap_id, entity_id=entity_id,
                    topic_id=topic.topic_id, source="onboarding",
                ))
            for tag_id in sorted(payload.answers.preference_tag_ids):
                db.add(TapPreferenceTag(tap_id=tap.tap_id, tag_id=tag_id, topic_id=topic.topic_id))
            response_answers = OnboardingAnswersV2(
                version=2,
                favorite_entity_ids=sorted(payload.answers.favorite_entity_ids),
                preference_tag_ids=sorted(payload.answers.preference_tag_ids),
                consumption_type=payload.answers.consumption_type,
            )
        else:
            answers = OnboardingAnswersRecord(
                tap_id=tap.tap_id, schema_version=1,
                q1_choice=payload.answers.q1, q2_choice=payload.answers.q2,
            )
            response_answers = OnboardingAnswers(q1=payload.answers.q1, q2=payload.answers.q2)
        db.add(answers)

        db.add(NotificationSetting(user_id=user.user_id))
        # Validate all onboarding writes before rotating an external session.
        await db.flush()
        await revoke_auth_session(context.session, store=context.session_store)
        new_session, raw_token = await create_auth_session(
            db, user_id=user.user_id, session_state="active", store=context.session_store
        )
        await db.commit()

        active_topic = ActiveTopic(
            id=topic.topic_id,
            code=topic.topic_code,
            name=topic.topic_name,
            subtopic_ids=[],
        )
        return (
            OnboardingResponse(
                user=OnboardingUser(id=user.user_id, account_type=user.account_type),
                onboarding_completed=True,
                active_topic=active_topic,
                answers=response_answers,
            ),
            raw_token,
        )
    except Exception:
        await db.rollback()
        await discard_uncommitted_session(new_session, store=context.session_store)
        raise
