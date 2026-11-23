"""토론방 투표 흐름 (설계 docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 4·6·7장).

- 응답의 상태는 저장된 status와 지금 시각으로 계산한다. 투표 마감·방 종료 시각이 지나면 정리 작업이
  아직 돌지 않았어도 끝난 것으로 본다.
- 같은 회원의 주제 올리기·투표·취소는 그 회원의 투표권 줄을 잠가 한 번에 하나씩 처리하고, 같은 주제의
  표 수는 주제 줄을 잠가 바꾼다. 잠금 순서는 항상 투표권 줄 → 주제 줄이다 (교착 방지).
- 10번째 표는 표 저장 → 개설 → 투표한 사람·작성자 알림을 한 트랜잭션에서 처리한다.
- 규칙 값은 설정(DEBATE_*)에서 읽는다. 테스트는 설정과 utcnow를 바꿔 시간을 흘려 본다.
"""

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, exists, func, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ApiError
from app.models import (
    Debate, DebateMember, DebateNotification, DebateSearchLog, DebateSide, DebateVote, DebateVoteTicket, DebateWork,
    Entity, Notification, NotificationSetting, UserAccount,
)
from app.repositories import onboarding_catalog_repository as catalog_repo
from app.repositories.onboarding_repository import find_active_topic
from app.schemas.debate import (
    DebateCreateRequest, DebateListResponse, DebateSearchResponse, DebateSearchWork, DebateSideView,
    DebateSummary, DebateVoteResponse, DebateWorkView, VoteTicketsResponse,
)
from app.services.cursor import decode_cursor, encode_cursor

DEBATE_OPENED = "debate_opened"
ACTIVE_STATUSES = ("active", "warned")
# 탈퇴했거나(유예 중 포함) 지워진 회원의 닉네임 대신 보여 주는 이름
UNKNOWN_NICKNAME = "알 수 없음"
SEARCH_TEXT_MAX = 50
SEARCH_LIMIT = 30


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite 테스트는 시간대 없는 값을 돌려준다. 저장 값은 모두 UTC다."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _not_found() -> ApiError:
    return ApiError(404, "DEBATE_NOT_FOUND", "토론을 찾을 수 없습니다.")


# ───────────── 상태 ─────────────

def effective_status(debate: Debate, now: datetime) -> str:
    if debate.status == "voting" and _aware(debate.voting_ends_at) <= now:
        return "expired"
    if debate.status == "open" and debate.closes_at is not None and _aware(debate.closes_at) <= now:
        return "closed"
    return debate.status


def _voting_now(now: datetime):
    return and_(Debate.status == "voting", Debate.voting_ends_at > now)


def _open_now(now: datetime):
    return and_(Debate.status == "open", Debate.closes_at > now)


def _closed_now(now: datetime):
    return or_(Debate.status == "closed", and_(Debate.status == "open", Debate.closes_at <= now))


def _expired_now(now: datetime):
    return or_(Debate.status == "expired", and_(Debate.status == "voting", Debate.voting_ends_at <= now))


def ended_at_column():
    """끝나는(끝난) 시각: 열렸던 방은 방 종료 시각, 열리지 못한 주제는 투표 마감 시각."""
    return func.coalesce(Debate.closes_at, Debate.voting_ends_at)


def keep_after(now: datetime) -> datetime:
    """이 시각보다 뒤에 끝났어야 보여 준다 (끝난 뒤 보관 기간 9일)."""
    return now - timedelta(days=settings.debate_result_retention_days)


def _kept(now: datetime):
    """아직 진행 중이거나, 끝난 지 보관 기간이 안 지난 토론. 지난 것은 정리 작업이 돌기 전이라도 바로 숨긴다."""
    return ended_at_column() > keep_after(now)


def _is_kept(debate: Debate, now: datetime) -> bool:
    return _aware(debate.closes_at or debate.voting_ends_at) > keep_after(now)


def _shown(now: datetime):
    """보여 줄 토론: 투표 중·진행 중이거나, 방이 끝난 지 보관 기간 안인 것.
    7일 안에 기준 표를 못 받은 주제는 바로 삭제되는 규칙이라, 정리 작업이 지우기 전이라도 숨긴다."""
    return and_(~_expired_now(now), _kept(now))


