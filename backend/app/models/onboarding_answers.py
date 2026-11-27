"""Versioned onboarding answers; v1 choices and v2 preferences stay distinct.

tap_consumption_type holds the Q3 ranking (2026-11); onboarding_answers.consumption_type
stays rank 1, the column the production main code and the admin screens read.
"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint, func
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


class TapConsumptionType(Base):
    """Q3 consumption types in the order the member tapped them (rank 1 = consumption_type above)."""

    __tablename__ = "tap_consumption_type"

    tap_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tap.tap_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    rank: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    consumption_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tap_id", "consumption_type", name="uq_tap_consumption_type_tap_type"),
        CheckConstraint("rank BETWEEN 1 AND 6", name="ck_tap_consumption_type_rank"),
        CheckConstraint(
            "consumption_type IN ('world', 'relationships', 'ost', 'goods', 'events', 'news')",
            name="ck_tap_consumption_type_consumption_type",
        ),
    )
