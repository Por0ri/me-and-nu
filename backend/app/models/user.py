from datetime import date, datetime
from typing import Literal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserAccount(Base):
    __tablename__ = "user_account"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    provider_user_id: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(50), nullable=False)
    account_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default="active",
    )
    birth_date: Mapped[date | None] = mapped_column(Date)
    auth_provider: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="local",
        server_default="local",
    )
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    profile_image_url: Mapped[str | None] = mapped_column(String(500))
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    withdrawal_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "auth_provider",
            "provider_user_id",
            name="uq_user_account_auth_provider_provider_user_id",
        ),
        Index(
            "uq_user_account_email",
            func.lower(email),
            unique=True,
            postgresql_where=email.is_not(None),
        ),
        CheckConstraint(
            "account_status IN "
            "('active', 'warned', 'suspended', 'banned', 'withdrawn')",
            name="ck_user_account_status",
        ),
        CheckConstraint(
            "auth_provider IN ('local', 'kakao', 'naver', 'apple')",
            name="ck_user_account_auth_provider",
        ),
        CheckConstraint(
            "(auth_provider = 'local' AND email IS NOT NULL "
            "AND hashed_password IS NOT NULL "
            "AND provider_user_id IS NULL) "
            "OR (auth_provider <> 'local' "
            "AND provider_user_id IS NOT NULL)",
            name="ck_user_account_auth_identity",
        ),
    )

    @property
    def id(self) -> int:
        """기존 API 응답 계약을 유지하기 위한 호환 속성입니다."""
        return self.user_id

    @property
    def account_type(self) -> Literal["user"]:
        """일반 회원 API의 단일 유형이며 DB에 별도로 저장하지 않습니다."""
        return "user"

    @property
    def role(self) -> Literal["user"]:
        """개발용 가입 계약과 호환되는 단일 회원 유형입니다."""
        return self.account_type

    def __repr__(self) -> str:
        return (
            f"UserAccount(user_id={self.user_id!r}, "
            f"email={self.email!r})"
        )
