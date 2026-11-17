"""유예(30일)가 끝난 탈퇴 계정을 지웁니다. 서버에서 하루에 한 번 실행합니다.

기본은 미리보기입니다. 대상 DB 이름을 --expected-database 로 정확히 적어야 하고,
--apply 를 붙여야 실제로 지웁니다. 결과에는 개수와 회원 번호만 찍습니다.

    python -m app.jobs.purge_withdrawn_users --expected-database me_nu_checking --apply

--user-id 는 탈퇴를 신청한 테스트 계정 하나를 유예 없이 지울 때만 씁니다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.seeds.menu_works import MenuWorksError, validate_target
from app.services.withdrawal_service import purge_withdrawn_accounts


async def run(*, expected_database: str, apply: bool, user_id: int | None, database_url: str | None = None) -> dict:
    if database_url is None:
        from app.core.config import settings
        database_url = settings.database_url
    validate_target(database_url, expected_database)
    engine = create_async_engine(database_url, echo=False)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            if not apply:
                await db.execute(text("SET TRANSACTION READ ONLY"))
            return await purge_withdrawn_accounts(db, apply=apply, user_id=user_id)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expected-database", required=True, help="대상 DB 이름 (주소 말고 이름만)")
    parser.add_argument("--database-url", help="DB 주소. 없으면 설정값을 씁니다. 화면에 찍지 않습니다.")
    parser.add_argument("--user-id", type=int, help="탈퇴를 신청한 이 계정 하나만 유예 없이 지웁니다.")
    parser.add_argument("--apply", action="store_true", help="실제로 지웁니다. 없으면 미리보기입니다.")
    args = parser.parse_args()
    try:
        task = run(expected_database=args.expected_database, apply=args.apply, user_id=args.user_id,
                   database_url=args.database_url)
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                report = runner.run(task)
        else:
            report = asyncio.run(task)
    except MenuWorksError as exc:
        parser.exit(1, f"Withdrawn account purge stopped: {exc}\n")
    except (ValueError, SQLAlchemyError):
        parser.exit(1, "Withdrawn account purge failed; no changes were committed. Check configuration and migrations.\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
