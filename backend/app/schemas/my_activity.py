"""마이페이지 '내 활동' 탭: 좋아요한 콘텐츠 · 최근 본 콘텐츠 목록 응답입니다.

카드 칸은 저장 목록(API-041)과 같아서 프론트가 SAVE 탭 카드를 그대로 다시 쓸 수 있습니다.
"""

from datetime import datetime

from pydantic import Field

from app.schemas.common import CamelModel


class ActivityContentCard(CamelModel):
    content_id: int
    title: str
    topic_id: int
    summary: str | None = None
    image_url: str | None = None
    source_name: str | None = None
    eyebrow: str | None = None
    production_type: str | None = None
    published_at: datetime | None = None
    subtopic_names: list[str] = Field(default_factory=list)


class LikedContentItem(ActivityContentCard):
    liked_at: datetime


class ViewedContentItem(ActivityContentCard):
    viewed_at: datetime


class LikedContentsResponse(CamelModel):
    items: list[LikedContentItem]
    next_cursor: str | None = None


class RecentViewsResponse(CamelModel):
    items: list[ViewedContentItem]
    next_cursor: str | None = None
