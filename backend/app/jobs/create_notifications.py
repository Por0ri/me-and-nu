"""알림을 만드는 서버 명령입니다. 기본은 미리보기이고, --apply 를 붙여야 저장합니다.

    # 새 글 알림: 매일 아침 9시 (한국 시간) 한 번
    python -m app.jobs.create_notifications new-posts --expected-database me_nu_checking --apply

    # 운영공지: 알림을 꺼 둔 회원도 받습니다. --user-id 를 주면 그 회원에게만
    python -m app.jobs.create_notifications notice --expected-database me_nu_checking \
        --title "점검 안내" --body "11월 20일 새벽 2시부터 30분 동안 점검해요." --apply

대상 DB 이름을 --expected-database 로 정확히 적어야 합니다. 결과에는 개수만 찍습니다.
--hours 는 새 글을 찾는 기간 (기본 24시간) 으로, 로컬 시험 DB에서 지난 글로 확인할 때만 늘립니다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.seeds.menu_works import MenuWorksError, validate_target
from app.services.notification_service import (
    NEW_POST_WINDOW,
    create_new_post_notifications,
    publish_notice,
)


async def run(
    command: str, *, expected_database: str, apply: bool, database_url: str | None = None,
    hours: float | None = None, title: str | None = None, body: str | None = None,
    user_ids: list[int] | None = None, now: datetime | None = None,
) -> dict:
    if database_url is None:
        from app.core.config import settings
        database_url = settings.database_url
    validate_target(database_url, expected_database)
    engine = create_async_engine(database_url, echo=False)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            if not apply:
                await db.execute(text("SET TRANSACTION READ ONLY"))
            if command == "new-posts":
                window = NEW_POST_WINDOW if hours is None else timedelta(hours=hours)
                return await create_new_post_notifications(db, apply=apply, now=now, window=window)
            if command == "notice":
                return await publish_notice(
                    db, title=title or "", body=body or "", apply=apply, user_ids=user_ids, now=now,
                )
            raise ValueError(f"모르는 명령: {command}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, summary in (("new-posts", "새 글 알림 만들기"), ("notice", "운영공지 만들기")):
        sub = commands.add_parser(name, help=summary)
        sub.add_argument("--expected-database", required=True, help="대상 DB 이름 (주소 말고 이름만)")
        sub.add_argument("--database-url", help="DB 주소. 없으면 설정값을 씁니다. 화면에 찍지 않습니다.")
        sub.add_argument("--apply", action="store_true", help="실제로 저장합니다. 없으면 미리보기입니다.")
        if name == "new-posts":
            sub.add_argument("--hours", type=float, help="새 글을 찾는 기간 (기본 24시간)")
        else:
            sub.add_argument("--title", required=True, help="공지 제목 (1~100자)")
            sub.add_argument("--body", required=True, help="공지 본문 (1~500자)")
            sub.add_argument("--user-id", type=int, action="append", dest="user_ids",
                             help="이 회원에게만 보냅니다. 여러 번 쓸 수 있습니다.")
    args = parser.parse_args()
    if getattr(args, "hours", None) is not None and not 0 < args.hours <= 24 * 90:
        parser.error("--hours 는 0보다 크고 2160 (90일) 이하여야 합니다.")
    try:
        task = run(
            args.command, expected_database=args.expected_database, apply=args.apply,
            database_url=args.database_url, hours=getattr(args, "hours", None),
            title=getattr(args, "title", None), body=getattr(args, "body", None),
            user_ids=getattr(args, "user_ids", None),
        )
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                report = runner.run(task)
        else:
            report = asyncio.run(task)
    except MenuWorksError as exc:
        parser.exit(1, f"알림 만들기 중단: {exc}\n")
    except ValueError as exc:
        parser.exit(1, f"알림 만들기 중단: {exc}\n")
    except SQLAlchemyError:
        parser.exit(1, "알림 만들기 실패: 저장된 것은 없습니다. 설정과 DB 번호를 확인해 주세요.\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
