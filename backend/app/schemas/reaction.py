from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from app.schemas.common import CamelModel


class TopicAction(CamelModel):
    topic_id: int = Field(gt=0)


class PreferenceRequest(TopicAction):
    value: Literal["positive", "negative"]


class PreferenceResponse(PreferenceRequest):
    model_config = ConfigDict(json_schema_extra={"example": {"contentId": 301, "topicId": 1, "value": "positive"}})
    content_id: int


class LikeResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"contentId": 301, "liked": True}})
    content_id: int
    liked: bool = True


class BookmarkResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"savedItemId": 61, "contentId": 301, "topicId": 1, "tags": ["영화 정보"], "resurfaceEnabled": True, "savedAt": "2026-09-17T03:00:00Z"}})
    saved_item_id: int
    content_id: int
    topic_id: int
    tags: list[str]
    resurface_enabled: bool
    saved_at: datetime
