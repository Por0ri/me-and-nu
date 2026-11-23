"""토론방 안 (설계 D-09~12): 진영 고르기, 채팅, 실시간 스트림(SSE)과 방송 허브.

API 시험은 test_debate_api의 일회용 SQLite 준비(debate_api)를 그대로 쓴다. 개설 기준이 3표라서
작성자와 회원 2명이 투표하면 방이 열린다. 시간은 debate_service.utcnow를 바꿔서 흘린다.

TestClient는 스트림이 끝나야 응답을 돌려주므로, 스트림은 다른 스레드에서 열고 허브의 close_all이나
방 종료 시각으로 끝낸 뒤 본문 전체를 읽는다.
"""

import asyncio
import json
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.debates.router import DebateStreamResponse, _resume_point
from app.core.config import settings
from app.models import DebateMessage, DebateSide, UserAccount
from app.schemas.debate import DebateMessageView
from app.services import debate_hub, debate_service
from app.services import debate_room_service as room
from app.services.debate_hub import CLOSE, DebateHub, HubEvent, StreamLimitError, hub
from test_debate_api import START, DebateSqliteSession, call, create, debate_api  # noqa: F401 (debate_api는 fixture)


# ───────────── 방송 허브 ─────────────

def test_hub_counts_streams_per_member_and_lets_them_go():
    async def scenario():
        local = DebateHub()
        first = local.subscribe(1, 7, limit=2)
        local.subscribe(2, 7, limit=2)
        with pytest.raises(StreamLimitError):
            local.subscribe(3, 7, limit=2)
        local.subscribe(1, 8, limit=2)  # 다른 회원은 따로 센다
        assert (local.count(), local.count(1), local.count(2), local.count(3)) == (3, 2, 1, 0)
        local.unsubscribe(first)
        local.unsubscribe(first)  # 두 번 빼도 괜찮다
        assert local.count(1) == 1
        local.subscribe(3, 7, limit=2)  # 자리가 나면 다시 열 수 있다

    asyncio.run(scenario())


def test_hub_drops_streams_that_never_started():
    async def scenario():
        local = DebateHub()
        stale = local.subscribe(1, 7, limit=2)
        running = local.subscribe(1, 7, limit=2)
        running.started = True
        old = debate_hub.UNSTARTED_TIMEOUT_SECONDS + 1
        stale.registered_at -= old
        running.registered_at -= old
        # 응답이 시작되기 전에 떠난 연결은 정리 코드가 돌지 않는다. 다음 구독 때 치워서 자리를 돌려준다.
        local.subscribe(2, 7, limit=2)
        assert (local.count(1), local.count(2)) == (1, 1)

    asyncio.run(scenario())


def test_hub_cuts_off_a_stream_that_falls_too_far_behind():
    async def scenario():
        local = DebateHub()
        slow = local.subscribe(1, 7, limit=3)
        for index in range(debate_hub.QUEUE_SIZE + 1):
            local.publish(1, HubEvent(kind="chat", data={"n": index}, event_id=index + 1))
        assert slow.overflowed
        # 쌓인 것을 버리고 끝 신호만 남긴다. 브라우저가 다시 붙으면서 놓친 채팅을 받아간다.
        assert slow.queue.qsize() == 1 and slow.queue.get_nowait() is CLOSE
        local.publish(1, HubEvent(kind="chat", data={}, event_id=999))
        assert slow.queue.empty()

    asyncio.run(scenario())


def test_hub_delivers_to_streams_on_other_event_loops():
    local = DebateHub()
    ready = threading.Event()
    received = []

    async def listen():
        subscriber = local.subscribe(1, 7, limit=3)
        ready.set()
        received.append(await asyncio.wait_for(subscriber.queue.get(), 5))
        local.unsubscribe(subscriber)

    thread = threading.Thread(target=lambda: asyncio.run(listen()))
    thread.start()
    assert ready.wait(5)
    assert local.publish(1, HubEvent(kind="sides", data={"participantCount": 1})) == 1
    assert local.publish(2, HubEvent(kind="sides", data={})) == 0  # 다른 방에는 가지 않는다
    thread.join(5)
    assert [event.data for event in received] == [{"participantCount": 1}]
    assert local.count() == 0


# ───────────── 스트림 본문 ─────────────

