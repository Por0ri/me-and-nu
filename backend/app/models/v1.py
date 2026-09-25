"""Persistent V1 sessions, policy history, subscriptions, and content actions."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuthSession(Base):
    __tablename__ = "auth_session"

    auth_session_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    session_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="CASCADE")
    )
    session_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="anonymous", server_default="anonymous"
    )
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        UniqueConstraint("session_token_hash", name="uq_auth_session_token_hash"),
        CheckConstraint(
            "session_state IN ('anonymous', 'onboarding_pending', 'active')",
            name="ck_auth_session_state",
        ),
        CheckConstraint(
            "(session_state = 'anonymous' AND user_id IS NULL) "
            "OR (session_state <> 'anonymous' AND user_id IS NOT NULL)",
            name="ck_auth_session_state_user",
        ),
        Index(
            "idx_auth_session_user_expires_revoked",
            "user_id",
            "expires_at",
            "revoked_at",
        ),
        Index("idx_auth_session_expires_revoked", "expires_at", "revoked_at"),
    )


class Policy(Base):
    __tablename__ = "policy"

    policy_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    policy_type: Mapped[str] = mapped_column(String(30), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    content_url: Mapped[str | None] = mapped_column(String(1000))
    is_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "policy_type", "policy_version", name="uq_policy_type_version"
        ),
        CheckConstraint(
            "content IS NOT NULL OR content_url IS NOT NULL",
            name="ck_policy_has_content",
        ),
        Index(
            "idx_policy_active_type_effective",
            "is_active",
            "policy_type",
            effective_at.desc(),
        ),
    )


class ConsentHistory(Base):
    __tablename__ = "consent_history"

    consent_history_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_type: Mapped[str] = mapped_column(String(30), nullable=False)
    is_agreed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(30), nullable=False)
    agreed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "idx_consent_history_user_type_latest",
            "user_id",
            "consent_type",
            created_at.desc(),
            consent_history_id.desc(),
        ),
    )


class NotificationSetting(Base):
    __tablename__ = "notification_setting"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    report_result: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    creator_new_post: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    saved_resurface: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    marketing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Tap(Base):
    __tablename__ = "tap"

    tap_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topic.topic_id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tap_id", "user_id", name="uq_tap_id_user_id"),
        Index(
            "uq_tap_active_user_topic",
            "user_id",
            "topic_id",
            unique=True,
            postgresql_where=deleted_at.is_(None),
        ),
        Index("idx_tap_user_deleted", "user_id", "deleted_at"),
    )


class TapTopic(Base):
    __tablename__ = "tap_topic"

    tap_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tap.tap_id", ondelete="CASCADE"),
        primary_key=True,
    )
    subtopic_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("subtopic.subtopic_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    subscribed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_tap_topic_subtopic_id", "subtopic_id"),)


class ContentTag(Base):
    __tablename__ = "content_tag"

    content_tag_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    content_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("content.content_id", ondelete="CASCADE"),
        nullable=False,
    )
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topic.topic_id", ondelete="RESTRICT"), nullable=False
    )
    subtopic_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("subtopic.subtopic_id", ondelete="RESTRICT"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "content_id", "subtopic_id", name="uq_content_tag_content_subtopic"
        ),
        Index("idx_content_tag_subtopic_content", "subtopic_id", "content_id"),
        Index("idx_content_tag_topic_content", "topic_id", "content_id"),
    )


class ContentReaction(Base):
    __tablename__ = "content_reaction"

    content_reaction_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    tap_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("content.content_id", ondelete="CASCADE"),
        nullable=False,
    )
    ox_feedback: Mapped[str | None] = mapped_column(String(1))
    is_liked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    liked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tap_id", "user_id"],
            ["tap.tap_id", "tap.user_id"],
            name="fk_content_reaction_tap_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "user_id",
            "tap_id",
            "content_id",
            name="uq_content_reaction_user_tap_content",
        ),
        CheckConstraint(
            "ox_feedback IN ('O', 'X')", name="ck_content_reaction_ox_feedback"
        ),
        Index("idx_content_reaction_user_content", "user_id", "content_id"),
    )


class SavedItem(Base):
    __tablename__ = "saved_item"

    saved_item_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_account.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    content_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("content.content_id", ondelete="CASCADE"),
        nullable=False,
    )
    tap_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="kept", server_default="kept"
    )
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    saved_tags: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tap_id", "user_id"],
            ["tap.tap_id", "tap.user_id"],
            name="fk_saved_item_tap_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "user_id", "content_id", "tap_id", name="uq_saved_item_user_content_tap"
        ),
        CheckConstraint(
            "status IN ('kept', 'resurface_off', 'deleted')",
            name="ck_saved_item_status",
        ),
        Index(
            "idx_saved_item_user_status_saved_at",
            "user_id",
            "status",
            saved_at.desc(),
        ),
    )
