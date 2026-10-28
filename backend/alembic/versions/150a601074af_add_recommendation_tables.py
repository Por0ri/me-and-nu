"""Add content-work links, ranked feed results and the activity event log.

Revision ID: 150a601074af
Revises: f4b8c1d9a620
Create Date: 2026-10-28

Downgrade refuses to discard stored links, recommendations or activity events.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "150a601074af"
down_revision: str | Sequence[str] | None = "f4b8c1d9a620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LINK_METHODS = ("carousel", "agent", "manual")
EVENT_TYPES = (
    "impression", "open",
    "like", "like_cancel", "bookmark", "bookmark_cancel",
    "preference_positive", "preference_negative", "preference_cancel",
    "card_swipe", "share", "spoiler_reveal", "watched",
)
DATA_SOURCES = ("production", "local_demo", "automated_test", "imported")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "content_entity_link",
        sa.Column("content_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("entity_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("link_method", sa.String(20), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("content_id", "entity_id", name="pk_content_entity_link"),
        sa.ForeignKeyConstraint(["content_id"], ["content.content_id"], name="fk_content_entity_link_content_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id", "topic_id"], ["entity.entity_id", "entity.topic_id"], name="fk_content_entity_link_entity_topic", ondelete="RESTRICT"),
        sa.UniqueConstraint("content_id", "sort_order", name="uq_content_entity_link_content_sort"),
        sa.CheckConstraint(_one_of("link_method", LINK_METHODS), name="ck_content_entity_link_method"),
        sa.CheckConstraint("sort_order >= 1", name="ck_content_entity_link_sort_order"),
    )
    op.create_index("idx_content_entity_link_entity_topic", "content_entity_link", ["entity_id", "topic_id"])

    op.create_table(
        "recommendation_round",
        sa.Column("recommendation_round_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tap_id", sa.BigInteger(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("algorithm_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("recommendation_round_id", name="pk_recommendation_round"),
        sa.ForeignKeyConstraint(["tap_id"], ["tap.tap_id"], name="fk_recommendation_round_tap_id", ondelete="CASCADE"),
        sa.UniqueConstraint("tap_id", "service_date", "algorithm_version", name="uq_recommendation_round_tap_date_version"),
    )

    op.create_table(
        "recommendation_item",
        sa.Column("recommendation_item_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("recommendation_round_id", sa.BigInteger(), nullable=False),
        sa.Column("content_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Double(), nullable=True),
        sa.Column("recommend_reason", postgresql.JSONB(), nullable=True),
        sa.Column("impressed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("recommendation_item_id", name="pk_recommendation_item"),
        sa.ForeignKeyConstraint(["recommendation_round_id"], ["recommendation_round.recommendation_round_id"], name="fk_recommendation_item_recommendation_round_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_id"], ["content.content_id"], name="fk_recommendation_item_content_id", ondelete="CASCADE"),
        sa.UniqueConstraint("recommendation_round_id", "rank", name="uq_recommendation_item_round_rank"),
        sa.UniqueConstraint("recommendation_round_id", "content_id", name="uq_recommendation_item_round_content"),
        sa.CheckConstraint("rank >= 1", name="ck_recommendation_item_rank"),
    )
    op.create_index("idx_recommendation_item_content", "recommendation_item", ["content_id"])

    op.create_table(
        "user_event",
        sa.Column("user_event_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tap_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("content_id", sa.BigInteger(), nullable=False),
        sa.Column("recommendation_item_id", sa.BigInteger(), nullable=True),
        sa.Column("event_value", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("data_source", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("user_event_id", name="pk_user_event"),
        sa.UniqueConstraint("client_event_id", name="uq_user_event_client_event_id"),
        sa.ForeignKeyConstraint(["user_id"], ["user_account.user_id"], name="fk_user_event_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tap_id", "user_id"], ["tap.tap_id", "tap.user_id"], name="fk_user_event_tap_owner", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_id"], ["content.content_id"], name="fk_user_event_content_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recommendation_item_id"], ["recommendation_item.recommendation_item_id"], name="fk_user_event_recommendation_item_id", ondelete="SET NULL"),
        sa.CheckConstraint(_one_of("event_type", EVENT_TYPES), name="ck_user_event_type"),
        sa.CheckConstraint(_one_of("data_source", DATA_SOURCES), name="ck_user_event_data_source"),
    )
    op.create_index("idx_user_event_user_occurred", "user_event", ["user_id", "occurred_at"])
    op.create_index("idx_user_event_tap_type_received", "user_event", ["tap_id", "event_type", "received_at"])
    op.create_index("idx_user_event_content_type", "user_event", ["content_id", "event_type"])
    op.create_index("idx_user_event_recommendation_item", "user_event", ["recommendation_item_id"])


def downgrade() -> None:
    # Links, recommendation results and activity events are learning records.
    # Block concurrent writes, then refuse before any schema mutation.
    op.execute(
        "LOCK TABLE user_event, recommendation_item, recommendation_round, content_entity_link "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM user_event)
                OR EXISTS (SELECT 1 FROM recommendation_item)
                OR EXISTS (SELECT 1 FROM recommendation_round)
                OR EXISTS (SELECT 1 FROM content_entity_link)
            THEN
                RAISE EXCEPTION 'Refusing to discard stored recommendation links, results or activity events';
            END IF;
        END
        $$;
    """)
    op.drop_table("user_event")
    op.drop_table("recommendation_item")
    op.drop_table("recommendation_round")
    op.drop_table("content_entity_link")
