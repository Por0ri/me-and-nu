"""토론 정리 작업 (설계 docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 5.3). 서버에서 10분마다 돈다.

1. 투표 마감이 지난 주제: voting → expired (소각)
2. 방 종료 시각이 지난 방: open → closed, 그 방의 채팅 삭제 (채팅은 남기지 않는다)
3. 끝난 지 보관 기간(기본 9일)이 지난 토론: 통째로 삭제. 투표·진영·관련 작품·개설 알림 연결은 외래 키로 함께 지워지고,
   알림함의 개설 알림 글은 남되 '토론방 보러 가기' 연결만 없어진다.

화면과 API는 저장된 상태가 늦어도 시각으로 계산해서 보여 주므로(설계 4장), 이 작업이 늦게 돌아도 보이는 결과는 같다.
모두 조건을 건 UPDATE·DELETE라서, API가 같은 토론 줄을 잠그고 있으면 기다렸다가 조건을 다시 확인한다
(마감 직전의 10번째 표나 마지막 채팅과 겹쳐도 안전하다). 여러 번 돌아도 결과가 같다.
트랜잭션은 호출한 쪽이 맡는다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Debate, DebateMessage
from app.services import debate_service as core


async def finalize_debates(db: AsyncSession, *, apply: bool = False, now: datetime | None = None) -> dict:
    """apply=False면 할 일의 개수만 센다 (미리보기). 결과에는 개수만 담는다."""
    now = now or core.utcnow()
    to_expire = and_(Debate.status == "voting", Debate.voting_ends_at <= now)
    to_close = and_(Debate.status == "open", Debate.closes_at <= now)
    to_purge = core.ended_at_column() <= core.keep_after(now)
    report = {"mode": "apply" if apply else "dry_run", "retention_days": settings.debate_result_retention_days}

    if not apply:
        async def count(condition) -> int:
            return await db.scalar(select(func.count()).select_from(Debate).where(condition))

        report["expired"] = await count(to_expire)
        report["closed"] = await count(to_close)
        # 끝난 방(저장 상태가 아직 open이어도 시각이 지난 방)에 남은 채팅
        report["deleted_messages"] = await db.scalar(
            select(func.count()).select_from(DebateMessage).join(Debate, Debate.debate_id == DebateMessage.debate_id)
            .where(core._closed_now(now))
        )
        report["purged_debates"] = await count(to_purge)
        return report

    def change(condition, status: str):
        return update(Debate).where(condition).values(status=status, updated_at=now).execution_options(synchronize_session=False)

    report["expired"] = (await db.execute(change(to_expire, "expired"))).rowcount
    report["closed"] = (await db.execute(change(to_close, "closed"))).rowcount
    # 이번에 닫은 방뿐 아니라 닫힌 방에 남은 채팅을 모두 지운다 (마감 순간에 저장된 채팅까지).
    report["deleted_messages"] = (await db.execute(
        delete(DebateMessage)
        .where(DebateMessage.debate_id.in_(select(Debate.debate_id).where(Debate.status == "closed")))
        .execution_options(synchronize_session=False)
    )).rowcount
    report["purged_debates"] = (await db.execute(
        delete(Debate).where(to_purge).execution_options(synchronize_session=False)
    )).rowcount
    return report
