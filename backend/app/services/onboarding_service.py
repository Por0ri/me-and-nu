from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext
from app.core.exceptions import ApiError
from app.models import NotificationSetting, OnboardingAnswersRecord, Tap
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
    OnboardingRequest,
    OnboardingResponse,
    OnboardingUser,
)
from app.services.policy_service import validate_consent_inputs


def _is_under_fourteen(value: date) -> bool:
    today = utcnow().date()
    if value > today:
        return True
    years = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
    return years < 14


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
        if _is_under_fourteen(payload.birth_date):
            raise ApiError(403, "AGE_RESTRICTED", "만 14세 미만은 가입할 수 없습니다.")

        policies = await get_current_policies(db)
        if not policies:
            raise ApiError(409, "STATE_CONFLICT", "활성 정책이 없습니다. 로컬 정책 seed를 실행해 주세요.")
        submitted = validate_consent_inputs(payload.consents, policies, require_all=True)

        topic = await find_active_topic(db, payload.topic_id)
        if topic is None:
            raise ApiError(404, "TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.")

        user.nickname = payload.nickname
        user.birth_date = payload.birth_date
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
        answers = OnboardingAnswersRecord(
            tap_id=tap.tap_id,
            q1_choice=payload.answers.q1,
            q2_choice=payload.answers.q2,
        )
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
                answers=OnboardingAnswers(q1=answers.q1_choice, q2=answers.q2_choice),
            ),
            raw_token,
        )
    except Exception:
        await db.rollback()
        await discard_uncommitted_session(new_session, store=context.session_store)
        raise
