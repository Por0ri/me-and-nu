"""토론 정리 작업. 서버에서 10분마다 실행합니다 (설계 docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 5.3).

7일 안에 기준 표를 못 받은 주제를 지우고, 방 종료 시각이 지난 방을 종료로 저장하고 그 방의 채팅을 지웁니다.
끝난 지 보관 기간(DEBATE_RESULT_RETENTION_DAYS, 기본 9일)이 지난 방은 통째로 지웁니다.

기본은 미리보기입니다. 대상 DB 이름을 --expected-database 로 정확히 적어야 하고,
--apply 를 붙여야 실제로 저장합니다. 결과에는 개수만 찍습니다.

    python -m app.jobs.finalize_debates --expected-database me_nu_checking --apply
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
from app.services.debate_finalize_service import finalize_debates


async def run(*, expected_database: str, apply: bool, database_url: str | None = None) -> dict:
    if database_url is None:
        from app.core.config import settings
        database_url = settings.database_url
    validate_target(database_url, expected_database)
    engine = create_async_engine(database_url, echo=False)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            if not apply:
                await db.execute(text("SET TRANSACTION READ ONLY"))
            return await finalize_debates(db, apply=apply)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expected-database", required=True, help="대상 DB 이름 (주소 말고 이름만)")
    parser.add_argument("--database-url", help="DB 주소. 없으면 설정값을 씁니다. 화면에 찍지 않습니다.")
    parser.add_argument("--apply", action="store_true", help="실제로 저장합니다. 없으면 미리보기입니다.")
    args = parser.parse_args()
    try:
        task = run(expected_database=args.expected_database, apply=args.apply, database_url=args.database_url)
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                report = runner.run(task)
        else:
            report = asyncio.run(task)
    except MenuWorksError as exc:
        parser.exit(1, f"Debate finalize stopped: {exc}\n")
    except (ValueError, SQLAlchemyError):
        parser.exit(1, "Debate finalize failed; no changes were committed. Check configuration and migrations.\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
