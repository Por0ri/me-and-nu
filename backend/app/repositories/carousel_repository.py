"""Public carousel projections, keeping PNG bytes out of ordinary reads."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Content, ContentSource
from app.models.content_slide import ContentSlide


CAROUSEL_METHOD = "carousel_import_v1"


def is_carousel(content) -> bool:
    basis = getattr(content, "ai_judgment_basis", None)
    return isinstance(basis, dict) and basis.get("method") == CAROUSEL_METHOD


def public_content_condition():
    """One visibility rule for detail, feed, saved list, reactions and media.

    Legacy publications keep their existing review behavior. Imported carousels
    fail closed unless all 3..10 contiguous slide records and a future expiry
    exist. PNG decoding and hash verification are the import boundary's job.
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
                Content.judgment_status == "confirmed",
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
