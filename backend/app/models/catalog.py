from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.types import Vector


class Domain(Base):
    __tablename__ = "domain"

    domain_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    domain_code: Mapped[str] = mapped_column(String(50), nullable=False)
    domain_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_active: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        UniqueConstraint(
            "domain_code",
            name="uq_domain_domain_code",
        ),
    )


class Subtopic(Base):
    __tablename__ = "subtopic"

    subtopic_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domain.domain_id"), nullable=False
    )
    parent_subtopic_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("subtopic.subtopic_id")
    )
    subtopic_name: Mapped[str] = mapped_column(String(100), nullable=False)
    depth_level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    linked_content_count: Mapped[int | None] = mapped_column(Integer)


class SourceSite(Base):
    __tablename__ = "source_site"

    source_site_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    media_name: Mapped[str] = mapped_column(String(150), nullable=False)
    collect_method: Mapped[str] = mapped_column(String(20), nullable=False)
    site_url: Mapped[str | None] = mapped_column(String(500))
    feed_url: Mapped[str | None] = mapped_column(String(500))
    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domain.domain_id"), nullable=False
    )
    source_grade: Mapped[str] = mapped_column(
        String(20), nullable=False, default="없음", server_default="없음"
    )
    copyright_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="unknown",
        server_default="unknown",
    )
    license_basis: Mapped[str | None] = mapped_column(String(500))
    is_collecting: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "uq_source_site_feed_url",
            "feed_url",
            unique=True,
            postgresql_where=feed_url.is_not(None),
        ),
    )


class CreatorChannel(Base):
    __tablename__ = "creator_channel"

    creator_channel_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id"), nullable=False
    )
    channel_name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_intro: Mapped[str | None] = mapped_column(String(1000))
    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domain.domain_id"), nullable=False
    )
    follower_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    channel_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    dm_receive_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default="all", server_default="all"
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    style_card: Mapped[dict | None] = mapped_column(JSONB)
    channel_review_status: Mapped[str | None] = mapped_column(String(20))
    channel_review_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "domain_id",
            name="uq_creator_channel_user_id_domain_id",
        ),
    )


class TopicCluster(Base):
    __tablename__ = "topic_cluster"

    topic_cluster_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    representative_content_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("content.content_id")
    )
    subtopic_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("subtopic.subtopic_id")
    )
    cluster_title: Mapped[str | None] = mapped_column(String(300))
    cluster_summary: Mapped[str | None] = mapped_column(Text)
    member_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    centroid_vector: Mapped[list[float] | None] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
