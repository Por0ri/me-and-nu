"""토론방 실시간 방송 허브 (1차: 서버 프로세스 메모리, 설계 docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 3.1).

방마다 구독자(열린 SSE 연결)의 대기열을 두고, 커밋이 끝난 이벤트만 넣는다.

- API 프로세스가 1개(--workers 1)이고 토론 기능을 한 환경에서만 켤 때만 맞다. 프로세스가 둘 이상이거나
  같은 DB를 쓰는 다른 서버에서도 켜면 PostgreSQL LISTEN/NOTIFY 허브로 바꿔야 한다 (2차).
- 받는 쪽이 느려 대기열(QUEUE_SIZE)이 차면 그 연결을 끝낸다. 브라우저가 다시 붙으면서 놓친 메시지를
  Last-Event-ID로 받아간다.
- 요청이 다른 이벤트 루프(테스트의 TestClient마다 다른 스레드)에서 올 수 있어서, 다른 루프의 대기열에는
  그 루프에 맡겨 넣는다. 운영에서는 루프가 하나라 바로 넣는다.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

QUEUE_SIZE = 100
# 구독은 응답을 보내기 전에 등록한다(놓친 메시지와 새 메시지 사이에 빈틈이 없게). 그런데 응답이 시작되기 전에
# 사용자가 떠나면 스트림의 정리 코드가 아예 돌지 않는다. 이렇게 시작하지 못한 구독은 이 시간이 지나면 치운다.
UNSTARTED_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class HubEvent:
    """kind: chat(채팅) · sides(진영 비율). 채팅은 메시지 번호를 SSE id로 쓴다."""

    kind: str
    data: dict[str, Any]
    event_id: int | None = None
    # 받는 사람마다 isMine을 계산하려고 둔다. 밖으로는 보내지 않는다 (다른 회원 번호를 노출하지 않음).
    sender_user_id: int | None = None
    # 완성된 SSE 글을 받는 쪽 종류(보낸 사람·다른 사람)별로 한 번만 만들어 같은 방 연결들이 나눠 쓴다 (부하 시험 3.5).
    frames: dict[Any, str] = field(default_factory=dict, compare=False, repr=False)


# 허브가 연결을 끝내라고 넣는 신호
CLOSE = HubEvent(kind="_close", data={})


class StreamLimitError(Exception):
    """한 회원이 동시에 열 수 있는 실시간 연결 수를 넘었다."""


@dataclass(eq=False)
class Subscriber:
    debate_id: int
    user_id: int
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))
    overflowed: bool = False
    # 스트림이 실제로 흐르기 시작했는지. 시작하지 못한 채 오래된 구독은 허브가 치운다.
    started: bool = False
    registered_at: float = field(default_factory=time.monotonic)


class DebateHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rooms: dict[int, set[Subscriber]] = defaultdict(set)
        self._per_user: Counter[int] = Counter()

    def subscribe(self, debate_id: int, user_id: int, limit: int) -> Subscriber:
        subscriber = Subscriber(debate_id=debate_id, user_id=user_id, loop=asyncio.get_running_loop())
        with self._lock:
            self._drop_unstarted()
            if self._per_user[user_id] >= limit:
                raise StreamLimitError
            self._rooms[debate_id].add(subscriber)
            self._per_user[user_id] += 1
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            self._remove(subscriber)

    def _remove(self, subscriber: Subscriber) -> None:
        room = self._rooms.get(subscriber.debate_id)
        if not room or subscriber not in room:
            return
        room.discard(subscriber)
        if not room:
            del self._rooms[subscriber.debate_id]
        self._per_user[subscriber.user_id] -= 1
        if self._per_user[subscriber.user_id] <= 0:
            del self._per_user[subscriber.user_id]

    def _drop_unstarted(self) -> None:
        deadline = time.monotonic() - UNSTARTED_TIMEOUT_SECONDS
        stale = [subscriber for room in self._rooms.values() for subscriber in room
                 if not subscriber.started and subscriber.registered_at < deadline]
        for subscriber in stale:
            self._remove(subscriber)

    def count(self, debate_id: int | None = None) -> int:
        with self._lock:
            if debate_id is None:
                return sum(len(room) for room in self._rooms.values())
            return len(self._rooms.get(debate_id, ()))

    def publish(self, debate_id: int, event: HubEvent) -> int:
        """커밋이 끝난 뒤에만 부른다 (롤백된 메시지가 화면에 나오지 않게). 받은 연결 수를 돌려준다."""
        with self._lock:
            subscribers = list(self._rooms.get(debate_id, ()))
        for subscriber in subscribers:
            self._deliver(subscriber, event)
        return len(subscribers)

    def close_user(self, debate_id: int, user_id: int) -> int:
        """한 방에서 한 사람의 연결만 끝낸다 (방 나가기). 끝낸 연결 수를 돌려준다."""
        with self._lock:
            subscribers = [subscriber for subscriber in self._rooms.get(debate_id, ()) if subscriber.user_id == user_id]
        for subscriber in subscribers:
            self._deliver(subscriber, CLOSE)
        return len(subscribers)

    def close_all(self) -> None:
        """열린 연결을 모두 끝낸다 (테스트, 서버 정리용)."""
        with self._lock:
            subscribers = [subscriber for room in self._rooms.values() for subscriber in room]
        for subscriber in subscribers:
            self._deliver(subscriber, CLOSE)

    @staticmethod
    def _put(subscriber: Subscriber, event: HubEvent) -> None:
        if subscriber.overflowed:
            return
        try:
            subscriber.queue.put_nowait(event)
        except asyncio.QueueFull:
            # 너무 느린 연결: 쌓인 것을 버리고 끝 신호만 남긴다. 다시 붙으면 놓친 메시지를 받아간다.
            subscriber.overflowed = True
            while not subscriber.queue.empty():
                subscriber.queue.get_nowait()
            subscriber.queue.put_nowait(CLOSE)

    def _deliver(self, subscriber: Subscriber, event: HubEvent) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is subscriber.loop:
            self._put(subscriber, event)
            return
        try:
            subscriber.loop.call_soon_threadsafe(self._put, subscriber, event)
        except RuntimeError:
            # 그 연결의 루프가 이미 끝났다 (연결이 닫히는 중). 정리는 그 연결의 finally가 한다.
            pass


hub = DebateHub()
