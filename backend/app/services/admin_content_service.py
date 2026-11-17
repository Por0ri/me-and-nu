"""Read-only foundation, intentionally not registered with FastAPI.

Authorization is NOT implemented here. A future route must enforce trusted
server-side admin permission before calling any of these operations.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.repositories import admin_content_repository as repo
from app.repositories.content_repository import list_content_sources
from app.schemas.admin_content import (
    AdminContentDetail, AdminContentItem, AdminContentListResponse, AdminContentQuery,
)
from app.schemas.feed import ContentSourceResponse


async def list_contents(db: AsyncSession, query: AdminContentQuery) -> AdminContentListResponse:
    rows, total = await repo.list_contents(db, query)
    return AdminContentListResponse(
        items=[AdminContentItem.model_validate(row) for row in rows],
        page=query.page, page_size=query.page_size, total=total,
    )


async def get_content(db: AsyncSession, content_id: int) -> AdminContentDetail:
    row = await repo.get_content(db, content_id)
    if row is None:
        raise ApiError(404, "CONTENT_NOT_FOUND", "콘텐츠를 찾을 수 없습니다.")
    sources = await list_content_sources(db, content_id)
    return AdminContentDetail(
        **row,
        sources=[ContentSourceResponse(
            url=source.source_url, title=source.source_title,
            publisher=site.media_name if site else None,
            role=source.source_role, citation_order=source.citation_order,
        ) for source, site in sources],
    )
