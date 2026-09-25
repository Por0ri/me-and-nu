from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.movie_agent_persistence import (
    MovieAgentPersistenceResult,
    complete_movie_agent_run,
    fail_movie_agent_run,
    start_movie_agent_run,
)


MovieProducer = Callable[[Any], Awaitable[Any]]


class MovieAgentExecutionError(RuntimeError):
    """에이전트 실행에 실패했으며 실패 상태가 DB에 기록되었습니다."""

    def __init__(self, agent_run_id: int, cause: BaseException) -> None:
        self.agent_run_id = agent_run_id
        self.cause = cause
        super().__init__(
            f"영화 에이전트 실행 실패: agent_run_id={agent_run_id}, "
            f"cause={cause}"
        )


async def run_movie_agent_and_persist(
    db: AsyncSession,
    *,
    topic_id: int,
    material: Any,
    producer: MovieProducer,
    source_site_ids: Mapping[str, int] | None = None,
    requested_by_user_id: int | None = None,
    queue_task_id: str | None = None,
    agent_code: str = "movie_review",
    agent_version: str = "V1.8",
) -> MovieAgentPersistenceResult:
    """영화 에이전트 1회 실행과 DB 저장을 하나의 흐름으로 묶습니다.

    producer를 인자로 받아 테스트에서는 가짜 에이전트를, 실제 실행에서는
    movie_review_agent.produce를 주입할 수 있습니다.
    """
    agent_run = await start_movie_agent_run(
        db,
        topic_id=topic_id,
        input_payload=material,
        agent_code=agent_code,
        agent_version=agent_version,
        requested_by_user_id=requested_by_user_id,
        queue_task_id=queue_task_id,
    )

    try:
        output = await producer(material)
        return await complete_movie_agent_run(
            db,
            agent_run=agent_run,
            result=output,
            source_site_ids=source_site_ids,
        )
    except Exception as exc:
        await fail_movie_agent_run(
            db,
            agent_run=agent_run,
            error=exc,
        )
        raise MovieAgentExecutionError(
            agent_run.agent_run_id,
            exc,
        ) from exc
