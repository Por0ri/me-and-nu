"""토론방 표.

회원이 올린 주제 하나가 debate 한 줄이다. 투표 중(voting)에 개설 투표를 받아 기준 표 수를 채우면
진행 중(open)인 토론방이 되고, 방 유지 시간이 지나면 종료(closed)된다. 투표 기간 안에 기준을 못 채우면
소멸(expired)한다.

응답에 쓰는 상태는 저장된 status와 지금 시각으로 계산한다. 그래서 정리 작업이 늦게 돌아도 마감 시각이
지난 주제는 끝난 것으로 보인다. 정리 작업은 저장된 status를 따라잡고 끝난 방의 채팅을 지운다.

개설 기준 표 수·기간·투표권 같은 규칙 값은 설정(app.core.config)에 있다. 여기 있는 글자 수 상한은
칼럼 크기와 같아서 바꾸려면 migration이 필요하다. 선택지 이름만 입력 상한(SIDE_LABEL_INPUT_MAX)을
칼럼보다 작게 둔다. 설계: docs/DEBATE_BACKEND_DESIGN_2026-11-19.md
"""

from datetime import datetime

from sqlalchemy import (
    JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
    SmallInteger, String, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.recommendation import DATA_SOURCES

# PostgreSQL에서는 BIGSERIAL·JSONB, 테스트용 SQLite에서는 자동 번호가 붙는 INTEGER·JSON
_ID = BigInteger().with_variant(Integer(), "sqlite")
_JSON = JSONB().with_variant(JSON(), "sqlite")

DEBATE_STATUSES = ("voting", "open", "closed", "expired")
DEBATE_SIDES = ("a", "b")

TITLE_MAX = 60
# 카드의 더보기를 없애면서 100자로 줄였다 (2026-11-25 결정, 카드에는 3줄까지 보이고 넘으면 '…'). 200자였던 칼럼과 그보다 긴 옛 설명은
# 7f843b19debf가 줄이고 자른다.
DESCRIPTION_MAX = 100
SIDE_LABEL_MAX = 20
# 선택지 이름은 칼럼보다 짧게 받는다. 2026-11-25 디자인대로 8자 (2026-11-24에는 12자).
# 입력 검사만 줄였으므로 migration은 없고, 이미 있는 8자 넘는 이름은 그대로 둔다.
SIDE_LABEL_INPUT_MAX = 8
MESSAGE_MAX = 300
# 관련 작품은 1~2개 필수 (2026-11-25 디자인, 전에는 0~3개). 이미 있는 0개 · 3개 주제는 그대로 둔다.
WORKS_MAX = 2
SEARCH_TEXT_MAX = 50


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Debate(Base):
    """주제 후보부터 지난 토론방까지 한 줄로 관리한다."""

    __tablename__ = "debate"

    debate_id: Mapped[int] = mapped_column(_ID, primary_key=True, autoincrement=True)
    # 분야. 연결한 작품도 같은 분야여야 한다 (debate_work 참고).
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topic.topic_id", ondelete="RESTRICT"), nullable=False
    )
    # 탈퇴 회원을 지울 때 NULL로 바꾸고 토론 기록은 남긴다.
    author_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(TITLE_MAX), nullable=False)
    # 간단한 설명 (선택). 카드에 전부 보인다.
    description: Mapped[str | None] = mapped_column(String(DESCRIPTION_MAX))
    # 'A vs B'의 양쪽 이름. 예: 나루토 / 사스케, 된다 / 못 된다
    side_a_label: Mapped[str] = mapped_column(String(SIDE_LABEL_MAX), nullable=False)
    side_b_label: Mapped[str] = mapped_column(String(SIDE_LABEL_MAX), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="voting", server_default="voting"
    )
    # 아래 숫자들은 같은 트랜잭션에서 이 줄을 잠그고 함께 바꾼다.
    vote_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    side_a_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    side_b_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 채팅을 지운 뒤에도 남는 기록용 숫자
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    voting_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # debate_work가 (주제, 분야)를 함께 가리켜 다른 분야 작품이 붙지 않게 한다.
        UniqueConstraint("debate_id", "topic_id", name="uq_debate_id_topic_id"),
        CheckConstraint(_one_of("status", DEBATE_STATUSES), name="ck_debate_status"),
        CheckConstraint(
            "vote_count >= 0 AND side_a_count >= 0 AND side_b_count >= 0 AND message_count >= 0",
            name="ck_debate_counts",
        ),
        CheckConstraint(
            "title <> '' AND side_a_label <> '' AND side_b_label <> '' AND side_a_label <> side_b_label",
            name="ck_debate_text",
        ),
        # 열린 적 없는 주제(voting, expired)에는 방 시각이 없고, 열린 방(open, closed)에는 반드시 있다.
        # closes_at IS NOT NULL을 따로 적는다. NULL과의 비교는 '모름'이 되고 CHECK는 '모름'을 통과시킨다.
        CheckConstraint(
            "(status IN ('voting', 'expired') AND opened_at IS NULL AND closes_at IS NULL)"
            " OR (status IN ('open', 'closed') AND opened_at IS NOT NULL AND closes_at IS NOT NULL"
            " AND closes_at > opened_at)",
            name="ck_debate_room_period",
        ),
        Index("idx_debate_status_voting_ends", "status", "voting_ends_at"),
        Index("idx_debate_status_closes", "status", "closes_at"),
        Index("idx_debate_author_status", "author_user_id", "status"),
    )


