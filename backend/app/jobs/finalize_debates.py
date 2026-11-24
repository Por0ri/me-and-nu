"""토론 정리 작업. 서버에서 10분마다 실행합니다 (설계 docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 5.3).

7일 안에 기준 표를 못 받은 주제를 지우고, 방 종료 시각이 지난 방을 종료로 저장하고 그 방의 채팅을 지웁니다.
끝난 지 보관 기간(DEBATE_RESULT_RETENTION_DAYS, 기본 9일)이 지난 방은 통째로 지웁니다.

기본은 미리보기입니다. 대상 DB 이름을 --expected-database 로 정확히 적어야 하고,
--apply 를 붙여야 실제로 저장합니다. 결과에는 개수만 찍습니다.

공지 작업처럼 스스로 끝납니다: 접속 5초, 잠금 대기 5초, 문장 하나 60초, 전체 180초를 넘으면 전부 되돌리고
실패로 끝나며 다음 실행이 다시 합니다. main 서버 타이머(deploy/main/main_debates.py)는 240초 뒤 docker
명령만 끊고 rollout 잠금을 풀기 때문에, 컨테이너 안 작업이 그보다 먼저 끝나야 잠금 약속이 지켜집니다.

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

# 서버 타이머의 240초보다 짧게 (deploy/main/main_debates.py 의 runtime.run timeout).
JOB_TIMEOUT_SECONDS = 180


async def run(*, expected_database: str, apply: bool, database_url: str | None = None) -> dict:
    if database_url is None:
        from app.core.config import settings
        database_url = settings.database_url
    validate_target(database_url, expected_database)
    engine = create_async_engine(database_url, echo=False, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        async with asyncio.timeout(JOB_TIMEOUT_SECONDS):
            async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
                if not apply:
                    await db.execute(text("SET TRANSACTION READ ONLY"))
                # API는 토론 줄을 몇 ms만 잠그므로 5초면 충분하다. 오래 잡힌 잠금(열어 둔 psql 등) 뒤에 줄 서지 않는다.
                await db.execute(text("SET LOCAL lock_timeout = '5s'"))
                # 큰 방의 채팅 삭제도 넉넉히 끝나는 값. 프로세스가 갑자기 죽어도 DB 쪽에서 문장이 멈춘다.
                await db.execute(text("SET LOCAL statement_timeout = '60s'"))
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
    except (ValueError, SQLAlchemyError, TimeoutError):
        # 한 트랜잭션이라 실패하면(시간 초과 포함) 아무것도 저장되지 않는다. 주소·값은 찍지 않는다.
        parser.exit(1, "Debate finalize failed or timed out; no changes were committed. The next run retries.\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
