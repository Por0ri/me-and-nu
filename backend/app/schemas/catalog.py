from datetime import datetime

from pydantic import ConfigDict, Field, field_validator

from app.schemas.common import CamelModel


class TopicItem(CamelModel):
    id: int
    code: str
    name: str


class TopicsResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"topics": [{"id": 1, "code": "movie", "name": "영화"}]}})
    topics: list[TopicItem]


class SubtopicItem(CamelModel):
    subtopic_id: int
    topic_id: int
    name: str
    parent_subtopic_id: int | None


class SubtopicsResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"items": [{"subtopicId": 12, "topicId": 1, "name": "영화 정보", "parentSubtopicId": None}], "nextCursor": None}})
    items: list[SubtopicItem]
    next_cursor: str | None = None


class MyTopicItem(TopicItem):
    subtopic_ids: list[int]
    status: str
    deleted_at: datetime | None


class MyTopicsResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"topics": [{"id": 1, "code": "movie", "name": "영화", "subtopicIds": [12], "status": "active", "deletedAt": None}]}})
    topics: list[MyTopicItem]


class CreateMyTopicRequest(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"topicId": 1, "subtopicIds": [12]}})
    topic_id: int = Field(gt=0)
    subtopic_ids: list[int] = Field(min_length=1)

    @field_validator("subtopic_ids")
    @classmethod
    def unique_subtopic_ids(cls, ids: list[int]) -> list[int]:
        if any(value <= 0 for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("subtopicIds에는 중복 없는 양의 정수 ID가 필요합니다.")
        return ids


class MyTopicResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"topic": {"id": 1, "code": "movie", "name": "영화", "subtopicIds": [12], "status": "active", "deletedAt": None}}})
    topic: MyTopicItem


class MySubtopicItem(CamelModel):
    subtopic_id: int
    name: str
    order: int
    subscribed_at: datetime


class MySubtopicsResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"items": [{"subtopicId": 12, "name": "영화 정보", "order": 1, "subscribedAt": "2026-09-17T03:00:00Z"}], "orderingBasis": "subscription_time"}})
    items: list[MySubtopicItem]
    ordering_basis: str = "subscription_time"


class SubtopicSubscriptionResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"topicId": 1, "subtopicId": 12, "subscribed": True}})
    topic_id: int
    subtopic_id: int
    subscribed: bool = True
