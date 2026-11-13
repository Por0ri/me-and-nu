"""관리자 권한 부여·회수 이력과 관리자 조치 기록."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, false, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdminAccount(Base):
    __tablename__ = "admin_account"

    admin_account_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_account.user_id"))
    role: Mapped[str] = mapped_column(String(20))
    granted_by_admin_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("admin_account.admin_account_id")
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminActionLog(Base):
    __tablename__ = "admin_action_log"

    admin_action_log_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    admin_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("admin_account.admin_account_id"))
    action_type: Mapped[str] = mapped_column(String(40))
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    before_value: Mapped[Any | None] = mapped_column(
        JSONB(none_as_null=True).with_variant(JSON(none_as_null=True), "sqlite")
    )
    after_value: Mapped[Any | None] = mapped_column(
        JSONB(none_as_null=True).with_variant(JSON(none_as_null=True), "sqlite")
    )
    rationale: Mapped[str] = mapped_column(Text)
    is_reverted: Mapped[bool] = mapped_column(Boolean, server_default=false())
    acted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
