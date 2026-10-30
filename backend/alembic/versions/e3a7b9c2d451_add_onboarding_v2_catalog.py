"""Add versioned onboarding catalogs without applying the birth-date branch.

Revision ID: e3a7b9c2d451
Revises: c0e5f3b8a124
Create Date: 2026-10-28

Apply this exact revision (or onboarding_v2@head) for onboarding-only upgrades.
The sibling d2f6a9c4e731 revision is deliberately not a dependency.
Downgrade refuses to discard saved v2 answers or user selections.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "e3a7b9c2d451"
down_revision: str | Sequence[str] | None = "c0e5f3b8a124"
branch_labels: str | Sequence[str] | None = ("onboarding_v2",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_tap_id_topic_id", "tap", ["tap_id", "topic_id"])

    op.create_table(
        "entity",
        sa.Column("entity_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_type", sa.String(20), server_default="anime", nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("image_url", sa.String(1000), nullable=True),
        sa.Column("release_year", sa.SmallInteger(), nullable=True),
        sa.Column("media_format", sa.String(30), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("entity_id", name="pk_entity"),
        sa.UniqueConstraint("entity_id", "topic_id", name="uq_entity_id_topic_id"),
        sa.ForeignKeyConstraint(["topic_id"], ["topic.topic_id"], name="fk_entity_topic_id", ondelete="RESTRICT"),
        sa.CheckConstraint("entity_type IN ('anime')", name="ck_entity_type"),
    )
    op.create_index("idx_entity_topic_active_name", "entity", ["topic_id", "is_active", "name", "entity_id"])

    op.create_table(
        "entity_external_id",
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("provider", "external_id", name="pk_entity_external_id"),
        sa.UniqueConstraint("entity_id", "provider", name="uq_entity_external_id_entity_provider"),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.entity_id"], name="fk_entity_external_id_entity_id", ondelete="RESTRICT"),
    )

    op.create_table(
        "preference_tag",
        sa.Column("tag_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("tag_id", name="pk_preference_tag"),
        sa.UniqueConstraint("tag_id", "topic_id", name="uq_preference_tag_id_topic_id"),
        sa.UniqueConstraint("topic_id", "kind", "code", name="uq_preference_tag_topic_kind_code"),
        sa.ForeignKeyConstraint(["topic_id"], ["topic.topic_id"], name="fk_preference_tag_topic_id", ondelete="RESTRICT"),
        sa.CheckConstraint("kind IN ('genre', 'theme')", name="ck_preference_tag_kind"),
    )
    op.create_index("idx_preference_tag_topic_active_sort", "preference_tag", ["topic_id", "is_active", "sort_order", "tag_id"])

    op.create_table(
        "entity_tag",
        sa.Column("entity_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("tag_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("entity_id", "tag_id", name="pk_entity_tag"),
        sa.ForeignKeyConstraint(["entity_id", "topic_id"], ["entity.entity_id", "entity.topic_id"], name="fk_entity_tag_entity_topic", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tag_id", "topic_id"], ["preference_tag.tag_id", "preference_tag.topic_id"], name="fk_entity_tag_tag_topic", ondelete="RESTRICT"),
    )
    op.create_index("idx_entity_tag_tag_topic", "entity_tag", ["tag_id", "topic_id"])

    op.create_table(
        "tap_entity",
        sa.Column("tap_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("entity_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(20), server_default="onboarding", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("tap_id", "entity_id", name="pk_tap_entity"),
        sa.ForeignKeyConstraint(["tap_id", "topic_id"], ["tap.tap_id", "tap.topic_id"], name="fk_tap_entity_tap_topic", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id", "topic_id"], ["entity.entity_id", "entity.topic_id"], name="fk_tap_entity_entity_topic", ondelete="RESTRICT"),
        sa.CheckConstraint("source IN ('onboarding')", name="ck_tap_entity_source"),
    )
    op.create_index("idx_tap_entity_entity_topic", "tap_entity", ["entity_id", "topic_id"])

    op.create_table(
        "tap_preference_tag",
        sa.Column("tap_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("tag_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("tap_id", "tag_id", name="pk_tap_preference_tag"),
        sa.ForeignKeyConstraint(["tap_id", "topic_id"], ["tap.tap_id", "tap.topic_id"], name="fk_tap_preference_tag_tap_topic", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id", "topic_id"], ["preference_tag.tag_id", "preference_tag.topic_id"], name="fk_tap_preference_tag_tag_topic", ondelete="RESTRICT"),
    )
    op.create_index("idx_tap_preference_tag_tag_topic", "tap_preference_tag", ["tag_id", "topic_id"])

    # A server default backfills existing rows and accepts inserts from the
    # old API, which does not know about these two new columns.
    op.add_column("onboarding_answers", sa.Column("schema_version", sa.SmallInteger(), server_default="1", nullable=False))
    op.add_column("onboarding_answers", sa.Column("consumption_type", sa.String(32), nullable=True))
    op.alter_column("onboarding_answers", "q1_choice", existing_type=sa.String(32), nullable=True)
    op.alter_column("onboarding_answers", "q2_choice", existing_type=sa.String(32), nullable=True)
    op.create_check_constraint("ck_onboarding_answers_schema_version", "onboarding_answers", "schema_version IN (1, 2)")
    op.create_check_constraint(
        "ck_onboarding_answers_consumption_type", "onboarding_answers",
        "consumption_type IN ('world', 'relationships', 'ost', 'goods', 'events', 'news')",
    )
    op.create_check_constraint(
        "ck_onboarding_answers_version_shape", "onboarding_answers",
        "(schema_version = 1 AND q1_choice IS NOT NULL AND q2_choice IS NOT NULL "
        "AND consumption_type IS NULL) OR "
        "(schema_version = 2 AND q1_choice IS NULL AND q2_choice IS NULL "
        "AND consumption_type IS NOT NULL)",
    )


def downgrade() -> None:
    # There is no truthful v1 Q1/Q2 mapping for a v2 answer. Fail before any
    # schema mutation rather than invent choices or delete completed answers.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM onboarding_answers WHERE schema_version = 2)
                OR EXISTS (SELECT 1 FROM tap_entity)
                OR EXISTS (SELECT 1 FROM tap_preference_tag)
            THEN
                RAISE EXCEPTION 'Cannot downgrade onboarding_v2 while v2 answers or user selections exist';
            END IF;
        END
        $$;
    """)
    op.drop_constraint("ck_onboarding_answers_version_shape", "onboarding_answers", type_="check")
    op.drop_constraint("ck_onboarding_answers_consumption_type", "onboarding_answers", type_="check")
    op.drop_constraint("ck_onboarding_answers_schema_version", "onboarding_answers", type_="check")
    op.alter_column("onboarding_answers", "q1_choice", existing_type=sa.String(32), nullable=False)
    op.alter_column("onboarding_answers", "q2_choice", existing_type=sa.String(32), nullable=False)
    op.drop_column("onboarding_answers", "consumption_type")
    op.drop_column("onboarding_answers", "schema_version")
    op.drop_table("tap_preference_tag")
    op.drop_table("tap_entity")
    op.drop_table("entity_tag")
    op.drop_table("entity_external_id")
    op.drop_table("preference_tag")
    op.drop_table("entity")
    op.drop_constraint("uq_tap_id_topic_id", "tap", type_="unique")
