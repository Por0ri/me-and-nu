"""Store service-owned carousel slides without changing the birth-date branch.

Revision ID: f4b8c1d9a620
Revises: e3a7b9c2d451
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "f4b8c1d9a620"
down_revision: str | Sequence[str] | None = "e3a7b9c2d451"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("content", sa.Column("feed_valid_until", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "content_slide",
        sa.Column("content_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("page", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("eyebrow", sa.String(300), nullable=False),
        sa.Column("title_lines", postgresql.JSONB(), nullable=False),
        sa.Column("body_sentences", postgresql.JSONB(), nullable=False),
        sa.Column("layout_json", postgresql.JSONB(), nullable=False),
        sa.Column("image_data", sa.LargeBinary(), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("image_width", sa.Integer(), nullable=False),
        sa.Column("image_height", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("content_id", "page", name="pk_content_slide"),
        sa.ForeignKeyConstraint(
            ["content_id"], ["content.content_id"],
            name="fk_content_slide_content_id", ondelete="CASCADE",
        ),
        sa.CheckConstraint("page BETWEEN 1 AND 10", name="ck_content_slide_page"),
        sa.CheckConstraint("length(image_sha256) = 64", name="ck_content_slide_hash_length"),
        sa.CheckConstraint(
            "length(image_data) BETWEEN 1 AND 20971520", name="ck_content_slide_image_size",
        ),
        sa.CheckConstraint(
            "image_width = 1308 AND image_height = 1632", name="ck_content_slide_dimensions",
        ),
    )


def downgrade() -> None:
    # Block concurrent ingestion before checking for data. SQL-side guards also
    # remain effective in generated offline downgrade scripts.
    op.execute("LOCK TABLE content_slide, content IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM content_slide)
                OR EXISTS (SELECT 1 FROM content WHERE feed_valid_until IS NOT NULL)
            THEN
                RAISE EXCEPTION 'Refusing to remove stored carousel cards or visibility deadlines';
            END IF;
        END
        $$;
    """)
    op.drop_table("content_slide")
    op.drop_column("content", "feed_valid_until")
