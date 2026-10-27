"""One-time, browser-bound OAuth login attempts; raw state is never stored."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OAuthAttempt(Base):
    __tablename__ = "oauth_attempt"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_session_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("provider IN ('kakao', 'naver')", name="ck_oauth_attempt_provider"),
        CheckConstraint("expires_at > created_at", name="ck_oauth_attempt_expiry"),
        Index("idx_oauth_attempt_browser_created", "browser_session_hash", "created_at"),
        Index("idx_oauth_attempt_expires_at", "expires_at"),
    )
