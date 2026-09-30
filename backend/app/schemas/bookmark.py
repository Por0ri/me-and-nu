"""API-041 saved content listing."""

from datetime import datetime

from pydantic import ConfigDict, Field

from app.schemas.common import CamelModel


class SavedBookmarkItem(CamelModel):
    saved_item_id: int
    content_id: int
    title: str
    topic_id: int
    tags: list[str] = Field(default_factory=list)
    resurface_enabled: bool
    summary: str | None = None
    image_url: str | None = None
    source_name: str | None = None
    production_type: str | None = None
    published_at: datetime | None = None
    subtopic_names: list[str] = Field(default_factory=list)


class SavedBookmarksResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "items": [{
            "savedItemId": 61,
            "contentId": 301,
            "title": "로컬 샘플",
            "topicId": 1,
            "tags": ["영화 정보"],
            "resurfaceEnabled": True,
            "summary": "허용된 발췌",
            "imageUrl": None,
            "sourceName": "MeNu 로컬 테스트",
        }],
        "nextCursor": None,
    }})
    items: list[SavedBookmarkItem]
    next_cursor: str | None = None
