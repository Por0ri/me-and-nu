"""One Q1 and one Q2 preference saved for an onboarding interest."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OnboardingAnswersRecord(Base):
    __tablename__ = "onboarding_answers"

    tap_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tap.tap_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    q1_choice: Mapped[str] = mapped_column(String(32), nullable=False)
    q2_choice: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "q1_choice IN ('world', 'relationships', 'ost', 'goods', 'events', 'news')",
            name="ck_onboarding_answers_q1_choice",
        ),
        CheckConstraint(
            "q2_choice IN ('releases', 'characters', 'restock', 'events')",
            name="ck_onboarding_answers_q2_choice",
        ),
    )
