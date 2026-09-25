"""Create the reserved local Swagger test identity after the V1 catalog seed.

The identity has no usable password and is distinguished by its reserved
provider user ID. The caller owns the transaction for ``ensure_dev_user``.
"""

import asyncio
import sys
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import (
    ConsentHistory,
    NotificationSetting,
    Subtopic,
    Tap,
    TapTopic,
    Topic,
    UserAccount,
)
from app.repositories.auth_session_repository import utcnow
from app.repositories.policy_repository import add_consent_history, get_current_policies


DEV_USER_EMAIL = "swagger-demo@menu.invalid"
DEV_USER_PROVIDER_ID = "menu-swagger-demo-v1"
DEV_USER_AUTH_PROVIDER = "kakao"


async def ensure_dev_user(db: AsyncSession) -> UserAccount:
    """Idempotently provision an onboarded consumer for local Swagger calls.

    A reserved provider identity keeps the account distinct from normal local
    email/password users. An email or provider-ID collision is never adopted.
    """
    topic = await db.scalar(
        select(Topic).where(Topic.topic_code == "movie", Topic.is_active.is_(True))
    )
    if topic is None:
        raise RuntimeError("영화 Topic이 없습니다. 먼저 V1 로컬 시드를 실행하세요.")
    subtopic = await db.scalar(
        select(Subtopic).where(
            Subtopic.topic_id == topic.topic_id,
            Subtopic.parent_subtopic_id.is_(None),
            Subtopic.subtopic_name == "영화 정보",
        )
    )
    if subtopic is None:
        raise RuntimeError("영화 정보 Subtopic이 없습니다. 먼저 V1 로컬 시드를 실행하세요.")

    policies = await get_current_policies(db)
    if any(
        policy_type not in policies or not policies[policy_type].is_required
        for policy_type in ("terms", "privacy")
    ):
        raise RuntimeError("필수 이용약관 또는 개인정보 정책이 없습니다. 먼저 V1 로컬 시드를 실행하세요.")

    user = await db.scalar(
        select(UserAccount).where(func.lower(UserAccount.email) == DEV_USER_EMAIL)
    )
    if user is None:
        provider_owner = await db.scalar(
            select(UserAccount).where(
                UserAccount.auth_provider == DEV_USER_AUTH_PROVIDER,
                UserAccount.provider_user_id == DEV_USER_PROVIDER_ID,
            )
        )
        if provider_owner is not None:
            raise RuntimeError("Swagger 데모 provider ID가 다른 계정에 사용 중입니다.")
        user = UserAccount(
            provider_user_id=DEV_USER_PROVIDER_ID,
            email=DEV_USER_EMAIL,
            nickname="Swagger 데모 사용자",
            signup_channel="consumer",
            account_status="active",
            birth_date=date(2000, 1, 1),
            auth_provider=DEV_USER_AUTH_PROVIDER,
            hashed_password=None,
            onboarding_completed_at=utcnow(),
        )
        db.add(user)
        await db.flush()
    elif (
        user.auth_provider != DEV_USER_AUTH_PROVIDER
        or user.provider_user_id != DEV_USER_PROVIDER_ID
        or user.email != DEV_USER_EMAIL
        or user.hashed_password is not None
    ):
        raise RuntimeError("Swagger 데모 이메일이 다른 계정에 사용 중입니다.")

    if (
        user.account_status != "active"
        or user.signup_channel != "consumer"
        or user.onboarding_completed_at is None
    ):
        raise RuntimeError("Swagger 데모 계정의 활성 상태 또는 온보딩 상태가 올바르지 않습니다.")

    tap = await db.scalar(
        select(Tap).where(
            Tap.user_id == user.user_id,
            Tap.topic_id == topic.topic_id,
            Tap.deleted_at.is_(None),
        )
    )
    if tap is None:
        tap = Tap(user_id=user.user_id, topic_id=topic.topic_id)
        db.add(tap)
        await db.flush()

    tap_topic = await db.scalar(
        select(TapTopic).where(
            TapTopic.tap_id == tap.tap_id,
            TapTopic.subtopic_id == subtopic.subtopic_id,
        )
    )
    if tap_topic is None:
        db.add(TapTopic(tap_id=tap.tap_id, subtopic_id=subtopic.subtopic_id))

    if await db.get(NotificationSetting, user.user_id) is None:
        db.add(NotificationSetting(user_id=user.user_id))

    for policy_type, policy in policies.items():
        if not policy.is_required:
            continue
        existing = await db.scalar(
            select(ConsentHistory.consent_history_id).where(
                ConsentHistory.user_id == user.user_id,
                ConsentHistory.consent_type == policy_type,
            )
        )
        if existing is None:
            add_consent_history(
                db,
                user_id=user.user_id,
                consent_type=policy_type,
                policy_version=policy.policy_version,
                agreed=True,
            )

    await db.flush()
    return user


async def run_dev_user_seed() -> int:
    async with AsyncSessionLocal() as db:
        async with db.begin():
            user = await ensure_dev_user(db)
            return user.user_id


def main() -> None:
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            user_id = runner.run(run_dev_user_seed())
    else:
        user_id = asyncio.run(run_dev_user_seed())
    print(f"Swagger 데모 사용자 시드 완료: user_id={user_id}")


if __name__ == "__main__":
    main()
