"""알림함 (API-107·108·109)."""

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict

from app.schemas.common import CamelModel


class NotificationItem(CamelModel):
    notification_id: int
    # topic_new_post · recommended_post 는 topicId · contentId · imageUrl(글 대표 그림)이 있고, 누르면 그 글로 간다.
    type: Literal["system_notice", "topic_new_post", "recommended_post"]
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
        "notificationId": 701, "type": "system_notice", "title": "서비스 운영 공지",
        "body": "me;nu 서비스 운영에 관한 안내입니다.", "topicId": None, "contentId": None, "imageUrl": None,
        "isRead": False, "readAt": None, "createdAt": "2026-09-28T09:00:00Z",
    }, {
        "notificationId": 702, "type": "topic_new_post", "title": "‘체인소 맨’ 새 글",
        "body": "체인소 맨 레제편 굿즈 모음", "topicId": 1, "contentId": 501,
        "imageUrl": "/api/v1/contents/501/slides/1/image?topicId=1",
        "isRead": False, "readAt": None, "createdAt": "2026-09-29T00:00:00Z",
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
