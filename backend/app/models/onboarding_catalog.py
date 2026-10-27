"""Works and genre/theme catalogs used by version-2 onboarding.

The composite foreign keys enforce Topic consistency. Selection counts and
current eligibility belong to the onboarding service transaction.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey,
    ForeignKeyConstraint, Index, Integer, SmallInteger, String,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Entity(Base):
    __tablename__ = "entity"

    entity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topic.topic_id", ondelete="RESTRICT"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="anime", server_default="anime"
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    image_url: Mapped[str | None] = mapped_column(String(1000))
    release_year: Mapped[int | None] = mapped_column(SmallInteger)
    media_format: Mapped[str | None] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("entity_id", "topic_id", name="uq_entity_id_topic_id"),
        CheckConstraint("entity_type IN ('anime')", name="ck_entity_type"),
        Index("idx_entity_topic_active_name", "topic_id", "is_active", "name", "entity_id"),
    )


class EntityExternalId(Base):
    __tablename__ = "entity_external_id"

    provider: Mapped[str] = mapped_column(String(30), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("entity.entity_id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("entity_id", "provider", name="uq_entity_external_id_entity_provider"),
    )


class PreferenceTag(Base):
    __tablename__ = "preference_tag"

    tag_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topic.topic_id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tag_id", "topic_id", name="uq_preference_tag_id_topic_id"),
        UniqueConstraint("topic_id", "kind", "code", name="uq_preference_tag_topic_kind_code"),
        CheckConstraint("kind IN ('genre', 'theme')", name="ck_preference_tag_kind"),
        Index("idx_preference_tag_topic_active_sort", "topic_id", "is_active", "sort_order", "tag_id"),
    )


class EntityTag(Base):
    __tablename__ = "entity_tag"

    entity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    tag_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["entity_id", "topic_id"], ["entity.entity_id", "entity.topic_id"],
            name="fk_entity_tag_entity_topic", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tag_id", "topic_id"], ["preference_tag.tag_id", "preference_tag.topic_id"],
            name="fk_entity_tag_tag_topic", ondelete="RESTRICT",
        ),
        Index("idx_entity_tag_tag_topic", "tag_id", "topic_id"),
    )


class TapEntity(Base):
    __tablename__ = "tap_entity"

    tap_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="onboarding", server_default="onboarding"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tap_id", "topic_id"], ["tap.tap_id", "tap.topic_id"],
            name="fk_tap_entity_tap_topic", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["entity_id", "topic_id"], ["entity.entity_id", "entity.topic_id"],
            name="fk_tap_entity_entity_topic", ondelete="RESTRICT",
        ),
        CheckConstraint("source IN ('onboarding')", name="ck_tap_entity_source"),
        Index("idx_tap_entity_entity_topic", "entity_id", "topic_id"),
    )


class TapPreferenceTag(Base):
    __tablename__ = "tap_preference_tag"

    tap_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    tag_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tap_id", "topic_id"], ["tap.tap_id", "tap.topic_id"],
            name="fk_tap_preference_tag_tap_topic", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tag_id", "topic_id"], ["preference_tag.tag_id", "preference_tag.topic_id"],
            name="fk_tap_preference_tag_tag_topic", ondelete="RESTRICT",
        ),
        Index("idx_tap_preference_tag_tag_topic", "tag_id", "topic_id"),
    )
