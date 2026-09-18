from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.repositories.user_repository import (
    create_user,
    get_user_by_email,
)
from app.schemas.auth import RegisterRequest


class EmailAlreadyRegisteredError(Exception):
    """이미 가입된 이메일로 회원가입을 시도했습니다."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def register_user(
    db: AsyncSession,
    register_request: RegisterRequest,
) -> User:
    email = normalize_email(str(register_request.email))

    if await get_user_by_email(db, email) is not None:
        raise EmailAlreadyRegisteredError()

    try:
        user = await create_user(
            db,
            email=email,
            nickname=register_request.nickname.strip(),
            role=register_request.role,
            hashed_password=hash_password(register_request.password),
        )
        await db.commit()
        await db.refresh(user)
    except IntegrityError as exc:
        await db.rollback()
        raise EmailAlreadyRegisteredError() from exc
    except Exception:
        await db.rollback()
        raise

    return user


async def authenticate_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
) -> User | None:
    user = await get_user_by_email(
        db,
        normalize_email(email),
    )

    if user is None:
        return None

    if not verify_password(password, user.hashed_password):
        return None

    return user
