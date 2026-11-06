"""콘텐츠 신고(API-070)와 마이페이지 문의."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 에이전트가 만든 애니 글에서 실제로 생길 문제 기준 (11/6 결정). 화면 문구는 프론트가 정한다.
REPORT_REASONS = ("wrong_info", "spoiler", "rights", "inappropriate", "other")
REPORT_STATUSES = ("received", "reviewing", "resolved", "rejected")
INQUIRY_TYPES = ("service", "bug")
INQUIRY_STATUSES = ("received", "answered", "closed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class ContentReport(Base):
    __tablename__ = "content_report"

    report_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 탈퇴해도 글 검토 기록은 남기고 신고자만 비운다.
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="SET NULL")
    )
    content_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content.content_id", ondelete="CASCADE"), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received", server_default="received")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in("reason_code", REPORT_REASONS), name="ck_content_report_reason_code"),
        CheckConstraint(_in("status", REPORT_STATUSES), name="ck_content_report_status"),
        # 한 회원이 같은 글은 한 번만 신고한다.
        UniqueConstraint("user_id", "content_id", name="uq_content_report_user_content"),
        Index("idx_content_report_content", "content_id"),
        Index("idx_content_report_status_created_at", "status", created_at.desc()),
    )


class Inquiry(Base):
    __tablename__ = "inquiry"

    inquiry_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 문의 내용엔 개인정보가 들어갈 수 있어 탈퇴하면 같이 지운다.
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="CASCADE"), nullable=False
    )
    inquiry_type: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received", server_default="received")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in("inquiry_type", INQUIRY_TYPES), name="ck_inquiry_type"),
        CheckConstraint(_in("status", INQUIRY_STATUSES), name="ck_inquiry_status"),
        Index("idx_inquiry_user_created_at", "user_id", created_at.desc()),
        Index("idx_inquiry_status_created_at", "status", created_at.desc()),
    )
