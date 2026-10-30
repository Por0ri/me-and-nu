"""Public carousel projections, keeping PNG bytes out of ordinary reads."""

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Content, ContentSource
from app.models.content_slide import ContentSlide


CAROUSEL_METHOD = "carousel_import_v1"


def local_test_database(database_url: str) -> str | None:
    """Recognize a bounded loopback test target, never a URL override/tunnel host."""
    try:
        url = make_url(database_url)
    except (ArgumentError, TypeError, ValueError):
        return None
    name = url.database or ""
    if (url.get_backend_name() != "postgresql"
            or url.host not in {"localhost", "127.0.0.1", "::1"} or url.query
            or not (name.startswith("test_") or name.endswith(("_test", "_e2e")))):
        return None
    return name


def _carousel_review_condition():
    from app.core.config import settings

    basis = Content.ai_judgment_basis
    preview = func.coalesce(basis["preview"].as_boolean(), False)
    database = local_test_database(settings.database_url)
    preview_allowed = false()
    if database and settings.event_data_source != "production":
        preview_allowed = and_(
            preview,
            Content.judgment_status == "needs_review",
            basis["local_test_preview"]["database"].as_string() == database,
            basis["local_test_preview"]["quality_approval"].as_string() == "not_reviewed",
        )
    return or_(and_(~preview, Content.judgment_status == "confirmed"), preview_allowed)


def is_carousel(content) -> bool:
    basis = getattr(content, "ai_judgment_basis", None)
    return isinstance(basis, dict) and basis.get("method") == CAROUSEL_METHOD


def public_content_condition():
    """One visibility rule for detail, feed, saved list, reactions and media.

    Legacy publications keep their existing review behavior. Imported carousels
    require approval, except for explicitly marked previews in a matching local
    test service. Both paths require 3..10 contiguous slides and a future expiry.
    PNG decoding and hash verification are the import boundary's job.
    """
    count = (
        select(func.count(ContentSlide.page))
        .where(ContentSlide.content_id == Content.content_id)
        .correlate(Content).scalar_subquery()
    )
    last_page = (
        select(func.max(ContentSlide.page))
        .where(ContentSlide.content_id == Content.content_id)
        .correlate(Content).scalar_subquery()
    )
    imported = func.coalesce(Content.ai_judgment_basis["method"].as_string(), "") == CAROUSEL_METHOD
    return and_(
        Content.status == "active",
        Content.deleted_at.is_(None),
        or_(Content.feed_valid_until.is_(None), Content.feed_valid_until > func.now()),
        or_(
            ~imported,
            and_(
                _carousel_review_condition(),
                Content.feed_valid_until.is_not(None),
                count.between(3, 10),
                last_page == count,
                select(ContentSource.content_source_id)
                .where(ContentSource.content_id == Content.content_id)
                .correlate(Content).exists(),
            ),
        ),
    )


async def list_slides(db: AsyncSession, content_id: int) -> list[ContentSlide]:
    # image_data is deferred on the ORM model, so these reads fetch metadata only.
    return list((await db.scalars(
        select(ContentSlide)
        .where(ContentSlide.content_id == content_id)
        .order_by(ContentSlide.page)
    )).all())


async def get_slide_image(db: AsyncSession, content_id: int, page: int) -> bytes | None:
    return await db.scalar(
        select(ContentSlide.image_data)
        .join(Content, Content.content_id == ContentSlide.content_id)
        .where(
            ContentSlide.content_id == content_id, ContentSlide.page == page,
            public_content_condition(),
            select(ContentSource.content_source_id)
            .where(ContentSource.content_id == Content.content_id)
            .correlate(Content).exists(),
        )
    )
