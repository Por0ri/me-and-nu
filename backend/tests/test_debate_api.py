"""토론방 투표 흐름 API (설계 D-01~08, D-13~17)와 토론 개설 알림.

일회용 SQLite(외래 키 켬)에 회원·분야·작품·알림·토론 표를 만들고, 개발용 가입·로그인으로 회원을 여러 명 만들어
각자의 TestClient(쿠키)로 움직인다. 개설 기준은 3표로 줄이고, 시간은 debate_service.utcnow를 바꿔서 흘린다.
"""

import itertools
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, Integer, MetaData, create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.session import get_db
from app.main import create_app
from app.models import (
    AdminAccount, AgentRun, AuthSession, ConsentHistory, Content, CreatorChannel, Debate, DebateMember, DebateMessage,
    DebateNotification, DebateSearchLog, DebateSide, DebateVote, DebateVoteTicket, DebateWork, Draft, Entity, Notice,
    Notification, NotificationSetting, OAuthAttempt, OnboardingAnswersRecord, Policy, Subtopic, Tap, TapTopic, Topic,
    TopicCluster, UserAccount,
)
from app.services import debate_service
from test_oauth_postgres_flow import AsyncSqliteSession

START = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)
# 알림 표가 글(content)과 관리자 공지(notice)를 가리켜서 글 쪽 표(초안·채널·작품 묶음·에이전트 실행)와 공지 표도 만든다.
MODELS = (
    UserAccount, AdminAccount, AuthSession, OAuthAttempt, Topic, Subtopic, Policy, ConsentHistory, NotificationSetting,
    Tap, TapTopic, OnboardingAnswersRecord, CreatorChannel, AgentRun, TopicCluster, Draft, Content, Entity, Notice,
    Notification,
    Debate, DebateWork, DebateVote, DebateVoteTicket, DebateSide, DebateMember, DebateMessage, DebateNotification, DebateSearchLog,
)


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kw):
    return "JSON"


class DebateSqliteSession(AsyncSqliteSession):
    """개발용 가입(register)이 쓰는 refresh를 더한 테스트용 세션."""

    async def refresh(self, row, *args, **kwargs):
        self.session.refresh(row, *args, **kwargs)
PASSWORD = "Debate!test-password-1"


