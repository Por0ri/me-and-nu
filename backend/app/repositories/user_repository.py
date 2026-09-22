from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserAccount


async def get_user_by_email(
    db: AsyncSession,
    email: str,
) -> UserAccount | None:
    result = await db.execute(
        select(UserAccount).where(UserAccount.email == email)
    )
    return result.scalar_one_or_none()


async def get_user_by_id(
    db: AsyncSession,
    user_id: int,
) -> UserAccount | None:
    return await db.get(UserAccount, user_id)


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    nickname: str,
    signup_channel: str,
    hashed_password: str,
) -> UserAccount:
    user = UserAccount(
        email=email,
        nickname=nickname,
        signup_channel=signup_channel,
        auth_provider="local",
        hashed_password=hashed_password,
    )
    db.add(user)
    await db.flush()
    return user
