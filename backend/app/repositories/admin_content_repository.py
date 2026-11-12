"""Unrestricted operational reads, for a future server-authorized admin boundary.

Do not wire these into public/consumer routes. Queue membership and data scope
are not decided: omitted filters deliberately mean all stored Content rows.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Content
from app.schemas.admin_content import AdminContentQuery

_ITEM_COLUMNS = (
    Content.content_id, Content.title, Content.judgment_status,
    Content.status, Content.created_at, Content.published_at,
)


async def list_contents(db: AsyncSession, query: AdminContentQuery):
    conditions = []
    if query.q is not None:
        # Treat SQL wildcard characters as literal title text, using bind params.
        conditions.append(Content.title.icontains(query.q, autoescape=True))
    if query.judgment_status is not None:
        conditions.append(Content.judgment_status == query.judgment_status)
    if query.status is not None:
        conditions.append(Content.status == query.status)
    total = await db.scalar(select(func.count()).select_from(Content).where(*conditions))
    result = await db.execute(
        select(*_ITEM_COLUMNS).where(*conditions)
        .order_by(Content.created_at.desc(), Content.content_id.desc())
        .offset((query.page - 1) * query.page_size).limit(query.page_size)
    )
    return list(result.mappings().all()), total


async def get_content(db: AsyncSession, content_id: int):
    result = await db.execute(
        select(*_ITEM_COLUMNS, Content.summary, Content.body)
        .where(Content.content_id == content_id)
    )
    return result.mappings().one_or_none()