def _view(message_id: int, *, mine: bool) -> DebateMessageView:
    return DebateMessageView(message_id=message_id, side=None, nickname="회원", body=f"채팅 {message_id}", created_at=START, is_mine=mine)


def _chat(message_id: int, sender: int) -> HubEvent:
    data = _view(message_id, mine=False).model_dump(by_alias=True, mode="json", exclude={"is_mine"})
    return HubEvent(kind="chat", data=data, event_id=message_id, sender_user_id=sender)


@pytest.fixture
def stream_clock(monkeypatch):
    clock = SimpleNamespace(now=START)
    monkeypatch.setattr(debate_service, "utcnow", lambda: clock.now)
    monkeypatch.setattr(settings, "debate_stream_heartbeat_seconds", 0.05)
    monkeypatch.setattr(room, "retry_milliseconds", lambda: 3000)
    yield clock
    assert hub.count() == 0


def test_reconnect_delay_is_spread_wider_as_connections_grow(monkeypatch):
    # 서버가 다시 시작돼도 모두가 같은 순간에 다시 붙지 않게 연결마다 다르게 알려 준다.
    # 연결이 적으면 1~3초로 짧게, 많으면 1~8초로 넓게 흩는다.
    def delays(connections):
        monkeypatch.setattr(hub, "count", lambda debate_id=None: connections)
        return [room.retry_milliseconds() for _ in range(300)]

    few = delays(10)
    assert 1000 <= min(few) and max(few) <= 3000 and len(set(few)) > 50
    many = delays(5000)
    assert 1000 <= min(many) and max(many) <= 8000 and max(many) > 5000
    middle = delays(400)  # 연결 1개당 10ms씩 넓어진다: 400개면 1~5초
    assert max(middle) <= 5000 and max(middle) > 4000


def _plan(subscriber, *, replay=(), known_upto=0, closes_in=timedelta(hours=1), session_in=timedelta(hours=1), resync=False):
    return room.StreamPlan(subscriber, list(replay), START + closes_in, START + session_in, known_upto, resync)


def test_stream_sends_missed_chat_first_then_live_events_once(stream_clock):
    async def scenario():
        subscriber = hub.subscribe(5, 7, limit=3)
        frames = room.stream_events(_plan(subscriber, replay=[_view(11, mine=True)], known_upto=10))
        assert await anext(frames) == "retry: 3000\n\n"
        assert await anext(frames) == (
            'id: 11\nevent: chat\ndata: {"messageId":11,"side":null,"nickname":"회원","body":"채팅 11",'
            '"createdAt":"2026-11-19T03:00:00Z","isMine":true}\n\n'
        )
        hub.publish(5, _chat(11, sender=7))  # 방금 다시 보낸 것과 겹침: 보내지 않는다
        hub.publish(5, _chat(10, sender=8))  # 화면이 이미 가진 번호: 보내지 않는다
        hub.publish(5, _chat(13, sender=7))
        hub.publish(5, _chat(12, sender=8))  # 거의 동시에 저장되어 방송 순서가 바뀌어도 빠뜨리지 않는다
        hub.publish(5, HubEvent(kind="sides", data={"participantCount": 1}))
        # 쌓여 있던 이벤트는 한 번에 묶여 온다. isMine은 받는 사람마다 계산한다.
        assert await anext(frames) == (
            'id: 13\nevent: chat\ndata: {"messageId":13,"side":null,"nickname":"회원","body":"채팅 13",'
            '"createdAt":"2026-11-19T03:00:00Z","isMine":true}\n\n'
            'id: 12\nevent: chat\ndata: {"messageId":12,"side":null,"nickname":"회원","body":"채팅 12",'
            '"createdAt":"2026-11-19T03:00:00Z","isMine":false}\n\n'
            'event: sides\ndata: {"participantCount":1}\n\n'
        )
        hub.close_all()
        assert [frame async for frame in frames] == []

    asyncio.run(scenario())


def test_stream_pings_while_quiet_and_says_closed_after_sending_what_is_left(stream_clock):
    async def scenario():
        subscriber = hub.subscribe(5, 7, limit=3)
        frames = room.stream_events(_plan(subscriber))
        assert await anext(frames) == "retry: 3000\n\n"
        assert await anext(frames) == "retry: 3000\n: ping\n\n"  # 빈 신호에 지금 기준 재연결 간격을 같이 싣는다
        hub.publish(5, _chat(1, sender=8))
        stream_clock.now = START + timedelta(hours=1)
        assert "event: chat" in await anext(frames)  # 끝나기 직전 채팅도 빠짐없이
        assert [frame async for frame in frames] == ['event: closed\ndata: {"debateId":5}\n\n']

    asyncio.run(scenario())


