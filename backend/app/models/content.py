from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
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


class Draft(Base):
    __tablename__ = "draft"

    draft_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    agent_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agent_run.agent_run_id")
    )
    creator_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("creator_channel.creator_channel_id")
    )
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topic.topic_id"), nullable=False
    )
    production_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="human", server_default="human"
    )
    content_type: Mapped[str | None] = mapped_column(String(30))
    title: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    structure_template: Mapped[dict | None] = mapped_column(JSONB)
    structure_template_history: Mapped[dict | None] = mapped_column(JSONB)
    factcheck_result: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="writing", server_default="writing"
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
    similar_draft_warning: Mapped[str | None] = mapped_column(Text)
    similar_draft_score: Mapped[Decimal | None] = mapped_column(Numeric)

    __table_args__ = (
        Index(
            "uq_draft_agent_run_id",
            "agent_run_id",
            unique=True,
            postgresql_where=agent_run_id.is_not(None),
        ),
        Index(
            "idx_draft_topic_status_updated_at",
            "topic_id",
            "status",
            updated_at.desc(),
        ),
        CheckConstraint(
            "agent_run_id IS NOT NULL OR creator_channel_id IS NOT NULL",
            name="ck_draft_owner",
        ),
        CheckConstraint(
            "production_type IN ('human', 'ai', 'hybrid')",
            name="ck_draft_production_type",
        ),
        CheckConstraint(
            "status IN "
            "('writing', 'checking', 'approved', 'published', 'discarded')",
            name="ck_draft_status",
        ),
        CheckConstraint(
            "similar_draft_score IS NULL "
            "OR similar_draft_score BETWEEN 0 AND 1",
            name="ck_draft_similarity_score",
        ),
    )


class Content(Base):
    __tablename__ = "content"

    content_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    source_draft_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("draft.draft_id", ondelete="SET NULL"),
    )
    creator_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("creator_channel.creator_channel_id")
    )
    topic_cluster_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("topic_cluster.topic_cluster_id")
    )
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topic.topic_id"), nullable=False
    )
    production_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="human", server_default="human"
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    keyword: Mapped[list | dict | None] = mapped_column(JSONB)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    content_type: Mapped[str | None] = mapped_column(String(30))
    difficulty_level: Mapped[int | None] = mapped_column(SmallInteger)
    promotional_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    harmful_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    judgment_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    ai_judgment_basis: Mapped[dict | None] = mapped_column(JSONB)
    is_sponsored: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
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
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        Index(
            "uq_content_source_draft_id",
            "source_draft_id",
            unique=True,
            postgresql_where=source_draft_id.is_not(None),
        ),
        Index(
            "idx_content_topic_status_published_at",
            "topic_id",
            "status",
            published_at.desc(),
        ),
        CheckConstraint(
            "production_type IN ('human', 'ai', 'hybrid')",
            name="ck_content_production_type",
        ),
        CheckConstraint(
            "judgment_status IN "
            "('pending', 'confirmed', 'needs_review', 'failed')",
            name="ck_content_judgment_status",
        ),
        CheckConstraint(
            "status IN ('active', 'hidden', 'pending_review', 'deleted')",
            name="ck_content_status",
        ),
        CheckConstraint(
            "promotional_score IS NULL "
            "OR promotional_score BETWEEN 0 AND 1",
            name="ck_content_promotional_score",
        ),
        CheckConstraint(
            "harmful_score IS NULL OR harmful_score BETWEEN 0 AND 1",
            name="ck_content_harmful_score",
        ),
    )


class ContentSource(Base):
    __tablename__ = "content_source"

    content_source_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    content_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("content.content_id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_source_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "agent_run_source.agent_run_source_id",
            ondelete="SET NULL",
        ),
    )
    source_site_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("source_site.source_site_id", ondelete="SET NULL"),
    )
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_title: Mapped[str | None] = mapped_column(String(300))
    source_role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="material",
        server_default="material",
    )
    citation_order: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "content_id",
            "source_url",
            name="uq_content_source_content_id_source_url",
        ),
        Index("idx_content_source_content_id", "content_id"),
        CheckConstraint(
            "source_role IN ('primary', 'material', 'citation')",
            name="ck_content_source_role",
        ),
        CheckConstraint(
            "citation_order IS NULL OR citation_order >= 1",
            name="ck_content_source_citation_order",
        ),
    )
