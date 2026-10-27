"""Immutable service-owned carousel cards, independent of producer retention."""

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ContentSlide(Base):
    __tablename__ = "content_slide"

    content_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content.content_id", ondelete="CASCADE"),
        primary_key=True, autoincrement=False,
    )
    page: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    eyebrow: Mapped[str] = mapped_column(String(300), nullable=False)
    title_lines: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    body_sentences: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    layout_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Metadata reads must not load multi-megabyte PNGs into feed/detail responses.
    image_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    image_width: Mapped[int] = mapped_column(Integer, nullable=False)
    image_height: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("page BETWEEN 1 AND 10", name="ck_content_slide_page"),
        CheckConstraint("length(image_sha256) = 64", name="ck_content_slide_hash_length"),
        CheckConstraint(
            "length(image_data) BETWEEN 1 AND 20971520", name="ck_content_slide_image_size",
        ),
        CheckConstraint(
            "image_width = 1308 AND image_height = 1632", name="ck_content_slide_dimensions",
        ),
    )