def test_stream_says_session_expired_when_login_runs_out(stream_clock):
    async def scenario():
        subscriber = hub.subscribe(5, 7, limit=3)
        frames = room.stream_events(_plan(subscriber, session_in=timedelta(minutes=30), resync=True))
        assert await anext(frames) == "retry: 3000\n\n"
        assert await anext(frames) == "event: resync\ndata: {}\n\n"
        stream_clock.now = START + timedelta(minutes=30)
        assert [frame async for frame in frames] == ["event: session-expired\ndata: {}\n\n"]

    asyncio.run(scenario())


def test_stream_response_lets_go_even_when_the_browser_leaves_before_it_starts(stream_clock):
    async def scenario():
        subscriber = hub.subscribe(5, 7, limit=3)

        async def receive():
            return {"type": "http.disconnect"}

        async def send(_message):
            await asyncio.Event().wait()  # 응답 머리도 못 보낸 채 끊긴다

        await DebateStreamResponse(_plan(subscriber))({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)
        # 본문이 시작되지 않아 본문의 정리 코드는 돌지 않았지만 구독은 풀렸다.
        assert not subscriber.started and hub.count() == 0

    asyncio.run(scenario())


def test_each_event_is_rendered_once_per_kind_of_receiver():
    # 같은 방 연결 수백 개가 같은 글을 나눠 쓴다 (받는 사람마다 JSON을 다시 만들지 않는다).
    event = _chat(5, sender=7)
    assert room._render(event, 7) is room._render(event, 7)
    assert '"isMine":true' in room._render(event, 7) and '"isMine":false' in room._render(event, 8)
    assert room._render(event, 8) is room._render(event, 9)
    assert set(event.frames) == {True, False}
    sides = HubEvent(kind="sides", data={"participantCount": 1})
    assert room._render(sides, 7) is room._render(sides, 8) == 'event: sides\ndata: {"participantCount":1}\n\n'


def test_resume_point_prefers_the_newest_id_the_browser_has():
    assert _resume_point(None, None) is None
    assert _resume_point(None, 3) == 3
    assert _resume_point("7", 3) == 7
    assert _resume_point("2", 3) == 3
    assert _resume_point(" 9 ", None) == 9
    assert _resume_point("abc", 3) == 3
    assert _resume_point("-1", None) is None


# ───────────── API ─────────────

def open_room(debate_api):
    """방을 열고 작성자·투표한 두 사람이 바로 참여한다 (채팅이 없을 때 참여해서 모든 채팅이 보인다)."""
    author = debate_api.member()
    debate_id = create(author).json()["debateId"]
    voters = [debate_api.member() for _ in range(2)]
    for member in (author, *voters):
        assert call(member, "PUT", f"/debates/{debate_id}/vote").status_code == 200
    assert call(author, "GET", f"/debates/{debate_id}").json()["status"] == "open"
    for member in (author, *voters):
        assert join(member, debate_id).status_code == 200
    return debate_id, author, voters


def join(member, debate_id):
    return call(member, "PUT", f"/debates/{debate_id}/membership")


def leave(member, debate_id):
    return call(member, "DELETE", f"/debates/{debate_id}/membership")


def side(member, debate_id, value):
    return call(member, "PUT", f"/debates/{debate_id}/side", json={"side": value})


def say(member, debate_id, body):
    return call(member, "POST", f"/debates/{debate_id}/messages", json={"body": body})


def test_side_is_separate_from_chat_and_can_change(debate_api):
    debate_id, author, (first, second) = open_room(debate_api)
    chosen = side(first, debate_id, "a")
    assert chosen.status_code == 200, chosen.text
    body = chosen.json()
    assert (body["mySide"], body["participantCount"], body["sideA"], body["sideB"]) == (
        "a", 1, {"label": "나루토", "count": 1, "percent": 100}, {"label": "사스케", "count": 0, "percent": 0},
    )
    assert side(second, debate_id, "b").json()["sideB"]["percent"] == 50
    again = side(first, debate_id, "a").json()  # 같은 진영을 다시 골라도 그대로
    assert (again["sideA"]["count"], again["sideB"]["count"]) == (1, 1)
    changed = side(first, debate_id, "b").json()  # 토론 중에 마음이 바뀔 수 있다
    assert (changed["mySide"], changed["sideA"]["count"], changed["sideB"]["count"], changed["sideB"]["percent"]) == ("b", 0, 2, 100)
    with Session(debate_api.engine) as session:
        rows = session.execute(select(DebateSide.side, func.count()).where(DebateSide.debate_id == debate_id).group_by(DebateSide.side)).all()
    assert dict(rows) == {"b": 2}

    assert side(first, debate_id, "c").status_code == 422
    no_csrf = first.client.put(f"/api/v1/debates/{debate_id}/side", json={"side": "a"})
    assert no_csrf.status_code == 403 and no_csrf.json()["code"] == "CSRF_INVALID"
    voting = create(debate_api.member(), title="투표 중인 주제").json()["debateId"]
    assert side(first, voting, "a").json()["code"] == "DEBATE_NOT_OPEN"
    assert side(first, 999, "a").json()["code"] == "DEBATE_NOT_FOUND"
    debate_api.clock.now = START + timedelta(hours=48)
    closed = side(first, debate_id, "a")
    assert closed.status_code == 409 and closed.json()["code"] == "DEBATE_NOT_OPEN"
    assert call(author, "GET", f"/debates/{debate_id}").json()["result"] == "b"


def test_messages_work_without_a_side_and_page_back_from_the_newest(debate_api):
    debate_id, author, (first, second) = open_room(debate_api)
    side(first, debate_id, "a")
    sent = say(first, debate_id, "  나루토가 이긴다  ")
    assert sent.status_code == 201, sent.text
    message = sent.json()
    assert (message["body"], message["side"], message["nickname"], message["isMine"]) == ("나루토가 이긴다", "a", "토론회원2", True)
    assert say(second, debate_id, "아직 못 골랐어").json()["side"] is None  # 진영 없이도 채팅할 수 있다
    side(first, debate_id, "b")  # 이미 보낸 메시지의 진영은 보낸 순간 그대로
    for index in range(3):
        assert say(author, debate_id, f"메시지 {index}").status_code == 201

    listing = call(second, "GET", f"/debates/{debate_id}/messages").json()
    assert [(item["body"], item["side"], item["isMine"]) for item in listing["items"][:2]] == [
        ("나루토가 이긴다", "a", False), ("아직 못 골랐어", None, True),
    ]
    assert listing["nextCursor"] is None
    page = call(second, "GET", f"/debates/{debate_id}/messages", params={"limit": 2}).json()
    assert [item["body"] for item in page["items"]] == ["메시지 1", "메시지 2"]  # 최근 것을 오래된 순으로
    older = call(second, "GET", f"/debates/{debate_id}/messages", params={"limit": 2, "cursor": page["nextCursor"]}).json()
    assert [item["body"] for item in older["items"]] == ["아직 못 골랐어", "메시지 0"]
    oldest = call(second, "GET", f"/debates/{debate_id}/messages", params={"limit": 2, "cursor": older["nextCursor"]}).json()
    assert ([item["body"] for item in oldest["items"]], oldest["nextCursor"]) == (["나루토가 이긴다"], None)
    assert call(second, "GET", f"/debates/{debate_id}/messages", params={"cursor": "broken"}).json()["code"] == "INVALID_CURSOR"
    assert call(author, "GET", f"/debates/{debate_id}").json()["messageCount"] == 5

    for payload in ({"body": ""}, {"body": "   "}, {"body": "가" * 301}, {"body": "진영", "side": "a"}):
        assert call(second, "POST", f"/debates/{debate_id}/messages", json=payload).status_code == 422, payload
    assert say(second, debate_id, "가" * 300).status_code == 201
    voting = create(debate_api.member(), title="투표 중인 주제").json()["debateId"]
    assert say(second, voting, "아직 안 열렸어").json()["code"] == "DEBATE_NOT_OPEN"
    assert call(second, "GET", f"/debates/{voting}/messages").json()["code"] == "DEBATE_NOT_OPEN"


def test_message_rate_limit_is_per_member_per_room_and_ends_with_the_room(debate_api):
    debate_id, author, (first, _) = open_room(debate_api)
    for index in range(5):
        assert say(first, debate_id, f"{index}번째").status_code == 201
    limited = say(first, debate_id, "6번째")
    assert limited.status_code == 429
    assert limited.json()["code"] == "DEBATE_MESSAGE_RATE_LIMITED" and "10초 뒤" in limited.json()["message"]
    assert say(author, debate_id, "다른 사람은 보낼 수 있다").status_code == 201
    debate_api.clock.now = START + timedelta(seconds=4)
    assert "6초 뒤" in say(first, debate_id, "아직").json()["message"]
    debate_api.clock.now = START + timedelta(seconds=10)
    assert say(first, debate_id, "이제 된다").status_code == 201

    debate_api.clock.now = START + timedelta(hours=48)
    closed = say(first, debate_id, "끝난 뒤")
    assert closed.status_code == 409 and closed.json()["code"] == "DEBATE_NOT_OPEN"
    # 끝난 방의 채팅은 보여 주지 않는다 (지우는 것은 4단계 정리 작업)
    assert call(first, "GET", f"/debates/{debate_id}/messages").json()["code"] == "DEBATE_NOT_OPEN"
    with Session(debate_api.engine) as session:
        assert session.scalar(select(func.count()).select_from(DebateMessage)) == 7


# ───────────── 실시간 스트림 API ─────────────

@pytest.fixture
def fast_stream(monkeypatch):
    monkeypatch.setattr(settings, "debate_stream_heartbeat_seconds", 0.05)
    monkeypatch.setattr(room, "retry_milliseconds", lambda: 3000)
    yield
    hub.close_all()


def open_stream(debate_api, member, debate_id, **kwargs):
    """다른 스레드에서 스트림을 연다. 각자 다른 이벤트 루프라서 허브의 루프 넘김도 함께 시험한다."""
    client = TestClient(debate_api.api, cookies=dict(member.client.cookies))
    result = {}
    thread = threading.Thread(target=lambda: result.update(response=client.get(f"/api/v1/debates/{debate_id}/stream", **kwargs)), daemon=True)
    thread.start()
    # 스트림 쪽이 DB 일을 끝내고(prepare_stream) 기다리기 시작할 때까지 기다린다. 시험용 SQLite는 연결이 하나뿐이다.
    deadline = time.monotonic() + 5
    while not any(subscriber.started for subscriber in list(hub._rooms.get(debate_id, ()))):
        assert time.monotonic() < deadline and thread.is_alive(), result
        time.sleep(0.01)
    return thread, result


def finish(thread, result, *, close=True):
    if close:
        hub.close_all()
    thread.join(5)
    assert not thread.is_alive()
    assert hub.count() == 0  # 끝난 연결은 허브에서 빠진다
    return result["response"]


def events(text: str) -> list[tuple[str, str | None, dict]]:
    found = []
    for block in text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if line and not line.startswith(":"))
        if "event" in fields:
            found.append((fields["event"], fields.get("id"), json.loads(fields["data"])))
    return found