class DebateWork(Base):
    """주제에 연결한 작품. 작품 목록(entity)에서 고르고, 주제와 같은 분야만 연결된다."""

    __tablename__ = "debate_work"

    debate_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 화면에 보일 순서 (0부터). 개수 상한(WORKS_MAX)은 서비스에서 지킨다.
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["debate_id", "topic_id"], ["debate.debate_id", "debate.topic_id"],
            name="fk_debate_work_debate_topic", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["entity_id", "topic_id"], ["entity.entity_id", "entity.topic_id"],
            name="fk_debate_work_entity_topic", ondelete="RESTRICT",
        ),
        UniqueConstraint("debate_id", "position", name="uq_debate_work_debate_position"),
        CheckConstraint("position >= 0", name="ck_debate_work_position"),
        Index("idx_debate_work_entity_topic", "entity_id", "topic_id"),
    )


class DebateVote(Base):
    """개설 투표. 한 주제에 1인 1표."""

    __tablename__ = "debate_vote"

    debate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("debate.debate_id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_debate_vote_user_created", "user_id", "created_at"),
    )


class DebateVoteTicket(Base):
    """회원별 투표권. 줄이 없으면 가득 찬 것으로 본다.

    충전은 매시간 도는 작업 없이 조회할 때 refilled_at부터 계산한다. 이 줄은 그 회원의
    주제 올리기·투표·투표 취소를 한 번에 하나씩 처리하는 잠금으로도 쓴다.
    """

    __tablename__ = "debate_vote_ticket"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    balance: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    refilled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_debate_vote_ticket_balance"),
    )


class DebateSide(Base):
    """토론방에서 고른 진영. 채팅과는 별개이고 언제든 바꿀 수 있다. 방이 끝나는 순간의 인원으로 승패를 정한다."""

    __tablename__ = "debate_side"

    debate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("debate.debate_id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    side: Mapped[str] = mapped_column(String(1), nullable=False)
    chosen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(_one_of("side", DEBATE_SIDES), name="ck_debate_side_side"),
    )


class DebateMember(Base):
    """토론방 참여. 카카오톡 오픈채팅처럼 참여한 순간부터의 채팅만 보인다. 방을 나가면 지워지고(고른 진영도
    함께 취소), 다시 참여하면 그 순간부터 다시 시작한다. 채팅·진영 고르기·실시간 연결은 참여한 사람만 할 수 있다."""

    __tablename__ = "debate_member"

    debate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("debate.debate_id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    # 참여한 순간 그 방의 마지막 채팅 번호. 이 번호보다 뒤의 채팅만 보여 준다 (채팅이 없었으면 0).
    history_after_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("history_after_message_id >= 0", name="ck_debate_member_history"),
        # 내가 참여 중인 방 목록
        Index("idx_debate_member_user_joined", "user_id", "joined_at"),
    )


class DebateMessage(Base):
    """토론방 채팅. 방이 끝나면 정리 작업이 지운다 (채팅 내역은 남기지 않는다).

    번호(message_id)는 실시간 스트림의 이벤트 번호로도 쓴다. 같은 방 메시지는 debate 줄을 잠그고
    저장하므로 번호 순서와 커밋 순서가 같다.
    """

    __tablename__ = "debate_message"

    message_id: Mapped[int] = mapped_column(_ID, primary_key=True, autoincrement=True)
    debate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("debate.debate_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="CASCADE"), nullable=False
    )
    # 보낸 순간의 진영. 진영을 고르지 않고도 채팅할 수 있어서 비어 있을 수 있다.
    # 나중에 진영을 바꿔도 이미 보낸 메시지의 진영은 그대로다.
    side: Mapped[str | None] = mapped_column(String(1))
    body: Mapped[str] = mapped_column(String(MESSAGE_MAX), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # NULL(진영 없음)은 CHECK를 통과한다. 값이 있으면 a·b만 된다.
        CheckConstraint(_one_of("side", DEBATE_SIDES), name="ck_debate_message_side"),
        CheckConstraint("body <> ''", name="ck_debate_message_body"),
        Index("idx_debate_message_debate_message", "debate_id", "message_id"),
        # 1인 메시지 속도 제한 (최근 N초에 보낸 개수)
        Index("idx_debate_message_user_debate_created", "user_id", "debate_id", "created_at"),
    )


class DebateNotification(Base):
    """토론 개설 알림과 토론의 연결.

    notification 표에 칼럼을 더하면 migration이 적용되기 전 서버에서 알림함 조회가 깨지므로,
    연결은 이 표에 따로 둔다. 같은 토론으로 같은 회원에게 알림을 두 번 만들지 않는다.
    """

    __tablename__ = "debate_notification"

    notification_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("notification.notification_id", ondelete="CASCADE"),
        primary_key=True, autoincrement=False,
    )
    debate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("debate.debate_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.user_id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("debate_id", "user_id", name="uq_debate_notification_debate_user"),
    )


class DebateSearchLog(Base):
    """실제로 실행된 토론 검색 한 번에 한 줄. 자유 태그가 필요한지 판단하려고 남긴다.

    누가 검색했는지는 남기지 않는다. matched_entity_ids가 비어 있으면 작품 목록에 없는 말이고,
    result_count가 0이면 그 말로 찾을 수 있는 토론이 없었다는 뜻이다.
    """

    __tablename__ = "debate_search_log"

    search_log_id: Mapped[int] = mapped_column(_ID, primary_key=True, autoincrement=True)
    topic_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("topic.topic_id", ondelete="SET NULL")
    )
    search_text: Mapped[str] = mapped_column(String(SEARCH_TEXT_MAX), nullable=False)
    picked_entity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("entity.entity_id", ondelete="SET NULL")
    )
    matched_entity_ids: Mapped[list[int]] = mapped_column(_JSON, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False)
    searched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    data_source: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        CheckConstraint("result_count >= 0", name="ck_debate_search_log_result_count"),
        CheckConstraint(_one_of("data_source", DATA_SOURCES), name="ck_debate_search_log_data_source"),
        Index("idx_debate_search_log_searched", "searched_at"),
    )
