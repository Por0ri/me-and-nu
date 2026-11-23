"""토론 정리 작업(설계 5.3)과 지난 토론방 보관 기간(9일).

test_debate_api의 일회용 SQLite 준비(debate_api)를 그대로 쓴다. 개설 기준이 3표라서 작성자와 회원 2명이
투표하면 방이 START에 열리고 START+48시간에 끝난다. 정리 작업은 서비스 함수를 직접 부르고 커밋한다.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.jobs.finalize_debates import run as run_job
from app.models import (
    Debate, DebateMessage, DebateNotification, DebateSide, DebateVote, DebateWork, Notification,
)
from app.seeds.menu_works import MenuWorksError
from app.services.debate_finalize_service import finalize_debates
from test_debate_api import START, DebateSqliteSession, call, create, debate_api  # noqa: F401 (debate_api는 fixture)

ROOM_ENDS = START + timedelta(hours=48)
TOPIC_ENDS = START + timedelta(days=7)
KEEP = timedelta(days=9)


def finalize(debate_api, *, apply=True):
    async def scenario():
        with Session(debate_api.engine, expire_on_commit=False) as session:
            db = DebateSqliteSession(session, SimpleNamespace(enabled=False, answers_insert=False))
            report = await finalize_debates(db, apply=apply, now=debate_api.clock.now)
            await (db.commit() if apply else db.rollback())
            return report

    return asyncio.run(scenario())


def counts(debate_api, debate_id):
    with Session(debate_api.engine) as session:
        def number(model):
            return session.scalar(select(func.count()).select_from(model).where(model.debate_id == debate_id))

        row = session.get(Debate, debate_id)
        return {
            "status": row.status if row else None, "messages": number(DebateMessage), "votes": number(DebateVote),
            "sides": number(DebateSide), "works": number(DebateWork), "notice_links": number(DebateNotification),
        }


def setup_debates(debate_api):
    """열린 방 하나(진영·채팅 있음)와, 표가 모자라 투표 중인 주제 하나."""
    author = debate_api.member()
    room = create(author, workIds=[100]).json()["debateId"]
    voters = [debate_api.member() for _ in range(2)]
    for member in (author, *voters):
        assert call(member, "PUT", f"/debates/{room}/vote").status_code == 200
    call(voters[0], "PUT", f"/debates/{room}/side", json={"side": "a"})
    for index in range(3):
        assert call(voters[0], "POST", f"/debates/{room}/messages", json={"body": f"채팅 {index}"}).status_code == 201
    topic = create(voters[1], title="표가 모자란 주제", side_a="예", side_b="아니오").json()["debateId"]
    assert call(author, "PUT", f"/debates/{topic}/vote").status_code == 200
    return room, topic, author, voters


def test_finalize_saves_ended_states_and_deletes_chat_of_closed_rooms(debate_api):
    room, topic, author, voters = setup_debates(debate_api)
    zero = {"expired": 0, "closed": 0, "deleted_messages": 0, "purged_debates": 0}
    assert finalize(debate_api) == {"mode": "apply", "retention_days": 9, **zero}  # 아직 끝난 게 없다

    debate_api.clock.now = ROOM_ENDS
    # 미리보기는 개수만 세고 아무것도 바꾸지 않는다.
    assert finalize(debate_api, apply=False) == {"mode": "dry_run", "retention_days": 9, **zero, "closed": 1, "deleted_messages": 3}
    assert counts(debate_api, room)["status"] == "open" and counts(debate_api, room)["messages"] == 3

    assert finalize(debate_api) == {"mode": "apply", "retention_days": 9, **zero, "closed": 1, "deleted_messages": 3}
    assert counts(debate_api, room) == {"status": "closed", "messages": 0, "votes": 3, "sides": 1, "works": 1, "notice_links": 3}
    detail = call(author, "GET", f"/debates/{room}").json()
    # 채팅은 지워도 채팅 수와 결과는 남는다.
    assert (detail["status"], detail["messageCount"], detail["result"], detail["participantCount"]) == ("closed", 3, "a", 1)
    assert finalize(debate_api) == {"mode": "apply", "retention_days": 9, **zero}  # 다시 돌려도 같다

    debate_api.clock.now = TOPIC_ENDS
    assert finalize(debate_api)["expired"] == 1
    assert counts(debate_api, topic)["status"] == "expired"
    assert call(author, "GET", f"/debates/{topic}").json()["status"] == "expired"


def test_ended_debates_stay_nine_days_then_disappear_and_are_purged(debate_api):
    room, topic, author, voters = setup_debates(debate_api)

    def ended(member, **params):
        return [row["debateId"] for row in call(member, "GET", "/debates", params={"status": "ended", **params}).json()["items"]]

    # 끝난 뒤 9일이 되기 직전까지는 지난 토론방·상세·검색·내 주제에 보인다 (정리 작업이 안 돌았어도 시각으로 계산).
    debate_api.clock.now = ROOM_ENDS + KEEP - timedelta(seconds=1)
    assert ended(author) == [topic, room]
    assert call(author, "GET", f"/debates/{room}").status_code == 200
    assert room in [row["debateId"] for row in call(author, "GET", "/debates/search", params={"topicId": 1, "q": "나루토"}).json()["debates"]]
    assert room in [row["debateId"] for row in call(author, "GET", "/users/me/debates").json()["items"]]

    # 9일이 지나면 정리 작업이 지우기 전이라도 바로 숨긴다.
    debate_api.clock.now = ROOM_ENDS + KEEP
    assert ended(author) == [topic]
    assert ended(author, result="closed") == []
    assert call(author, "GET", f"/debates/{room}").json()["code"] == "DEBATE_NOT_FOUND"
    assert call(author, "GET", "/debates/search", params={"topicId": 1, "q": "나루토"}).json()["debates"] == []
    assert call(author, "GET", "/users/me/debates").json()["items"] == []

    # 정리 작업이 토론 줄을 통째로 지운다. 투표·진영·관련 작품·알림 연결은 함께 지워지고 알림 글은 남는다.
    assert finalize(debate_api, apply=False)["purged_debates"] == 1
    report = finalize(debate_api)
    assert (report["closed"], report["expired"], report["deleted_messages"], report["purged_debates"]) == (1, 1, 3, 1)
    assert counts(debate_api, room) == {"status": None, "messages": 0, "votes": 0, "sides": 0, "works": 0, "notice_links": 0}
    with Session(debate_api.engine) as session:
        assert session.scalar(select(func.count()).select_from(Notification).where(Notification.notification_type == "debate_opened")) == 3
    notice = next(item for item in call(voters[0], "GET", "/users/me/notifications").json()["items"] if item["type"] == "debate_opened")
    assert notice["debateId"] is None  # '토론방 보러 가기' 연결만 없어진다

    # 소각된 주제는 투표 마감(7일) 뒤 9일까지 남는다.
    debate_api.clock.now = TOPIC_ENDS + KEEP - timedelta(seconds=1)
    assert ended(author) == [topic] and finalize(debate_api)["purged_debates"] == 0
    debate_api.clock.now = TOPIC_ENDS + KEEP
    assert ended(author) == [] and finalize(debate_api)["purged_debates"] == 1
    assert counts(debate_api, topic)["status"] is None


@pytest.mark.parametrize("database_url", ["sqlite://", "postgresql+psycopg://user:secret@localhost/other_database"])
def test_job_refuses_a_database_that_is_not_the_one_named(database_url):
    with pytest.raises(MenuWorksError):
        asyncio.run(run_job(expected_database="me_nu_checking", apply=False, database_url=database_url))