def test_stream_sends_chat_and_sides_to_everyone_in_the_room(debate_api, fast_stream):
    debate_id, author, (first, second) = open_room(debate_api)
    thread, result = open_stream(debate_api, second, debate_id, params={"after": 0})
    other = say(first, debate_id, "첫 채팅").json()
    side(first, debate_id, "a")
    side(first, debate_id, "a")  # 바뀐 게 없으면 보내지 않는다
    mine = say(second, debate_id, "내 채팅").json()
    response = finish(thread, result)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert (response.headers["x-accel-buffering"], response.headers["cache-control"]) == ("no", "private, no-store")
    assert response.text.startswith("retry: 3000\n\n")
    assert events(response.text) == [
        ("chat", str(other["messageId"]), {**other, "isMine": False, "messageCount": 1}),
        ("sides", None, {"sideA": {"label": "나루토", "count": 1, "percent": 100},
                         "sideB": {"label": "사스케", "count": 0, "percent": 0}, "participantCount": 1}),
        ("chat", str(mine["messageId"]), {**mine, "isMine": True, "messageCount": 2}),
    ]
    assert "userId" not in response.text  # 다른 회원 번호는 보내지 않는다


def test_stream_replays_missed_chat_when_the_browser_comes_back(debate_api, fast_stream, monkeypatch):
    debate_id, _author, (first, second) = open_room(debate_api)
    ids = [say(first, debate_id, f"놓친 채팅 {index}").json()["messageId"] for index in range(3)]

    # 처음 연결: 화면이 불러온 마지막 채팅 번호(after) 뒤의 것을 먼저 보낸다.
    response = finish(*open_stream(debate_api, second, debate_id, params={"after": ids[0]}))
    assert [(kind, event_id) for kind, event_id, _ in events(response.text)] == [("chat", str(ids[1])), ("chat", str(ids[2]))]
    # 다시 연결: 브라우저가 보내는 Last-Event-ID가 더 크면 그쪽부터
    response = finish(*open_stream(debate_api, second, debate_id, params={"after": 0}, headers={"Last-Event-ID": str(ids[1])}))
    assert [(kind, event_id) for kind, event_id, _ in events(response.text)] == [("chat", str(ids[2]))]
    # 너무 많이 놓쳤으면 하나씩 보내지 않고 다시 불러오라고 알린다.
    monkeypatch.setattr(room, "REPLAY_LIMIT", 2)
    response = finish(*open_stream(debate_api, second, debate_id, params={"after": 0}))
    assert events(response.text) == [("resync", None, {})]


