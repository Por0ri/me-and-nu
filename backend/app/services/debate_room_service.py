"""토론방 안: 진영 고르기, 채팅, 실시간 스트림 (설계 docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 3장, D-09~12).

- 토론방에 참여(D-18)한 사람만 채팅·진영 고르기·실시간 연결을 할 수 있다. 카카오톡 오픈채팅처럼 참여한 순간부터의
  채팅만 보인다. 방을 나가면(D-19) 참여 기록과 고른 진영이 지워지고, 다시 참여하면 그 순간부터 다시 시작한다.
- 진영 선택은 채팅과 별개다. 진영을 안 골라도 채팅할 수 있고, 진영은 언제든 바꾼다.
- 같은 방의 진영·채팅은 토론 줄을 잠그고 저장한다. 그래서 메시지 번호 순서와 커밋 순서가 같다.
- 커밋이 끝난 뒤에만 허브에 발행한다 (롤백된 메시지가 화면에 나오지 않게).
- 스트림은 DB로 할 일(방 확인, 놓친 메시지 읽기)을 먼저 끝내고 DB 연결을 풀에 돌려준 뒤에 기다린다.
  FastAPI는 스트리밍 응답이 끝날 때까지 get_db 세션을 닫지 않고, 실서비스 DB 연결은 2개(여유분 0)뿐이다.
- 시간은 debate_service.utcnow를 함께 쓴다 (테스트가 한 곳만 바꾸면 되게).
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ApiError
from app.models import Debate, DebateMember, DebateMessage, DebateSide, UserAccount
from app.schemas.debate import DebateMessagesResponse, DebateMessageView, DebateSummary
from app.services import debate_service as core
from app.services.cursor import decode_cursor, encode_cursor
from app.services.debate_hub import CLOSE, HubEvent, StreamLimitError, Subscriber, hub

REPLAY_LIMIT = 100
# 끊겼을 때 브라우저가 다시 붙기까지 기다리는 시간. 1초 + 무작위(흩는 폭)로 연결마다 다르게 정한다.
# 서버를 다시 시작하면 열려 있던 연결이 한꺼번에 다시 붙는데, 모두 같은 간격이면 DB 연결(2개) 앞에
# 줄이 막혀 시간 초과(500)가 난다 (부하 시험, 설계 3.5). 흩는 폭은 서버의 연결 수에 맞춰 넓힌다:
# 연결이 적으면 1~3초(거의 기다리지 않게), 연결 1개당 10ms씩 넓혀 700개 이상이면 1~8초.
RETRY_BASE_MILLISECONDS = 1000
RETRY_SPREAD_MIN_MILLISECONDS = 2000
RETRY_SPREAD_MAX_MILLISECONDS = 7000
RETRY_SPREAD_PER_CONNECTION_MILLISECONDS = 10
# 밀렸을 때 한 번에 묶어 보내는 이벤트 수
BATCH_MAX = 50


def _not_open() -> ApiError:
    return ApiError(409, "DEBATE_NOT_OPEN", "진행 중인 토론방이 아닙니다.")


async def _open_debate(db: AsyncSession, debate_id: int, *, lock: bool) -> Debate:
    debate = await core._lock_debate(db, debate_id) if lock else await db.get(Debate, debate_id)
    if debate is None:
        raise core._not_found()
    if core.effective_status(debate, core.utcnow()) != "open":
        raise _not_open()
    return debate


def _not_joined() -> ApiError:
    return ApiError(403, "DEBATE_NOT_JOINED", "토론에 참여한 사람만 할 수 있어요. 먼저 토론에 참여해 주세요.")


async def _member(db: AsyncSession, debate_id: int, user_id: int) -> DebateMember:
    member = await db.scalar(select(DebateMember).where(DebateMember.debate_id == debate_id, DebateMember.user_id == user_id))
    if member is None:
        raise _not_joined()
    return member


def _sides_payload(debate: Debate) -> dict:
    percent_a, percent_b = core._percents(debate.side_a_count, debate.side_b_count)
    return {
        "sideA": {"label": debate.side_a_label, "count": debate.side_a_count, "percent": percent_a},
        "sideB": {"label": debate.side_b_label, "count": debate.side_b_count, "percent": percent_b},
        "participantCount": debate.side_a_count + debate.side_b_count,
    }


def _view(message: DebateMessage, nickname: str | None, user_id: int) -> DebateMessageView:
    return DebateMessageView(
        message_id=message.message_id, side=message.side, nickname=nickname or core.UNKNOWN_NICKNAME, body=message.body,
        created_at=core._aware(message.created_at), is_mine=message.user_id == user_id,
    )


def _messages_with_nickname():
    # 탈퇴한(유예 중 포함) 회원의 채팅은 남기되 닉네임은 '알 수 없음'으로 보여 준다.
    nickname = case((UserAccount.account_status.in_(core.ACTIVE_STATUSES), UserAccount.nickname), else_=None)
    return select(DebateMessage, nickname).join(UserAccount, UserAccount.user_id == DebateMessage.user_id)


# ───────────── 진영 (D-09) ─────────────

async def choose_side(db: AsyncSession, user_id: int, debate_id: int, side: str) -> DebateSummary:
    now = core.utcnow()
    debate = await _open_debate(db, debate_id, lock=True)
    await _member(db, debate_id, user_id)
    current = (await db.execute(
        select(DebateSide).where(DebateSide.debate_id == debate_id, DebateSide.user_id == user_id)
    )).scalar_one_or_none()
    changed = current is None or current.side != side
    if current is None:
        db.add(DebateSide(debate_id=debate_id, user_id=user_id, side=side, chosen_at=now, updated_at=now))
    elif current.side != side:
        if current.side == "a":
            debate.side_a_count -= 1
        else:
            debate.side_b_count -= 1
        current.side = side
        current.updated_at = now
    if changed:
        if side == "a":
            debate.side_a_count += 1
        else:
            debate.side_b_count += 1
    await db.commit()
    if changed:
        hub.publish(debate_id, HubEvent(kind="sides", data=_sides_payload(debate)))
    return (await core._summaries(db, user_id, [debate], now))[0]


# ───────────── 참여·나가기 (D-18·19) ─────────────

async def join_room(db: AsyncSession, user_id: int, debate_id: int) -> DebateSummary:
    """토론방에 참여한다. 이미 참여 중이면 그대로 둔다. 참여한 순간 방의 마지막 채팅 번호를 적어 두고 그 뒤의 채팅만 보여 준다.
    토론 줄을 잠근 채 적으므로(채팅 저장도 같은 줄을 잠근다) 그 사이에 저장된 채팅이 끼어들지 않는다."""
    now = core.utcnow()
    debate = await _open_debate(db, debate_id, lock=True)
    joined = await db.scalar(select(DebateMember.user_id).where(DebateMember.debate_id == debate_id, DebateMember.user_id == user_id))
    if joined is None:
        last = await db.scalar(select(func.max(DebateMessage.message_id)).where(DebateMessage.debate_id == debate_id))
        db.add(DebateMember(debate_id=debate_id, user_id=user_id, history_after_message_id=last or 0, joined_at=now))
        await db.commit()
    return (await core._summaries(db, user_id, [debate], now))[0]


async def leave_room(db: AsyncSession, user_id: int, debate_id: int) -> DebateSummary:
    """토론방을 나간다. 참여 기록과 고른 진영을 지운다(진영 비율에서 빠진다). 보낸 채팅은 다른 사람 화면에 그대로 남는다.
    참여 중이 아니어도 그대로 200. 열려 있는 이 사람의 실시간 연결은 끊는다."""
    now = core.utcnow()
    debate = await _open_debate(db, debate_id, lock=True)
    left = (await db.execute(
        delete(DebateMember).where(DebateMember.debate_id == debate_id, DebateMember.user_id == user_id)
        .execution_options(synchronize_session=False)
    )).rowcount
    side = await db.scalar(select(DebateSide.side).where(DebateSide.debate_id == debate_id, DebateSide.user_id == user_id))
    if side is not None:
        await db.execute(
            delete(DebateSide).where(DebateSide.debate_id == debate_id, DebateSide.user_id == user_id)
            .execution_options(synchronize_session=False)
        )
        if side == "a":
            debate.side_a_count -= 1
        else:
            debate.side_b_count -= 1
    if left or side is not None:
        await db.commit()
    if side is not None:
        hub.publish(debate_id, HubEvent(kind="sides", data=_sides_payload(debate)))
    if left:
        hub.close_user(debate_id, user_id)
    return (await core._summaries(db, user_id, [debate], now))[0]


# ───────────── 채팅 (D-10·11) ─────────────

async def list_messages(db: AsyncSession, user_id: int, debate_id: int, *, cursor: str | None, limit: int) -> DebateMessagesResponse:
    """참여한 뒤의 최근 메시지를 오래된 순으로 준다. 더 이전 것은 nextCursor로. 끝난 방의 채팅은 보여 주지 않는다."""
    await _open_debate(db, debate_id, lock=False)
    member = await _member(db, debate_id, user_id)
    context = {"v": 1, "resource": "debate-messages", "debateId": debate_id}
    statement = _messages_with_nickname().where(
        DebateMessage.debate_id == debate_id, DebateMessage.message_id > member.history_after_message_id,
    )
    if cursor is not None:
        before = decode_cursor(cursor, expected=context).get("before")
        if type(before) is not int or before <= 0:
            raise ApiError(422, "INVALID_CURSOR", "유효하지 않은 cursor입니다.")
        statement = statement.where(DebateMessage.message_id < before)
    rows = (await db.execute(statement.order_by(DebateMessage.message_id.desc()).limit(limit + 1))).all()
    page = rows[:limit]
    next_cursor = encode_cursor({**context, "before": page[-1][0].message_id}) if len(rows) > limit and page else None
    return DebateMessagesResponse(items=[_view(message, nickname, user_id) for message, nickname in reversed(page)], next_cursor=next_cursor)


async def send_message(db: AsyncSession, user_id: int, debate_id: int, body: str) -> DebateMessageView:
    now = core.utcnow()
    debate = await _open_debate(db, debate_id, lock=True)
    await _member(db, debate_id, user_id)
    window = timedelta(seconds=settings.debate_message_rate_window_seconds)
    recent = (await db.execute(
        select(DebateMessage.created_at).where(
            DebateMessage.debate_id == debate_id, DebateMessage.user_id == user_id, DebateMessage.created_at > now - window,
        ).order_by(DebateMessage.created_at)
    )).scalars().all()
    if len(recent) >= settings.debate_message_rate_count:
        retry = max(1, math.ceil((core._aware(recent[0]) + window - now).total_seconds()))
        raise ApiError(429, "DEBATE_MESSAGE_RATE_LIMITED", f"메시지를 너무 빨리 보내고 있어요. {retry}초 뒤에 다시 보내 주세요.")
    # 보낸 순간의 진영 (고르지 않았으면 비어 있다)
    side = await db.scalar(select(DebateSide.side).where(DebateSide.debate_id == debate_id, DebateSide.user_id == user_id))
    nickname = await db.scalar(select(UserAccount.nickname).where(UserAccount.user_id == user_id))
    message = DebateMessage(debate_id=debate_id, user_id=user_id, side=side, body=body, created_at=now)
    db.add(message)
    debate.message_count += 1
    message_count = debate.message_count
    await db.flush()
    await db.commit()
    view = _view(message, nickname, user_id)
    # 방의 채팅 수도 함께 보낸다 (토론 줄을 잠그고 센 값이라 정확하다). isMine은 받는 사람마다 스트림이 붙인다.
    data = {**view.model_dump(by_alias=True, mode="json", exclude={"is_mine"}), "messageCount": message_count}
    hub.publish(debate_id, HubEvent(kind="chat", data=data, event_id=message.message_id, sender_user_id=user_id))
    return view


# ───────────── 실시간 스트림 (D-12, SSE) ─────────────

@dataclass
class StreamPlan:
    subscriber: Subscriber
    replay: list[DebateMessageView]
    closes_at: datetime
    session_expires_at: datetime
    # 화면이 이미 가진 마지막 채팅 번호 (after·Last-Event-ID). 이 번호까지는 다시 보내지 않는다.
    known_upto: int
    # 놓친 채팅이 REPLAY_LIMIT개를 넘었다: 하나씩 다시 보내지 않고 화면에 채팅을 새로 불러오라고 알린다.
    resync: bool = False


async def prepare_stream(
    db: AsyncSession, user_id: int, debate_id: int, *, resume_after: int | None, session_expires_at: datetime,
) -> StreamPlan:
    """구독을 먼저 등록하고 놓친 메시지를 읽은 뒤 DB 연결을 돌려준다 (구독 → 다시 읽기 순서라 빈틈이 없다)."""
    debate = await _open_debate(db, debate_id, lock=False)
    member = await _member(db, debate_id, user_id)
    if resume_after is not None:
        # 참여하기 전의 채팅은 다시 보내지 않는다.
        resume_after = max(resume_after, member.history_after_message_id)
    try:
        subscriber = hub.subscribe(debate_id, user_id, settings.debate_stream_per_user)
    except StreamLimitError as exc:
        raise ApiError(429, "DEBATE_STREAM_LIMIT", "동시에 열 수 있는 실시간 연결 수를 넘었습니다. 다른 창을 닫고 다시 시도해 주세요.") from exc
    try:
        replay: list[DebateMessageView] = []
        resync = False
        if resume_after is not None:
            rows = (await db.execute(
                _messages_with_nickname()
                .where(DebateMessage.debate_id == debate_id, DebateMessage.message_id > resume_after)
                .order_by(DebateMessage.message_id).limit(REPLAY_LIMIT + 1)
            )).all()
            if len(rows) > REPLAY_LIMIT:
                resync = True
            else:
                replay = [_view(message, nickname, user_id) for message, nickname in rows]
        closes_at = core._aware(debate.closes_at)
        # 여기서부터는 DB를 쓰지 않는다. 연결을 풀에 돌려줘야 스트림이 열려 있어도 다른 요청이 막히지 않는다.
        # (롤백하면 세션에 읽어 둔 객체가 만료되므로, 필요한 값은 위에서 모두 꺼내 두었다.)
        await db.rollback()
    except BaseException:
        hub.unsubscribe(subscriber)
        raise
    return StreamPlan(subscriber, replay, closes_at, core._aware(session_expires_at), resume_after or 0, resync)


def retry_milliseconds() -> int:
    spread = hub.count() * RETRY_SPREAD_PER_CONNECTION_MILLISECONDS
    spread = min(RETRY_SPREAD_MAX_MILLISECONDS, max(RETRY_SPREAD_MIN_MILLISECONDS, spread))
    return RETRY_BASE_MILLISECONDS + random.randint(0, spread)


def _frame(event: str, data: dict, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


async def stream_events(plan: StreamPlan) -> AsyncIterator[str]:
    """SSE 본문. 채팅(chat)·진영 비율(sides)을 보내고, 방이 끝나면 closed, 로그인 세션이 끝나면
    session-expired를 보내고 닫는다. 기다리는 동안 일정 간격으로 빈 신호(: ping)를 보낸다.

    서버가 연결을 끝내도 브라우저(EventSource)는 retry 뒤에 다시 붙는다. 그래서 closed·session-expired를
    받은 화면은 직접 연결을 닫아야 한다. 다시 붙더라도 끝난 방은 409, 끝난 세션은 401이라 거기서 멈춘다."""
    subscriber = plan.subscriber
    subscriber.started = True
    replayed = {message.message_id for message in plan.replay}
    try:
        yield f"retry: {retry_milliseconds()}\n\n"
        if plan.resync:
            yield _frame("resync", {})
        for message in plan.replay:
            yield _frame("chat", message.model_dump(by_alias=True, mode="json"), event_id=message.message_id)
        while True:
            if subscriber.queue.empty():
                # 받아 둔 이벤트를 다 보낸 뒤에만 종료 시각을 본다 (끝나기 직전 채팅도 빠짐없이 보내게).
                now = core.utcnow()
                if now >= plan.closes_at:
                    yield _frame("closed", {"debateId": subscriber.debate_id})
                    return
                if now >= plan.session_expires_at:
                    yield _frame("session-expired", {})
                    return
                wait = min(settings.debate_stream_heartbeat_seconds, (min(plan.closes_at, plan.session_expires_at) - now).total_seconds())
                try:
                    batch = [await asyncio.wait_for(subscriber.queue.get(), timeout=max(0.05, wait))]
                except asyncio.TimeoutError:
                    # 빈 신호에 지금 연결 수 기준의 재연결 간격을 같이 실어, 연결이 늘어나면 이미 열린 연결도 넓게 흩는다.
                    yield f"retry: {retry_milliseconds()}\n: ping\n\n"
                    continue
            else:
                batch = []
            # 밀려 있으면 쌓인 이벤트를 한 번에 묶어 보낸다. 보내는 횟수가 줄어서 바쁠 때 따라잡기 쉽다.
            while len(batch) < BATCH_MAX and not subscriber.queue.empty():
                batch.append(subscriber.queue.get_nowait())
            chunks: list[str] = []
            closing = False
            for event in batch:
                if event is CLOSE:
                    closing = True
                    break
                # 화면이 이미 가진 것, 방금 다시 보낸 것과 겹치면 건너뛴다. 번호 크기만으로 거르지 않는 까닭:
                # 거의 동시에 저장된 두 채팅은 방송 순서가 번호 순서와 드물게 다를 수 있다 (화면은 번호순으로 정렬한다).
                if event.kind == "chat" and event.event_id is not None and (
                    event.event_id <= plan.known_upto or event.event_id in replayed
                ):
                    continue
                chunks.append(_render(event, subscriber.user_id))
            if chunks:
                yield "".join(chunks)
            if closing:
                return
    finally:
        hub.unsubscribe(subscriber)


def _render(event: HubEvent, user_id: int) -> str:
    """받는 사람에게 보낼 SSE 글. 같은 방 연결 수백 개가 같은 글을 쓰므로, 이벤트마다 받는 쪽 종류별로
    (채팅은 보낸 사람·다른 사람 2가지, 그 밖은 1가지) 한 번만 만들어 둔다."""
    key = event.sender_user_id == user_id if event.kind == "chat" else None
    frame = event.frames.get(key)
    if frame is None:
        data = event.data if key is None else {**event.data, "isMine": key}
        frame = event.frames[key] = _frame(event.kind, data, event.event_id)
    return frame