def _is_shown(debate: Debate, now: datetime) -> bool:
    return effective_status(debate, now) != "expired" and _is_kept(debate, now)


def _percents(a: int, b: int) -> tuple[int | None, int | None]:
    total = a + b
    if total == 0:
        return None, None
    percent_a = int(a * 100 / total + 0.5)
    return percent_a, 100 - percent_a


def _result(debate: Debate, status: str) -> str | None:
    if status != "closed":
        return None
    if debate.side_a_count + debate.side_b_count == 0:
        return "none"
    if debate.side_a_count == debate.side_b_count:
        return "draw"
    return "a" if debate.side_a_count > debate.side_b_count else "b"


async def _summaries(db: AsyncSession, user_id: int, debates: list[Debate], now: datetime) -> list[DebateSummary]:
    """한 화면 분량을 한 번에 채운다: 작품, 작성자 닉네임, 참여 인원, 내 표, 내 진영, 내 참여."""
    if not debates:
        return []
    ids = [debate.debate_id for debate in debates]
    works: dict[int, list[DebateWorkView]] = defaultdict(list)
    rows = await db.execute(
        select(DebateWork.debate_id, Entity.entity_id, Entity.name)
        .join(Entity, Entity.entity_id == DebateWork.entity_id)
        .where(DebateWork.debate_id.in_(ids))
        .order_by(DebateWork.debate_id, DebateWork.position)
    )
    for debate_id, entity_id, name in rows.all():
        works[debate_id].append(DebateWorkView(entity_id=entity_id, name=name))
    voted = set((await db.execute(
        select(DebateVote.debate_id).where(DebateVote.user_id == user_id, DebateVote.debate_id.in_(ids))
    )).scalars().all())
    sides = dict((await db.execute(
        select(DebateSide.debate_id, DebateSide.side).where(DebateSide.user_id == user_id, DebateSide.debate_id.in_(ids))
    )).all())
    members = dict((await db.execute(
        select(DebateMember.debate_id, func.count()).where(DebateMember.debate_id.in_(ids)).group_by(DebateMember.debate_id)
    )).all())
    joined = set((await db.execute(
        select(DebateMember.debate_id).where(DebateMember.user_id == user_id, DebateMember.debate_id.in_(ids))
    )).scalars().all())
    author_ids = {debate.author_user_id for debate in debates if debate.author_user_id is not None}
    authors = dict((await db.execute(
        select(UserAccount.user_id, UserAccount.nickname)
        .where(UserAccount.user_id.in_(author_ids), UserAccount.account_status.in_(ACTIVE_STATUSES))
    )).all()) if author_ids else {}
    threshold = settings.debate_vote_threshold
    result = []
    for debate in debates:
        status = effective_status(debate, now)
        percent_a, percent_b = _percents(debate.side_a_count, debate.side_b_count)
        result.append(DebateSummary(
            debate_id=debate.debate_id,
            topic_id=debate.topic_id,
            title=debate.title,
            description=debate.description,
            side_a=DebateSideView(label=debate.side_a_label, count=debate.side_a_count, percent=percent_a),
            side_b=DebateSideView(label=debate.side_b_label, count=debate.side_b_count, percent=percent_b),
            works=works[debate.debate_id],
            status=status,
            vote_count=debate.vote_count,
            vote_threshold=threshold,
            voting_ends_at=_aware(debate.voting_ends_at),
            opened_at=_aware(debate.opened_at),
            closes_at=_aware(debate.closes_at),
            participant_count=debate.side_a_count + debate.side_b_count,
            member_count=members.get(debate.debate_id, 0),
            joined=debate.debate_id in joined,
            message_count=debate.message_count,
            result=_result(debate, status),
            my_vote=debate.debate_id in voted,
            my_side=sides.get(debate.debate_id),
            is_mine=debate.author_user_id == user_id,
            # 탈퇴했거나(유예 중 포함) 지워진 작성자는 닉네임을 보여 주지 않는다.
            author_nickname=authors.get(debate.author_user_id) or UNKNOWN_NICKNAME,
            created_at=_aware(debate.created_at),
        ))
    return result