def test_stream_says_closed_when_the_room_ends(debate_api, fast_stream):
    debate_id, _author, (first, _) = open_room(debate_api)
    thread, result = open_stream(debate_api, first, debate_id)
    debate_api.clock.now = START + timedelta(hours=48)
    response = finish(thread, result, close=False)
    assert events(response.text) == [("closed", None, {"debateId": debate_id})]


def test_stream_turns_away_strangers_rooms_not_open_and_too_many_connections(debate_api, monkeypatch):
    debate_id, _author, (first, _) = open_room(debate_api)
    anonymous = TestClient(debate_api.api).get(f"/api/v1/debates/{debate_id}/stream")
    assert anonymous.status_code == 401
    voting = create(debate_api.member(), title="투표 중인 주제").json()["debateId"]
    not_open = call(first, "GET", f"/debates/{voting}/stream")
    assert not_open.status_code == 409 and not_open.json()["code"] == "DEBATE_NOT_OPEN"
    assert call(first, "GET", "/debates/999/stream").json()["code"] == "DEBATE_NOT_FOUND"
    monkeypatch.setattr(settings, "debate_stream_per_user", 0)
    limited = call(first, "GET", f"/debates/{debate_id}/stream")
    assert limited.status_code == 429 and limited.json()["code"] == "DEBATE_STREAM_LIMIT"
    assert hub.count() == 0


