from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def get_user_by_email(
    db: AsyncSession,
    email: str,
) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(
    db: AsyncSession,
    user_id: int,
) -> User | None:
    return await db.get(User, user_id)


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    nickname: str,
    role: str,
    hashed_password: str,
) -> User:
    user = User(
        email=email,
        nickname=nickname,
        role=role,
        hashed_password=hashed_password,
    )
    db.add(user)
    await db.flush()
    return user
