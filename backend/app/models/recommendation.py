"""Tables that connect the recommendation model to the service feed.

content_entity_link says which works a content is about (sort_order 1 is the
main work). A recommendation_round is one ranked list per Tap, day and
algorithm version; "latest" records the plain newest-first feed so both can be
compared. user_event is an append-only activity log, and client_event_id makes
resent event batches idempotent.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime, Double, ForeignKey,
    ForeignKeyConstraint, Index, Integer, SmallInteger, String,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


LINK_METHODS = ("carousel", "agent", "manual")
USER_EVENT_TYPES = (
    "impression", "open",
    "like", "like_cancel", "bookmark", "bookmark_cancel",
    "preference_positive", "preference_negative", "preference_cancel",
    "card_swipe", "share", "spoiler_reveal", "watched",
)
DATA_SOURCES = ("production", "local_demo", "automated_test", "imported")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class ContentEntityLink(Base):
    __tablename__ = "content_entity_link"

    content_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content.content_id", ondelete="CASCADE"),
        primary_key=True, autoincrement=False,
    )
    entity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    link_method: Mapped[str] = mapped_column(String(20), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["entity_id", "topic_id"], ["entity.entity_id", "entity.topic_id"],
            name="fk_content_entity_link_entity_topic", ondelete="RESTRICT",
        ),
        UniqueConstraint("content_id", "sort_order", name="uq_content_entity_link_content_sort"),
        CheckConstraint(_one_of("link_method", LINK_METHODS), name="ck_content_entity_link_method"),
        CheckConstraint("sort_order >= 1", name="ck_content_entity_link_sort_order"),
        Index("idx_content_entity_link_entity_topic", "entity_id", "topic_id"),
    )


class RecommendationRound(Base):
    __tablename__ = "recommendation_round"

    recommendation_round_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    tap_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tap.tap_id", ondelete="CASCADE"), nullable=False
    )
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tap_id", "service_date", "algorithm_version",
            name="uq_recommendation_round_tap_date_version",
        ),
    )


class RecommendationItem(Base):
    __tablename__ = "recommendation_item"

    recommendation_item_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    recommendation_round_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("recommendation_round.recommendation_round_id", ondelete="CASCADE"),
        nullable=False,
    )
    content_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content.content_id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float | None] = mapped_column(Double)
    recommend_reason: Mapped[dict | None] = mapped_column(JSONB)
    impressed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("recommendation_round_id", "rank", name="uq_recommendation_item_round_rank"),
        UniqueConstraint(
            "recommendation_round_id", "content_id", name="uq_recommendation_item_round_content"
        ),
        CheckConstraint("rank >= 1", name="ck_recommendation_item_rank"),
        Index("idx_recommendation_item_content", "content_id"),
    )


class UserEvent(Base):
    __tablename__ = "user_event"

    user_event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="CASCADE"), nullable=False
    )
    tap_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content.content_id", ondelete="CASCADE"), nullable=False
    )
    recommendation_item_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("recommendation_item.recommendation_item_id", ondelete="SET NULL"),
    )
    event_value: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    data_source: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tap_id", "user_id"], ["tap.tap_id", "tap.user_id"],
            name="fk_user_event_tap_owner", ondelete="CASCADE",
        ),
        UniqueConstraint("client_event_id", name="uq_user_event_client_event_id"),
        CheckConstraint(_one_of("event_type", USER_EVENT_TYPES), name="ck_user_event_type"),
        CheckConstraint(_one_of("data_source", DATA_SOURCES), name="ck_user_event_data_source"),
        Index("idx_user_event_user_occurred", "user_id", "occurred_at"),
        Index("idx_user_event_tap_type_received", "tap_id", "event_type", "received_at"),
        Index("idx_user_event_content_type", "content_id", "event_type"),
        Index("idx_user_event_recommendation_item", "recommendation_item_id"),
    )