def test_prepare_stream_gives_the_db_connection_back_before_waiting(debate_api):
    debate_id, author, _ = open_room(debate_api)

    async def scenario():
        with Session(debate_api.engine, expire_on_commit=False) as session:
            db = DebateSqliteSession(session, SimpleNamespace(enabled=False, answers_insert=False))
            plan = await room.prepare_stream(db, author.user_id, debate_id, resume_after=0, session_expires_at=START + timedelta(hours=1))
            try:
                # 스트림이 열려 있는 동안 DB 연결을 붙잡고 있지 않는다 (실서비스 연결은 2개뿐).
                assert not session.in_transaction()
                assert plan.closes_at == START + timedelta(hours=48)
            finally:
                hub.unsubscribe(plan.subscriber)

    asyncio.run(scenario())


def test_chat_of_a_withdrawn_member_stays_but_without_the_nickname(debate_api):
    debate_id, _author, (first, second) = open_room(debate_api)
    say(first, debate_id, "탈퇴하기 전에 남긴 채팅")
    with Session(debate_api.engine) as session:
        session.get(UserAccount, first.user_id).account_status = "withdrawn"
        session.commit()
    items = call(second, "GET", f"/debates/{debate_id}/messages").json()["items"]
    assert [(item["body"], item["nickname"]) for item in items] == [("탈퇴하기 전에 남긴 채팅", "알 수 없음")]



# ───────────── 참여·나가기 (D-18·19) ─────────────