# ───────────── 투표권 (설계 6.2) ─────────────

def _refill() -> timedelta:
    return timedelta(minutes=settings.debate_vote_ticket_refill_minutes)


def _ticket_state(row: DebateVoteTicket | None, now: datetime) -> tuple[int, datetime]:
    """(지금 개수, 충전 기준 시각). 줄이 없거나 가득이면 최대치이고 시계는 멈춰 있다."""
    maximum = settings.debate_vote_ticket_max
    if row is None or row.balance >= maximum:
        return maximum, now
    refilled = _aware(row.refilled_at)
    refills = max(0, int((now - refilled) / _refill()))
    balance = min(maximum, row.balance + refills)
    return balance, refilled + refills * _refill()


def _tickets_view(row: DebateVoteTicket | None, now: datetime) -> VoteTicketsResponse:
    balance, anchor = _ticket_state(row, now)
    maximum = settings.debate_vote_ticket_max
    return VoteTicketsResponse(balance=balance, max=maximum, next_refill_at=anchor + _refill() if balance < maximum else None)


async def _lock_ticket(db: AsyncSession, user_id: int, now: datetime) -> DebateVoteTicket:
    """그 회원의 투표권 줄을 잠근다. 처음이면 가득 찬 줄을 만든다 (동시에 만들면 한쪽은 다시 읽는다)."""
    statement = select(DebateVoteTicket).where(DebateVoteTicket.user_id == user_id).with_for_update()
    row = (await db.execute(statement.execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None:
        try:
            async with db.begin_nested():
                db.add(DebateVoteTicket(user_id=user_id, balance=settings.debate_vote_ticket_max, refilled_at=now))
                await db.flush()
        except IntegrityError:
            pass
        row = (await db.execute(statement.execution_options(populate_existing=True))).scalar_one()
    return row


async def _lock_debate(db: AsyncSession, debate_id: int) -> Debate:
    debate = (await db.execute(
        select(Debate).where(Debate.debate_id == debate_id).with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if debate is None:
        raise _not_found()
    return debate


async def get_tickets(db: AsyncSession, user_id: int) -> VoteTicketsResponse:
    row = await db.get(DebateVoteTicket, user_id)
    return _tickets_view(row, utcnow())


# ───────────── 주제 올리기·삭제 ─────────────

async def create_debate(db: AsyncSession, user_id: int, payload: DebateCreateRequest) -> DebateSummary:
    now = utcnow()
    if await find_active_topic(db, payload.topic_id) is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.")
    if payload.work_ids:
        found = await catalog_repo.find_active_works(db, payload.topic_id, payload.work_ids, lock=True)
        if len(found) != len(payload.work_ids):
            raise ApiError(422, "VALIDATION_ERROR", "작품은 이 분야의 작품 목록에서 골라 주세요.", fields=[
                {"field": "workIds", "code": "invalid", "message": "작품 목록에 없는 작품이 있습니다."},
            ])
    # 투표권 줄을 그 회원 전용 자물쇠로 써서, 동시에 두 번 올려도 진행 중 주제가 한도를 넘지 않게 한다.
    await _lock_ticket(db, user_id, now)
    active = await db.scalar(
        select(func.count()).select_from(Debate).where(Debate.author_user_id == user_id, _voting_now(now))
    )
    if (active or 0) >= settings.debate_active_per_user:
        raise ApiError(409, "DEBATE_ACTIVE_LIMIT", "투표 중인 내 주제가 이미 있습니다. 그 주제가 열리거나 소각되면 새로 올릴 수 있습니다.")
    debate = Debate(
        topic_id=payload.topic_id,
        author_user_id=user_id,
        title=payload.title,
        description=payload.description,
        side_a_label=payload.side_a,
        side_b_label=payload.side_b,
        status="voting",
        voting_ends_at=now + timedelta(minutes=settings.debate_voting_period_minutes),
        created_at=now,
        updated_at=now,
    )
    db.add(debate)
    await db.flush()
    for position, entity_id in enumerate(payload.work_ids):
        db.add(DebateWork(debate_id=debate.debate_id, entity_id=entity_id, topic_id=payload.topic_id, position=position))
    await db.commit()
    return (await _summaries(db, user_id, [debate], now))[0]


async def delete_debate(db: AsyncSession, user_id: int, debate_id: int) -> None:
    now = utcnow()
    debate = await _lock_debate(db, debate_id)
    if debate.author_user_id != user_id:
        raise _not_found()
    # 표를 받은 주제는 지우지 않는다. 다른 사람의 투표권이 걸려 있다.
    if effective_status(debate, now) != "voting" or debate.vote_count > 0:
        raise ApiError(409, "DEBATE_DELETE_NOT_ALLOWED", "표를 받았거나 투표가 끝난 주제는 지울 수 없습니다.")
    await db.execute(delete(Debate).where(Debate.debate_id == debate_id))
    await db.commit()


# ───────────── 투표·취소 ─────────────

async def vote(db: AsyncSession, user_id: int, debate_id: int) -> DebateVoteResponse:
    now = utcnow()
    ticket = await _lock_ticket(db, user_id, now)
    debate = await _lock_debate(db, debate_id)
    status = effective_status(debate, now)
    if status == "expired":  # 기준 표를 못 받은 주제는 삭제된 것으로 본다
        raise _not_found()
    if status != "voting":
        raise ApiError(409, "DEBATE_NOT_VOTING", "투표가 끝났거나 이미 열린 주제입니다.")
    already = await db.scalar(select(DebateVote.debate_id).where(DebateVote.debate_id == debate_id, DebateVote.user_id == user_id))
    opened = False
    if already is None:
        balance, anchor = _ticket_state(ticket, now)
        if balance <= 0:
            minutes = max(1, math.ceil((anchor + _refill() - now).total_seconds() / 60))
            raise ApiError(409, "DEBATE_VOTE_TICKETS_EMPTY", f"투표권이 없습니다. {minutes}분 뒤에 1개 충전됩니다.")
        # 가득 찬 상태에서 처음 쓰면 그때부터 충전 시계가 돈다.
        ticket.refilled_at = now if balance >= settings.debate_vote_ticket_max else anchor
        ticket.balance = balance - 1
        db.add(DebateVote(debate_id=debate_id, user_id=user_id, created_at=now))
        debate.vote_count += 1
        await db.flush()
        if debate.vote_count >= settings.debate_vote_threshold:
            await _open(db, debate, now)
            opened = True
    await db.commit()
    return DebateVoteResponse(debate=(await _summaries(db, user_id, [debate], now))[0], tickets=_tickets_view(ticket, now), opened=opened)


async def cancel_vote(db: AsyncSession, user_id: int, debate_id: int) -> DebateVoteResponse:
    now = utcnow()
    ticket = await _lock_ticket(db, user_id, now)
    debate = await _lock_debate(db, debate_id)
    status = effective_status(debate, now)
    if status == "expired":  # 기준 표를 못 받은 주제는 삭제된 것으로 본다
        raise _not_found()
    if status != "voting":
        raise ApiError(409, "DEBATE_NOT_VOTING", "이미 열렸거나 끝난 주제라 취소할 수 없습니다.")
    removed = await db.execute(delete(DebateVote).where(DebateVote.debate_id == debate_id, DebateVote.user_id == user_id))
    if removed.rowcount:
        debate.vote_count -= 1
        # 취소하면 1개 즉시 돌려준다. 이미 가득이면 더 늘지 않는다.
        balance, anchor = _ticket_state(ticket, now)
        ticket.balance = min(settings.debate_vote_ticket_max, balance + 1)
        ticket.refilled_at = anchor if ticket.balance < settings.debate_vote_ticket_max else now
    await db.commit()
    return DebateVoteResponse(debate=(await _summaries(db, user_id, [debate], now))[0], tickets=_tickets_view(ticket, now))


async def _open(db: AsyncSession, debate: Debate, now: datetime) -> None:
    """기준 표 수를 채운 순간 연다. 투표한 사람과 작성자에게 알림을 한 번씩 만든다."""
    debate.status = "open"
    debate.opened_at = now
    debate.closes_at = now + timedelta(minutes=settings.debate_room_duration_minutes)
    voters = set((await db.execute(select(DebateVote.user_id).where(DebateVote.debate_id == debate.debate_id))).scalars().all())
    candidates = voters | ({debate.author_user_id} if debate.author_user_id is not None else set())
    if not candidates:
        return
    # 활동 중이고 앱 안 알림을 끄지 않은 회원만 (설정 줄이 없으면 켜진 것으로 본다).
    muted = exists().where(NotificationSetting.user_id == UserAccount.user_id, NotificationSetting.in_app_enabled.is_(False))
    recipients = (await db.execute(
        select(UserAccount.user_id).where(
            UserAccount.user_id.in_(candidates), UserAccount.account_status.in_(ACTIVE_STATUSES), ~muted,
        ).order_by(UserAccount.user_id)
    )).scalars().all()
    hours = settings.debate_room_duration_minutes // 60
    for user_id in recipients:
        about = "투표한" if user_id in voters else "내가 올린"
        notification = Notification(
            user_id=user_id,
            notification_type=DEBATE_OPENED,
            title="토론방이 열렸어요",
            body=f"{about} 주제 '{debate.title}'가 {settings.debate_vote_threshold}표를 모아 토론방이 열렸어요. "
                 f"{hours}시간 동안 토론할 수 있어요."[:500],
            topic_id=debate.topic_id,
            created_at=now,
        )
        db.add(notification)
        await db.flush()
        db.add(DebateNotification(notification_id=notification.notification_id, debate_id=debate.debate_id, user_id=user_id))
    await db.flush()


# ───────────── 조회 ─────────────

async def get_debate(db: AsyncSession, user_id: int, debate_id: int) -> DebateSummary:
    now = utcnow()
    debate = await db.get(Debate, debate_id)
    # 기준 표를 못 받은 주제, 끝난 지 보관 기간이 지난 방은 정리 작업이 지우기 전이라도 없는 것으로 본다.
    if debate is None or not _is_shown(debate, now):
        raise _not_found()
    return (await _summaries(db, user_id, [debate], now))[0]


def _contains(column, text: str):
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


async def list_debates(
    db: AsyncSession, user_id: int, *, status: str, sort: str, topic_id: int | None,
    entity_id: int | None, q: str | None, cursor: str | None, limit: int,
) -> DebateListResponse:
    now = utcnow()
    query = " ".join((q or "").split())[:SEARCH_TEXT_MAX]
    statement = select(Debate)
    if topic_id is not None:
        statement = statement.where(Debate.topic_id == topic_id)
    if entity_id is not None:
        statement = statement.where(exists().where(DebateWork.debate_id == Debate.debate_id, DebateWork.entity_id == entity_id))
    if query:
        statement = statement.where(or_(
            _contains(Debate.title, query), _contains(Debate.side_a_label, query), _contains(Debate.side_b_label, query),
        ))
    context = {"v": 1, "resource": "debates", "status": status, "sort": sort,
               "topicId": topic_id, "entityId": entity_id, "q": query.lower()}
    keyset = None
    if status == "voting":
        statement = statement.where(_voting_now(now))
        if sort == "votes":
            # 개설 임박순: 표가 많은 주제부터. 표 수가 계속 바뀌어서 다음 쪽(cursor)은 두지 않는다.
            statement = statement.order_by(Debate.vote_count.desc(), Debate.voting_ends_at, Debate.debate_id)
        else:
            keyset = (Debate.created_at, Debate.debate_id)
    elif status == "open":
        statement = statement.where(_open_now(now)).order_by(Debate.closes_at, Debate.debate_id)
    else:
        # 지난 토론방: 48시간이 끝난 방 중 끝난 지 보관 기간(9일) 안의 것만. 기준 표를 못 받은 주제는 바로 삭제돼 여기 없다.
        statement = statement.where(_closed_now(now), _kept(now))
        keyset = (ended_at_column(), Debate.debate_id)
    if keyset is not None:
        if cursor is not None:
            payload = decode_cursor(cursor, expected=context)
            try:
                after = (datetime.fromisoformat(payload["at"]), int(payload["id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ApiError(422, "INVALID_CURSOR", "유효하지 않은 cursor입니다.") from exc
            statement = statement.where(tuple_(*keyset) < after)
        statement = statement.order_by(keyset[0].desc(), keyset[1].desc())
    rows = list((await db.execute(statement.limit(limit + 1))).scalars().all())
    page = rows[:limit]
    next_cursor = None
    if keyset is not None and len(rows) > limit and page:
        last = page[-1]
        at = last.created_at if status == "voting" else (last.closes_at or last.voting_ends_at)
        next_cursor = encode_cursor({**context, "at": _aware(at).isoformat(), "id": last.debate_id})
    return DebateListResponse(items=await _summaries(db, user_id, page, now), next_cursor=next_cursor)


async def list_my_debates(db: AsyncSession, user_id: int, limit: int = 20) -> DebateListResponse:
    now = utcnow()
    rows = (await db.execute(
        select(Debate).where(Debate.author_user_id == user_id, _shown(now))
        .order_by(Debate.created_at.desc(), Debate.debate_id.desc()).limit(limit)
    )).scalars().all()
    return DebateListResponse(items=await _summaries(db, user_id, list(rows), now))


async def list_my_votes(db: AsyncSession, user_id: int, limit: int = 30) -> DebateListResponse:
    """내가 투표한 주제 중 아직 투표 중이거나 열려 있는 것. 투표 중인 것을 먼저 보여준다."""
    now = utcnow()
    rows = (await db.execute(
        select(Debate).join(DebateVote, and_(DebateVote.debate_id == Debate.debate_id, DebateVote.user_id == user_id))
        .where(or_(_voting_now(now), _open_now(now)))
        .order_by(Debate.status.desc(), Debate.voting_ends_at, Debate.debate_id)
        .limit(limit)
    )).scalars().all()
    return DebateListResponse(items=await _summaries(db, user_id, list(rows), now))


async def search(
    db: AsyncSession, user_id: int, *, topic_id: int, q: str, record: bool, picked_entity_id: int | None,
) -> DebateSearchResponse:
    """검색 입력창 하나 (설계 D-16): 이름이 맞는 작품과, 제목·선택지·연결 작품에 글자가 들어간 토론."""
    now = utcnow()
    query = " ".join(q.split())[:SEARCH_TEXT_MAX]
    if not query:
        return DebateSearchResponse(works=[], debates=[])
    if await find_active_topic(db, topic_id) is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.")
    works = await catalog_repo.search_works(db, topic_id, query=query, after_id=None, limit=5)
    work_ids = [work.entity_id for work in works]
    linked = exists().where(DebateWork.debate_id == Debate.debate_id, DebateWork.entity_id.in_(work_ids)) if work_ids else None
    text = or_(_contains(Debate.title, query), _contains(Debate.side_a_label, query), _contains(Debate.side_b_label, query))
    rows = list((await db.execute(
        select(Debate).where(Debate.topic_id == topic_id, _shown(now), or_(text, linked) if linked is not None else text)
        .order_by(Debate.created_at.desc(), Debate.debate_id.desc()).limit(SEARCH_LIMIT)
    )).scalars().all())
    rank = {"open": 0, "voting": 1, "closed": 2, "expired": 3}
    rows.sort(key=lambda debate: rank[effective_status(debate, now)])
    if record:
        # 사용자가 검색을 확정했을 때만 남긴다 (입력 완료 또는 작품 칩 선택). 누가 검색했는지는 남기지 않는다.
        db.add(DebateSearchLog(
            topic_id=topic_id, search_text=query, picked_entity_id=picked_entity_id if picked_entity_id in work_ids else None,
            matched_entity_ids=work_ids, result_count=len(rows), data_source=settings.event_data_source, searched_at=now,
        ))
        await db.commit()
    return DebateSearchResponse(
        works=[DebateSearchWork(entity_id=work.entity_id, name=work.name, aliases=list(work.aliases or [])) for work in works],
        debates=await _summaries(db, user_id, rows, now),
    )
