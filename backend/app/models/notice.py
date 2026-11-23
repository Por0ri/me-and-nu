"""Canonical administrator notices and their publication schedule."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Notice(Base):
    __tablename__ = "notice"

    notice_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_admin_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("admin_account.admin_account_id"), nullable=False
    )
    updated_by_admin_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("admin_account.admin_account_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("length(title) BETWEEN 1 AND 100", name="ck_notice_title_length"),
        CheckConstraint("length(body) BETWEEN 1 AND 5000", name="ck_notice_body_length"),
        CheckConstraint(
            "(status = 'draft' AND scheduled_at IS NULL AND published_at IS NULL) OR "
            "(status = 'scheduled' AND scheduled_at IS NOT NULL AND published_at IS NULL) OR "
            "(status = 'published' AND published_at IS NOT NULL "
            "AND (scheduled_at IS NULL OR published_at >= scheduled_at))",
            name="ck_notice_publication_state",
        ),
        Index("idx_notice_status_schedule", "status", "scheduled_at", "notice_id"),
        Index("idx_notice_created", created_at.desc(), notice_id.desc()),
    )
