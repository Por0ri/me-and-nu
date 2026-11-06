"""회원 탈퇴(API-009): 지금은 즉시 삭제만 지원합니다.

회원 줄을 지우면 DB 외래 키(ON DELETE CASCADE)가 로그인 세션 · 동의 기록 · 알림 설정 · 관심 분야(tap)와
그 아래 온보딩 답 · 반응 · 저장 · 행동 기록 · 추천 결과, 알림 · 챗봇 대화를 함께 지웁니다.
에이전트 실행 기록은 요청자 칸만 비웁니다(SET NULL). 크리에이터 채널이 있는 계정은 외래 키가 막습니다.
"""

from datetime import datetime, timezone
import logging

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import AuthSession, UserAccount
from app.repositories.redis_session_store import RedisSessionStore

logger = logging.getLogger(__name__)


async def withdraw_now(
    db: AsyncSession,
    *,
    user_id: int,
    session: AuthSession,
    store: RedisSessionStore | None,
) -> datetime:
    """회원과 연결 데이터를 지우고, 지금 세션을 끝낸 뒤 지운 시각을 돌려줍니다."""
    try:
        await db.execute(delete(UserAccount).where(UserAccount.user_id == user_id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(
            409, "WITHDRAWAL_BLOCKED", "연결된 크리에이터 채널이 있어 바로 탈퇴할 수 없습니다."
        ) from None
    deleted_at = datetime.now(timezone.utc)
    # PostgreSQL 세션은 회원과 함께 지워졌습니다. Redis 세션은 지금 기기 것만 지우고,
    # 다른 기기 세션은 회원이 없어서 인증 단계에서 거절되며 만료 시각에 사라집니다.
    if store is not None:
        try:
            await store.revoke(session)
        except ApiError:
            logger.warning("탈퇴한 회원의 Redis 세션을 지우지 못했습니다. 회원이 없어 더는 인증되지 않습니다.")
    return deleted_at
