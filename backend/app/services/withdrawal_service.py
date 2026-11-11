"""회원 탈퇴(API-009), 유예 중 복구(API-010), 유예가 끝난 계정 삭제.

탈퇴를 신청하면 계정을 바로 막고 데이터는 유예 기간 동안 그대로 둡니다. 유예 안에 같은 소셜 계정으로 로그인하면
복구 대기 세션을 받고, 복구 API를 불렀을 때만 계정이 돌아옵니다.

유예가 끝난 계정은 app.jobs.purge_withdrawn_users 가 지웁니다. 회원 줄을 지우면 DB 외래 키(ON DELETE CASCADE)가
로그인 세션 · 동의 기록 · 알림 설정 · 관심 분야(tap)와 그 아래 온보딩 답 · 반응 · 저장 · 행동 기록 · 추천 결과,
알림 · 챗봇 대화 · 문의를 함께 지웁니다. 에이전트 실행 기록과 콘텐츠 신고는 요청자 · 신고자 칸만 비웁니다(SET NULL).
"""

from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.models import AuthSession, CreatorChannel, UserAccount
from app.repositories.auth_session_repository import (
    create_auth_session,
    discard_uncommitted_session,
    revoke_auth_session,
    utcnow,
)
from app.repositories.onboarding_repository import lock_user
from app.repositories.redis_session_store import RedisSessionStore

logger = logging.getLogger(__name__)

WITHDRAWAL_GRACE_DAYS = 30


def scheduled_delete_at(withdrawn_at: datetime) -> datetime:
    """탈퇴 신청 시각으로 계정을 지울 시각을 계산합니다."""
    aware = withdrawn_at if withdrawn_at.tzinfo is not None else withdrawn_at.replace(tzinfo=timezone.utc)
    return aware + timedelta(days=WITHDRAWAL_GRACE_DAYS)


def in_grace_period(user: UserAccount) -> bool:
    return (
        user.account_status == "withdrawn"
        and user.withdrawn_at is not None
        and utcnow() < scheduled_delete_at(user.withdrawn_at)
    )


async def request_withdrawal(
    db: AsyncSession,
    *,
    user: UserAccount,
    session: AuthSession,
    store: RedisSessionStore | None,
) -> datetime:
    """계정을 막고 지금 세션을 끝낸 뒤 삭제 예정 시각을 돌려줍니다."""
    # 채널이 있는 계정은 외래 키 때문에 유예가 끝나도 못 지우므로 신청할 때 거절합니다.
    channel = await db.scalar(
        select(CreatorChannel.creator_channel_id).where(CreatorChannel.user_id == user.user_id).limit(1)
    )
    if channel is not None:
        raise ApiError(409, "WITHDRAWAL_BLOCKED", "연결된 크리에이터 채널이 있어 탈퇴할 수 없습니다.")
    now = utcnow()
    user.account_status = "withdrawn"
    user.withdrawn_at = now
    if store is None:
        session.revoked_at = now
    await db.commit()
    # 다른 기기의 세션은 계정 상태 검사에서 바로 거절되고 만료 시각에 사라집니다.
    if store is not None:
        try:
            await store.revoke(session)
        except ApiError:
            logger.warning("탈퇴한 회원의 Redis 세션을 지우지 못했습니다. 계정이 막혀 더는 인증되지 않습니다.")
    return scheduled_delete_at(now)


async def restore_withdrawal(
    db: AsyncSession,
    *,
    user_id: int,
    session: AuthSession,
    store: RedisSessionStore | None,
) -> tuple[UserAccount, str]:
    """유예 중인 계정을 되살리고 복구 대기 세션을 일반 세션으로 바꿉니다."""
    new_session = None
    try:
        # 삭제 작업과 겹쳐도 둘 중 하나만 성공하도록 회원 줄을 잠급니다.
        user = await lock_user(db, user_id)
        if user is None or not in_grace_period(user):
            raise ApiError(409, "RESTORATION_EXPIRED", "복구 기한이 지났습니다.")
        user.account_status = "active"
        user.withdrawn_at = None
        user.withdrawal_reason = None
        await revoke_auth_session(session, store=store)
        new_session, raw_token = await create_auth_session(
            db, user_id=user.user_id, session_state="active", store=store
        )
        await db.commit()
        return user, raw_token
    except Exception:
        await db.rollback()
        await discard_uncommitted_session(new_session, store=store)
        raise


async def purge_withdrawn_accounts(
    db: AsyncSession, *, apply: bool = False, user_id: int | None = None
) -> dict:
    """유예가 끝난 탈퇴 계정을 지웁니다. 트랜잭션은 호출한 쪽이 맡습니다.

    user_id를 주면 그 계정 하나만 유예와 상관없이 지웁니다(테스트 계정 재가입 시험용).
    어떤 경우에도 탈퇴를 신청하지 않은 계정은 지우지 않습니다.
    """
    conditions = [UserAccount.account_status == "withdrawn", UserAccount.withdrawn_at.is_not(None)]
    if user_id is not None:
        conditions.append(UserAccount.user_id == user_id)
    else:
        conditions.append(UserAccount.withdrawn_at <= utcnow() - timedelta(days=WITHDRAWAL_GRACE_DAYS))
    targets = (
        await db.execute(select(UserAccount.user_id).where(*conditions).order_by(UserAccount.user_id))
    ).scalars().all()
    report = {"mode": "apply" if apply else "dry_run", "targets": len(targets), "deleted": 0, "blocked_user_ids": []}
    if not apply:
        return report
    for target in targets:
        try:
            async with db.begin_nested():
                # 조건을 다시 걸어서, 그사이 복구된 계정은 지우지 않습니다.
                result = await db.execute(
                    delete(UserAccount)
                    .where(UserAccount.user_id == target, *conditions)
                    .execution_options(synchronize_session=False)
                )
        except IntegrityError:
            report["blocked_user_ids"].append(target)
            continue
        report["deleted"] += result.rowcount
    return report