def test_only_members_chat_and_they_see_chat_from_when_they_joined(debate_api, fast_stream):
    debate_id, _author, (first, second) = open_room(debate_api)
    late = debate_api.member()
    card = call(late, "GET", f"/debates/{debate_id}").json()
    assert (card["memberCount"], card["joined"]) == (3, False)
    # 참여하지 않으면 채팅·진영·실시간 연결을 쓸 수 없다.
    for response in (call(late, "GET", f"/debates/{debate_id}/messages"), say(late, debate_id, "몰래"),
                     side(late, debate_id, "a"), call(late, "GET", f"/debates/{debate_id}/stream")):
        assert response.status_code == 403 and response.json()["code"] == "DEBATE_NOT_JOINED"

    before = say(first, debate_id, "참여 전 채팅").json()["messageId"]
    joined = join(late, debate_id).json()
    assert (joined["memberCount"], joined["joined"]) == (4, True)
    assert join(late, debate_id).json()["memberCount"] == 4  # 두 번 참여해도 한 번
    assert call(late, "GET", f"/debates/{debate_id}/messages").json()["items"] == []  # 참여 전 채팅은 안 보인다
    after = say(second, debate_id, "참여 후 채팅").json()["messageId"]
    assert [item["messageId"] for item in call(late, "GET", f"/debates/{debate_id}/messages").json()["items"]] == [after]
    # 다시 연결하며 놓친 채팅을 받을 때도 참여 전 것은 빼고 보낸다.
    response = finish(*open_stream(debate_api, late, debate_id, params={"after": 0}))
    assert [event_id for kind, event_id, _ in events(response.text) if kind == "chat"] == [str(after)]
    assert before < after


def test_leaving_drops_the_member_and_side_and_rejoining_starts_fresh(debate_api, fast_stream):
    debate_id, _author, (first, second) = open_room(debate_api)
    side(first, debate_id, "a")
    old = say(first, debate_id, "나가기 전 채팅").json()["messageId"]
    thread, result = open_stream(debate_api, second, debate_id)

    left = leave(first, debate_id).json()
    assert (left["joined"], left["memberCount"], left["mySide"], left["sideA"]["count"], left["participantCount"]) == (False, 2, None, 0, 0)
    assert leave(first, debate_id).status_code == 200  # 이미 나갔어도 그대로
    # 나간 사람의 채팅은 다른 사람 화면에 남는다.
    assert [item["messageId"] for item in call(second, "GET", f"/debates/{debate_id}/messages").json()["items"]] == [old]
    assert call(first, "GET", f"/debates/{debate_id}/messages").json()["code"] == "DEBATE_NOT_JOINED"
    # 진영이 취소되면 방에 있는 사람들에게 새 비율이 간다.
    response = finish(thread, result)
    assert ("sides", None, {"sideA": {"label": "나루토", "count": 0, "percent": None},
                            "sideB": {"label": "사스케", "count": 0, "percent": None}, "participantCount": 0}) in events(response.text)

    # 다시 참여하면 그때부터 새로 시작한다 (나가기 전 채팅은 안 보인다).
    rejoined = join(first, debate_id).json()
    assert (rejoined["joined"], rejoined["memberCount"], rejoined["mySide"]) == (True, 3, None)
    assert call(first, "GET", f"/debates/{debate_id}/messages").json()["items"] == []


def test_leaving_ends_the_members_own_live_connection(debate_api, fast_stream):
    debate_id, _author, (first, _second) = open_room(debate_api)
    thread, result = open_stream(debate_api, first, debate_id)
    leave(first, debate_id)
    thread.join(5)  # 허브가 이 사람의 연결만 끝낸다 (close_all 없이)
    assert not thread.is_alive() and result["response"].status_code == 200
    assert hub.count() == 0


def test_join_and_leave_only_while_the_room_is_open(debate_api):
    debate_id, _author, (first, _second) = open_room(debate_api)
    voting = create(debate_api.member(), title="투표 중인 주제").json()["debateId"]
    assert join(first, voting).json()["code"] == "DEBATE_NOT_OPEN"
    assert leave(first, voting).json()["code"] == "DEBATE_NOT_OPEN"
    assert call(first, "PUT", "/debates/999/membership").json()["code"] == "DEBATE_NOT_FOUND"
    no_csrf = first.client.put(f"/api/v1/debates/{debate_id}/membership")
    assert no_csrf.status_code == 403 and no_csrf.json()["code"] == "CSRF_INVALID"
    debate_api.clock.now = START + timedelta(hours=48)
    assert leave(first, debate_id).json()["code"] == "DEBATE_NOT_OPEN"  # 끝난 방의 진영(결과)은 바꿀 수 없다