@pytest.fixture
def debate_api(monkeypatch):
    for name, value in {
        "session_backend": "postgres", "redis_url": None, "enable_dev_api": True, "enable_dev_auth_bypass": False,
        "session_cookie_secure": False, "debate_enabled": True, "debate_vote_threshold": 3,
    }.items():
        monkeypatch.setattr(settings, name, value)
    clock = SimpleNamespace(now=START)
    monkeypatch.setattr(debate_service, "utcnow", lambda: clock.now)

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def use_explicit_transactions(connection, _):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin_transaction(connection):
        connection.exec_driver_sql("BEGIN")

    metadata = MetaData()
    for model in MODELS:
        table = model.__table__.to_metadata(metadata)
        for column in table.primary_key.columns:
            if len(table.primary_key.columns) == 1 and isinstance(column.type, Integer):
                column.type = Integer()
    metadata.tables["creator_channel"].c.style_card.type = JSON()
    metadata.tables["entity"].c.aliases.type = JSON()
    metadata.tables["entity"].c.aliases.server_default = None  # '[]'::jsonb 는 PostgreSQL 문법이라 빼고 직접 넣는다
    metadata.tables["debate_search_log"].c.matched_entity_ids.type = JSON()
    metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            Topic(topic_id=1, topic_code="anime", topic_name="애니메이션", is_active=True),
            Topic(topic_id=2, topic_code="movie", topic_name="영화", is_active=True),
        ])
        for policy_type, required in (("terms", True), ("privacy", True), ("marketing", False)):
            session.add(Policy(policy_type=policy_type, policy_version="v1", title=policy_type, content="테스트 정책",
                               is_required=required, is_active=True, effective_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        session.add_all([
            Entity(entity_id=100, topic_id=1, entity_type="anime", name="나루토", aliases=["NARUTO"]),
            Entity(entity_id=101, topic_id=1, entity_type="anime", name="원피스", aliases=["ONE PIECE"]),
            Entity(entity_id=102, topic_id=1, entity_type="anime", name="주술회전", aliases=["JUJUTSU KAISEN"]),
            Entity(entity_id=200, topic_id=2, entity_type="anime", name="다른 분야 작품", aliases=[]),
        ])
        session.commit()

    failure = SimpleNamespace(enabled=False, answers_insert=False)

    async def database():
        with Session(engine, expire_on_commit=False) as session:
            yield DebateSqliteSession(session, failure)

    api = create_app(enable_dev_api=True, enable_debate=True)
    api.dependency_overrides[get_db] = database
    counter = itertools.count(1)
    with ExitStack() as stack:
        def member():
            index = next(counter)
            client = stack.enter_context(TestClient(api, follow_redirects=False, raise_server_exceptions=False))
            csrf = client.get("/api/v1/auth/session").json()["csrfToken"]
            account = {"email": f"member{index}@example.com", "password": PASSWORD}
            response = client.post("/api/v1/dev/auth/register", json={**account, "nickname": f"토론회원{index}"}, headers={"X-CSRF-Token": csrf})
            assert response.status_code == 201, response.text
            assert client.post("/api/v1/dev/auth/login", json=account, headers={"X-CSRF-Token": csrf}).status_code == 200
            csrf = client.get("/api/v1/auth/session").json()["csrfToken"]
            response = client.post("/api/v1/onboarding/complete", headers={"X-CSRF-Token": csrf}, json={
                "nickname": f"토론회원{index}", "accountType": "user", "topicId": 1, "answers": {"q1": "world", "q2": "releases"},
                "consents": [{"type": "terms", "policyVersion": "v1", "agreed": True},
                             {"type": "privacy", "policyVersion": "v1", "agreed": True},
                             {"type": "marketing", "policyVersion": "v1", "agreed": False}],
            })
            assert response.status_code == 201, response.text
            session = client.get("/api/v1/auth/session").json()
            return SimpleNamespace(client=client, csrf=session["csrfToken"], user_id=session["user"]["id"])

        yield SimpleNamespace(api=api, engine=engine, clock=clock, member=member)
    engine.dispose()


def call(member, method, path, **kwargs):
    headers = {"X-CSRF-Token": member.csrf} if method != "GET" else {}
    return member.client.request(method, f"/api/v1{path}", headers=headers, **kwargs)


def create(member, title="나루토와 사스케, 최종 승자는?", side_a="나루토", side_b="사스케", **extra):
    # 관련 작품은 1~2개 필수다 (2026-11-25). 따로 주지 않으면 나루토(100)를 단다.
    body = {"topicId": 1, "title": title, "sideA": side_a, "sideB": side_b, "workIds": [100], **extra}
    return call(member, "POST", "/debates", json=body)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def same_time(text: str | None, expected: datetime) -> bool:
    return text is not None and datetime.fromisoformat(text.replace("Z", "+00:00")) == expected


def test_debate_routes_exist_only_when_the_switch_is_on():
    assert "/api/v1/debates" in create_app(enable_debate=True).openapi()["paths"]
    assert not any("debate" in path for path in create_app(enable_debate=False).openapi()["paths"])


def test_create_validates_input_and_keeps_one_voting_topic_per_member(debate_api):
    author = debate_api.member()
    response = create(author, title="  나루토와   사스케,  최종 승자는?  ", description="  종말의 계곡 기준  ", workIds=[100])
    assert response.status_code == 201, response.text
    debate = response.json()
    assert debate["title"] == "나루토와 사스케, 최종 승자는?"
    assert debate["description"] == "종말의 계곡 기준"
    assert (debate["status"], debate["voteCount"], debate["voteThreshold"]) == ("voting", 0, 3)
    assert debate["works"] == [{"entityId": 100, "name": "나루토"}]
    assert (debate["isMine"], debate["myVote"], debate["mySide"], debate["result"]) == (True, False, None, None)
    assert same_time(debate["votingEndsAt"], START + timedelta(days=7))
    assert debate["sideA"] == {"label": "나루토", "count": 0, "percent": None}

    other = debate_api.member()
    valid = {"topicId": 1, "title": "주제", "sideA": "나루토", "sideB": "사스케", "workIds": [100]}
    for body, field in (
        ({"sideB": "나루토"}, None),                 # 양쪽 이름이 같음
        ({"sideA": "  "}, "sideA"),                  # 빈 선택지
        ({"title": "가" * 61}, "title"),
        ({"description": "가" * 101}, "description"),  # 설명은 100자까지 (2026-11-25)
        ({"workIds": []}, "workIds"),                # 작품은 필수 (2026-11-25)
        ({"workIds": [100, 100]}, "workIds"),        # 같은 작품 두 번
        ({"workIds": [1, 2, 3]}, "workIds"),         # 3개 이상 (2026-11-25부터 2개까지)
    ):
        response = call(other, "POST", "/debates", json=valid | body)
        assert response.status_code == 422, body
        assert response.json()["code"] == "VALIDATION_ERROR"
        if field:
            assert any(item["field"] == field for item in response.json()["fields"]), response.json()
    missing = call(other, "POST", "/debates", json={key: value for key, value in valid.items() if key != "workIds"})
    assert missing.status_code == 422 and any(item["field"] == "workIds" for item in missing.json()["fields"]), missing.json()
    response = create(other, workIds=[200])       # 다른 분야 작품
    assert response.status_code == 422 and response.json()["fields"][0]["field"] == "workIds"
    assert call(other, "POST", "/debates", json=valid | {"topicId": 9}).json()["code"] == "TOPIC_NOT_FOUND"
    no_csrf = other.client.post("/api/v1/debates", json=valid)
    assert no_csrf.status_code == 403 and no_csrf.json()["code"] == "CSRF_INVALID"

    second = create(author, title="두 번째 주제")
    assert second.status_code == 409 and second.json()["code"] == "DEBATE_ACTIVE_LIMIT"
    # 7일 안에 기준 표를 못 받은 주제는 삭제되고(정리 작업 전이라도 없는 것으로 본다), 다시 올릴 수 있다.
    debate_api.clock.now = START + timedelta(days=7, seconds=1)
    assert call(author, "GET", f"/debates/{debate['debateId']}").json()["code"] == "DEBATE_NOT_FOUND"
    assert create(author, title="두 번째 주제").status_code == 201


def test_a_member_cannot_post_a_new_topic_while_their_topic_room_is_open(debate_api):
    """한 사람이 주제를 하나씩만: 투표 중일 때뿐 아니라 토론방이 열린 48시간 동안에도 새 주제를 못 올린다 (2026-11-24 결정, 남용 방지)."""
    author = debate_api.member()
    debate_id = create(author).json()["debateId"]
    for member in (author, debate_api.member(), debate_api.member()):
        assert call(member, "PUT", f"/debates/{debate_id}/vote").status_code == 200
    assert call(author, "GET", f"/debates/{debate_id}").json()["status"] == "open"
    blocked = create(author, title="두 번째 주제")
    assert blocked.status_code == 409 and blocked.json()["code"] == "DEBATE_ACTIVE_LIMIT"
    debate_api.clock.now = START + timedelta(hours=48) - timedelta(seconds=1)
    assert create(author, title="두 번째 주제").status_code == 409
    # 48시간이 끝나면(정리 작업이 돌기 전이라도) 다시 올릴 수 있다.
    debate_api.clock.now = START + timedelta(hours=48)
    assert call(author, "GET", f"/debates/{debate_id}").json()["status"] == "closed"
    assert create(author, title="두 번째 주제").status_code == 201


def test_side_labels_take_up_to_8_characters(debate_api):
    """선택지 이름은 2026-11-25 디자인대로 8자까지만 받는다 (2026-11-24에는 12자). DB 칼럼은 20자 그대로다."""
    member = debate_api.member()
    too_long = create(member, side_a="가" * 9)
    assert too_long.status_code == 422 and too_long.json()["code"] == "VALIDATION_ERROR"
    assert any(item["field"] == "sideA" for item in too_long.json()["fields"]), too_long.json()
    # 앞뒤 공백과 겹친 공백을 줄인 뒤에 센다.
    response = create(member, side_a="  " + "가" * 8 + "  ", side_b="우즈마키  나루토")
    assert response.status_code == 201, response.json()
    assert (response.json()["sideA"]["label"], response.json()["sideB"]["label"]) == ("가" * 8, "우즈마키 나루토")


def test_descriptions_keep_every_line_break(debate_api):
    """설명의 줄바꿈은 막지 않고 그대로 저장한다. 카드에는 3줄까지만 보이고 넘으면 '…'로 줄인다 (2026-11-25, 화면 CSS)."""
    description = "가" + "\n" * 98 + "나"
    response = create(debate_api.member(), description=description)
    assert response.status_code == 201, response.json()
    assert response.json()["description"] == description


def test_topics_take_a_100_character_description_and_one_or_two_works(debate_api):
    """카드의 더보기를 없애면서 설명은 100자, 관련 작품은 1~2개 필수다 (2026-11-25 디자인, 전에는 200자 · 0~3개)."""
    response = create(debate_api.member(), description="가" * 100, workIds=[100, 101])
    assert response.status_code == 201, response.json()
    assert response.json()["description"] == "가" * 100
    assert response.json()["works"] == [{"entityId": 100, "name": "나루토"}, {"entityId": 101, "name": "원피스"}]


def test_topics_with_banned_words_are_turned_away(debate_api):
    """제목 · 설명 · 선택지에 욕설 · 선정적인 말이 있으면 올리지 않는다 (2026-11-25 사용자 결정, app/core/profanity)."""
    member = debate_api.member()
    for extra, field in (({"title": "씨발 누가 이김?"}, "title"), ({"description": "개새끼들 싸움"}, "description"),
                         ({"side_a": "야동왕"}, "sideA"), ({"side_b": "섹스"}, "sideB")):
        response = create(member, **extra)
        assert response.status_code == 422, response.json()
        body = response.json()
        assert (body["code"], body["message"]) == ("DEBATE_BANNED_WORD", "쓸 수 없는 단어가 들어 있어요. 다른 표현으로 바꿔 주세요.")
        assert [item["field"] for item in body["fields"]] == [field]
    # 걸리는 글자를 품은 멀쩡한 말은 올라간다
    response = create(member, title="시발점에서 다시 시작하면?", description="위험한 녀석 vs 졸라맨")
    assert response.status_code == 201, response.json()


def test_votes_spend_and_refund_tickets_and_refill_every_hour(debate_api):
    authors = [debate_api.member() for _ in range(4)]
    debates = [create(author, title=f"주제 {index}").json()["debateId"] for index, author in enumerate(authors)]
    voter = debate_api.member()
    assert call(voter, "GET", "/users/me/debate-tickets").json() == {"balance": 3, "max": 3, "nextRefillAt": None}

    first = call(voter, "PUT", f"/debates/{debates[0]}/vote")
    assert first.status_code == 200, first.text
    assert (first.json()["debate"]["voteCount"], first.json()["debate"]["myVote"], first.json()["opened"]) == (1, True, False)
    assert first.json()["tickets"]["balance"] == 2
    assert same_time(first.json()["tickets"]["nextRefillAt"], START + timedelta(hours=1))
    # 이미 투표한 주제에 다시 투표하면 투표권을 쓰지 않는다.
    again = call(voter, "PUT", f"/debates/{debates[0]}/vote").json()
    assert (again["debate"]["voteCount"], again["tickets"]["balance"]) == (1, 2)
    # 취소하면 1개 즉시 돌려받고, 가득 차면 충전 시계가 멈춘다.
    cancelled = call(voter, "DELETE", f"/debates/{debates[0]}/vote")
    assert cancelled.status_code == 200
    assert (cancelled.json()["debate"]["voteCount"], cancelled.json()["debate"]["myVote"]) == (0, False)
    assert cancelled.json()["tickets"] == {"balance": 3, "max": 3, "nextRefillAt": None}

    for debate_id in debates[:3]:
        assert call(voter, "PUT", f"/debates/{debate_id}/vote").status_code == 200
    empty = call(voter, "PUT", f"/debates/{debates[3]}/vote")
    assert empty.status_code == 409 and empty.json()["code"] == "DEBATE_VOTE_TICKETS_EMPTY"
    assert "60분" in empty.json()["message"]
    # 취소한 주제는 취소하지 않은 표 수 그대로다.
    assert [call(voter, "GET", f"/debates/{debate_id}").json()["voteCount"] for debate_id in debates] == [1, 1, 1, 0]

    debate_api.clock.now = START + timedelta(minutes=61)
    tickets = call(voter, "GET", "/users/me/debate-tickets").json()
    assert tickets["balance"] == 1 and same_time(tickets["nextRefillAt"], START + timedelta(hours=2))
    assert call(voter, "PUT", f"/debates/{debates[3]}/vote").json()["tickets"]["balance"] == 0
    debate_api.clock.now = START + timedelta(hours=5)
    assert call(voter, "GET", "/users/me/debate-tickets").json() == {"balance": 3, "max": 3, "nextRefillAt": None}


def test_threshold_vote_opens_the_room_and_notifies_voters_and_author(debate_api):
    author, first, muted, last = (debate_api.member() for _ in range(4))
    debate_id = create(author).json()["debateId"]
    with Session(debate_api.engine) as session:
        # 앱 안 알림을 끈 회원에게는 만들지 않는다.
        session.get(NotificationSetting, muted.user_id).in_app_enabled = False
        session.commit()
    assert call(first, "PUT", f"/debates/{debate_id}/vote").json()["opened"] is False
    assert call(muted, "PUT", f"/debates/{debate_id}/vote").json()["opened"] is False
    opened = call(last, "PUT", f"/debates/{debate_id}/vote").json()
    assert opened["opened"] is True
    room = opened["debate"]
    assert (room["status"], room["voteCount"]) == ("open", 3)
    assert same_time(room["openedAt"], START) and same_time(room["closesAt"], START + timedelta(hours=48))

    with Session(debate_api.engine) as session:
        links = session.execute(select(DebateNotification.user_id, Notification.notification_type, Notification.title, Notification.body)
                                .join(Notification, Notification.notification_id == DebateNotification.notification_id)).all()
    assert {user_id for user_id, *_ in links} == {author.user_id, first.user_id, last.user_id}
    assert {kind for _, kind, _, _ in links} == {"debate_opened"}
    # 제목은 토론 주제, 본문은 누구에게 가는 알림인지 (2026-11-24)
    assert {title for _, _, title, _ in links} == {"나루토와 사스케, 최종 승자는?"}
    bodies = {user_id: body for user_id, _, _, body in links}
    assert bodies[author.user_id] == "내가 올린 주제가 3표를 모아 토론방이 열렸어요. 48시간 동안 토론할 수 있어요."
    assert bodies[first.user_id].startswith("투표한 주제가 3표를 모아")

    inbox = call(first, "GET", "/users/me/notifications").json()["items"]
    item = next(item for item in inbox if item["type"] == "debate_opened")
    assert item["debateId"] == debate_id and item["isRead"] is False
    assert call(first, "GET", "/users/me/notifications/unread-count").json()["unreadCount"] >= 1
    assert call(first, "PATCH", f"/users/me/notifications/{item['notificationId']}", json={"isRead": True}).status_code == 200

    # 열린 뒤에는 투표도 취소도 할 수 없다.
    late = debate_api.member()
    assert call(late, "PUT", f"/debates/{debate_id}/vote").json()["code"] == "DEBATE_NOT_VOTING"
    assert call(first, "DELETE", f"/debates/{debate_id}/vote").json()["code"] == "DEBATE_NOT_VOTING"
    assert [row["debateId"] for row in call(late, "GET", "/debates", params={"status": "open"}).json()["items"]] == [debate_id]
    assert call(late, "GET", "/debates", params={"status": "voting"}).json()["items"] == []
    # 48시간이 지나면 정리 작업 전이라도 종료로 보이고, 진영 고른 사람이 없으니 결과는 none
    debate_api.clock.now = START + timedelta(hours=48)
    closed = call(late, "GET", f"/debates/{debate_id}").json()
    assert (closed["status"], closed["result"]) == ("closed", "none")
    ended = call(late, "GET", "/debates", params={"status": "ended"}).json()["items"]
    assert [row["debateId"] for row in ended] == [debate_id]


def test_only_the_author_can_delete_a_voting_topic_and_voters_get_tickets_back(debate_api):
    """투표 중인 주제는 표가 있어도 작성자가 지울 수 있다 (2026-11-23). 투표한 사람은 투표권을 돌려받는다."""
    author, other, voter = debate_api.member(), debate_api.member(), debate_api.member()
    debate_id = create(author).json()["debateId"]
    assert call(other, "DELETE", f"/debates/{debate_id}").json()["code"] == "DEBATE_NOT_FOUND"
    assert call(author, "PUT", f"/debates/{debate_id}/vote").status_code == 200  # 자기 주제에도 투표할 수 있다
    assert call(voter, "PUT", f"/debates/{debate_id}/vote").json()["tickets"]["balance"] == 2
    deleted = call(author, "DELETE", f"/debates/{debate_id}")
    assert deleted.status_code == 204, deleted.text
    assert call(author, "GET", f"/debates/{debate_id}").json()["code"] == "DEBATE_NOT_FOUND"
    for member in (author, voter):  # 표를 지우며 투표권 1개씩 돌려준다
        assert call(member, "GET", "/users/me/debate-tickets").json()["balance"] == 3
    assert call(voter, "GET", "/users/me/debate-votes").json()["items"] == []
    with Session(debate_api.engine) as session:
        assert session.scalar(select(func.count()).select_from(DebateVote).where(DebateVote.debate_id == debate_id)) == 0
    assert create(author, title="다시 올린 주제").status_code == 201


def test_others_cannot_delete_a_topic_that_already_has_votes(debate_api):
    """남의 주제는 표가 있어도, 투표한 사람이어도 지울 수 없고(404) 주제·표·투표권은 그대로다."""
    author, voter, stranger = debate_api.member(), debate_api.member(), debate_api.member()
    debate_id = create(author).json()["debateId"]
    assert call(voter, "PUT", f"/debates/{debate_id}/vote").json()["tickets"]["balance"] == 2
    for member in (voter, stranger):
        refused = call(member, "DELETE", f"/debates/{debate_id}")
        assert refused.status_code == 404 and refused.json()["code"] == "DEBATE_NOT_FOUND"
    assert call(author, "GET", f"/debates/{debate_id}").json()["voteCount"] == 1
    assert call(voter, "GET", "/users/me/debate-tickets").json()["balance"] == 2
    assert [row["debateId"] for row in call(voter, "GET", "/users/me/debate-votes").json()["items"]] == [debate_id]


def test_an_opened_topic_cannot_be_deleted(debate_api):
    author, stranger = debate_api.member(), debate_api.member()
    debate_id = create(author).json()["debateId"]
    for member in (author, debate_api.member(), debate_api.member()):
        assert call(member, "PUT", f"/debates/{debate_id}/vote").status_code == 200
    assert call(author, "GET", f"/debates/{debate_id}").json()["status"] == "open"
    refused = call(author, "DELETE", f"/debates/{debate_id}")
    assert refused.status_code == 409 and refused.json()["code"] == "DEBATE_DELETE_NOT_ALLOWED"
    # 남에게는 열린 방이어도 '지울 수 없는 상태'가 아니라 그냥 없는 주제로 답한다
    assert call(stranger, "DELETE", f"/debates/{debate_id}").json()["code"] == "DEBATE_NOT_FOUND"
    assert call(author, "GET", f"/debates/{debate_id}").json()["status"] == "open"


def test_lists_search_and_my_activity(debate_api):
    a, b, c, voter = (debate_api.member() for _ in range(4))
    naruto = create(a, workIds=[100]).json()["debateId"]
    debate_api.clock.now = START + timedelta(minutes=1)
    luffy = create(b, title="루피는 해적왕이 될 수 있을까?", side_a="된다", side_b="못 된다", workIds=[101]).json()["debateId"]
    debate_api.clock.now = START + timedelta(minutes=2)
    gojo = create(c, title="고죠는 최강일까?", side_a="최강", side_b="아니다", workIds=[102]).json()["debateId"]
    for member in (a, voter):
        call(member, "PUT", f"/debates/{luffy}/vote")
    call(voter, "PUT", f"/debates/{naruto}/vote")

    def ids(response):
        assert response.status_code == 200, response.text
        return [row["debateId"] for row in response.json()["items"]]

    assert ids(call(voter, "GET", "/debates", params={"status": "voting"})) == [gojo, luffy, naruto]
    assert ids(call(voter, "GET", "/debates", params={"status": "voting", "sort": "votes"})) == [luffy, naruto, gojo]
    assert ids(call(voter, "GET", "/debates", params={"status": "voting", "entityId": 100})) == [naruto]
    first_page = call(voter, "GET", "/debates", params={"status": "voting", "limit": 2}).json()
    assert [row["debateId"] for row in first_page["items"]] == [gojo, luffy] and first_page["nextCursor"]
    second_page = call(voter, "GET", "/debates", params={"status": "voting", "limit": 2, "cursor": first_page["nextCursor"]}).json()
    assert [row["debateId"] for row in second_page["items"]] == [naruto] and second_page["nextCursor"] is None
    assert call(voter, "GET", "/debates", params={"status": "open", "cursor": "broken"}).status_code == 200
    assert call(voter, "GET", "/debates", params={"status": "voting", "cursor": "broken"}).json()["code"] == "INVALID_CURSOR"

    my_votes = call(voter, "GET", "/users/me/debate-votes").json()["items"]
    assert {row["debateId"] for row in my_votes} == {naruto, luffy} and all(row["myVote"] for row in my_votes)
    assert ids(call(b, "GET", "/users/me/debates")) == [luffy]

    # 검색: 작품 이름은 작품 칩과 그 작품이 연결된 토론, 캐릭터 이름은 제목·선택지에서
    found = call(voter, "GET", "/debates/search", params={"topicId": 1, "q": "naruto"}).json()
    assert [work["entityId"] for work in found["works"]] == [100]
    assert [row["debateId"] for row in found["debates"]] == [naruto]
    assert [row["debateId"] for row in call(voter, "GET", "/debates/search", params={"topicId": 1, "q": "사스케"}).json()["debates"]] == [naruto]
    with Session(debate_api.engine) as session:
        assert session.scalar(select(func.count()).select_from(DebateSearchLog)) == 0
    call(voter, "GET", "/debates/search", params={"topicId": 1, "q": "원피스", "record": "true", "pickedEntityId": 101})
    call(voter, "GET", "/debates/search", params={"topicId": 1, "q": "결말", "record": "true"})
    with Session(debate_api.engine) as session:
        logs = session.execute(select(DebateSearchLog.search_text, DebateSearchLog.picked_entity_id,
                                      DebateSearchLog.matched_entity_ids, DebateSearchLog.result_count)
                               .order_by(DebateSearchLog.search_log_id)).all()
    assert [tuple(row) for row in logs] == [("원피스", 101, [101], 1), ("결말", None, [], 0)]
    works = call(voter, "GET", "/debates/works", params={"topicId": 1, "q": "원피"}).json()["items"]
    assert [work["entityId"] for work in works] == [101]

    # 7일 안에 기준 표를 못 받으면 삭제된다: 지난 토론방에도, 검색에도 없고 투표하면 찾을 수 없다.
    debate_api.clock.now = START + timedelta(days=8)
    assert ids(call(voter, "GET", "/debates", params={"status": "ended"})) == []
    assert call(voter, "GET", "/debates/search", params={"topicId": 1, "q": "루피"}).json()["debates"] == []
    assert call(voter, "GET", "/users/me/debate-votes").json()["items"] == []
    assert call(voter, "PUT", f"/debates/{gojo}/vote").json()["code"] == "DEBATE_NOT_FOUND"
    assert call(voter, "DELETE", f"/debates/{gojo}/vote").json()["code"] == "DEBATE_NOT_FOUND"


def test_debate_api_requires_an_onboarded_member(debate_api):
    member = debate_api.member()
    anonymous = TestClient(debate_api.api)
    with anonymous:
        assert anonymous.get("/api/v1/debates").json()["code"] == "AUTH_REQUIRED"
    assert call(member, "GET", "/debates/999").json()["code"] == "DEBATE_NOT_FOUND"


def test_cards_show_the_author_nickname_and_hide_it_after_the_author_withdraws(debate_api):
    author = debate_api.member()
    debate_id = create(author).json()["debateId"]
    viewer = debate_api.member()
    assert call(viewer, "GET", f"/debates/{debate_id}").json()["authorNickname"] == "토론회원1"
    assert [row["authorNickname"] for row in call(viewer, "GET", "/debates", params={"status": "voting"}).json()["items"]] == ["토론회원1"]
    with Session(debate_api.engine) as session:
        session.get(UserAccount, author.user_id).account_status = "withdrawn"
        session.commit()
    # 탈퇴(유예 중 포함)한 작성자의 닉네임은 보여 주지 않는다.
    assert call(viewer, "GET", f"/debates/{debate_id}").json()["authorNickname"] == "알 수 없음"
