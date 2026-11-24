"""Publish due notices atomically. Default: count only; --apply commits.

    python -m app.jobs.publish_scheduled_notices --expected-database me_nu_checking --apply

Each notice is one transaction. Row locks and the delivery unique index make
overlapping runs safe. Overdue rows remain scheduled after any failed attempt
and are retried by the next timer run. Only counts are emitted.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import sys

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Notice
from app.repositories.admin_content_review_repository import append_audit
from app.seeds.menu_works import MenuWorksError, validate_target
from app.services.notice_delivery_service import lock_notice_publications, publish_notice, utc


async def dispatch_one(db, *, now: datetime) -> int | None:
    """One locked, due row inside an already-open caller transaction."""
    await lock_notice_publications(db)
    notice = await db.scalar(select(Notice).where(
        Notice.status == "scheduled", Notice.scheduled_at <= now,
    ).order_by(Notice.scheduled_at, Notice.notice_id).limit(1).with_for_update(skip_locked=True))
    if notice is None:
        return None
    before = {"status": notice.status, "scheduled_at": utc(notice.scheduled_at).isoformat(),
              "updated_at": utc(notice.updated_at).isoformat()}
    created = await publish_notice(db, notice, now)
    await append_audit(db, admin_account_id=notice.updated_by_admin_id,
        action_type="notice.dispatch", target_type="notice", target_id=notice.notice_id,
        before_value=before,
        after_value={"contract_version": 1, "execution": "scheduled_job", "status": "published",
                     "published_at": utc(notice.published_at).isoformat(), "delivery_count": created},
        rationale="관리자가 승인한 예약 공지 자동 게시", acted_at=notice.published_at)
    return created


async def run(*, expected_database: str, apply: bool = False, database_url: str | None = None,
              limit: int = 100, now: datetime | None = None) -> dict:
    if not 1 <= limit <= 1000:
        raise ValueError("The per-run limit must be between 1 and 1000")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("An offset-aware clock is required")
    if database_url is None:
        from app.core.config import settings
        database_url = settings.database_url
    validate_target(database_url, expected_database)
    engine = create_async_engine(database_url, echo=False, hide_parameters=True,
                                 connect_args={"connect_timeout": 5})
    report = {"mode": "apply" if apply else "preview", "published": 0, "deliveries": 0, "due": 0}
    try:
        async with asyncio.timeout(180):
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db, db.begin():
                await db.execute(text("SET TRANSACTION READ ONLY"))
                await db.execute(text("SET LOCAL statement_timeout = '30s'"))
                report["due"] = await db.scalar(select(func.count()).select_from(Notice).where(
                    Notice.status == "scheduled", Notice.scheduled_at <= now,
                )) or 0
            if apply:
                for _ in range(limit):
                    async with factory() as db, db.begin():
                        await db.execute(text("SET LOCAL lock_timeout = '5s'"))
                        await db.execute(text("SET LOCAL statement_timeout = '30s'"))
                        created = await dispatch_one(db, now=now)
                    if created is None:
                        break
                    report["published"] += 1
                    report["deliveries"] += created
        return report
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    try:
        task = run(expected_database=args.expected_database, apply=args.apply, limit=args.limit)
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                report = runner.run(task)
        else:
            report = asyncio.run(task)
    except (SQLAlchemyError, MenuWorksError, ValueError, TimeoutError):
        # Earlier notice transactions may have committed. Retry is safe; do not
        # claim the entire run failed atomically or emit a DB URL/body in logs.
        parser.exit(1, "공지 자동 게시를 완료하지 못했습니다. 완료된 공지는 유지되며 남은 예약은 다음 실행에서 재시도합니다.\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
