"""Versioned onboarding answers; v1 choices and v2 preferences stay distinct."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, SmallInteger, String, func
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
    q1_choice: Mapped[str | None] = mapped_column(String(32))
    q2_choice: Mapped[str | None] = mapped_column(String(32))
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    consumption_type: Mapped[str | None] = mapped_column(String(32))
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
        CheckConstraint(
            "schema_version IN (1, 2)",
            name="ck_onboarding_answers_schema_version",
        ),
        CheckConstraint(
            "consumption_type IN ('world', 'relationships', 'ost', 'goods', 'events', 'news')",
            name="ck_onboarding_answers_consumption_type",
        ),
        CheckConstraint(
            "(schema_version = 1 AND q1_choice IS NOT NULL AND q2_choice IS NOT NULL "
            "AND consumption_type IS NULL) OR "
            "(schema_version = 2 AND q1_choice IS NULL AND q2_choice IS NULL "
            "AND consumption_type IS NOT NULL)",
            name="ck_onboarding_answers_version_shape",
        ),
    )
