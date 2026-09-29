"""알림함 (API-107·108·109)."""

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict

from app.schemas.common import CamelModel


class NotificationItem(CamelModel):
    notification_id: int
    type: str
    title: str
    body: str
    topic_id: int | None = None
    content_id: int | None = None
    image_url: str | None = None
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime


class NotificationsResponse(CamelModel):
    model_config = ConfigDict(json_schema_extra={"example": {"items": [{
        "notificationId": 701, "type": "topic_new_post", "title": "영화 분야 새 글",
        "body": "전장의 중심에 선 한 사람", "topicId": 1, "contentId": 37, "imageUrl": None,
        "isRead": False, "readAt": None, "createdAt": "2026-09-28T09:00:00Z",
    }], "nextCursor": None}})
    items: list[NotificationItem]
    next_cursor: str | None = None


class UnreadCountResponse(CamelModel):
    unread_count: int


class NotificationReadRequest(CamelModel):
    is_read: Literal[True]


class NotificationReadResponse(CamelModel):
    notification_id: int
    is_read: bool
    read_at: datetime | None
