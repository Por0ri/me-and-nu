"""알림함 (API-107 목록, API-108 미확인 개수, API-109 읽음 처리)."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

NOTIFICATION_TYPES = ("topic_new_post", "creator_new_post", "system_notice", "follow", "direct_message")


class Notification(Base):
    __tablename__ = "notification"

    notification_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="CASCADE"), nullable=False
    )
    # topic_new_post·creator_new_post는 "새 게시글·공지" 탭, follow·direct_message는 "팔로우·메시지" 탭
    notification_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("topic.topic_id", ondelete="SET NULL"))
    content_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("content.content_id", ondelete="CASCADE")
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "notification_type IN (" + ", ".join(f"'{t}'" for t in NOTIFICATION_TYPES) + ")",
            name="ck_notification_type",
        ),
        Index("idx_notification_user_created_at", "user_id", created_at.desc()),
        # 같은 글로 같은 알림을 두 번 만들지 않는다.
        Index(
            "uq_notification_user_type_content",
            "user_id",
            "notification_type",
            "content_id",
            unique=True,
            postgresql_where=content_id.is_not(None),
        ),
    )
