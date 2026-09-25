import pytest

from app.models import (
    AgentRun,
    AgentRunSource,
    AgentRunStep,
    Draft,
    JudgmentLog,
)
from app.services.movie_agent_runner import (
    MovieAgentExecutionError,
    run_movie_agent_and_persist,
)


class FakeAsyncSession:
    def __init__(self):
        self.added: list[object] = []
        self.commit_count = 0
        self.rollback_count = 0

    def add(self, instance: object) -> None:
        self.added.append(instance)
        self._assign_id(instance)

    async def flush(self) -> None:
        for instance in self.added:
            self._assign_id(instance)

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def refresh(self, instance: object) -> None:
        self._assign_id(instance)

    def _assign_id(self, instance: object) -> None:
        id_fields = {
            AgentRun: "agent_run_id",
            AgentRunStep: "agent_run_step_id",
            AgentRunSource: "agent_run_source_id",
            Draft: "draft_id",
            JudgmentLog: "judgment_log_id",
        }
        field = id_fields.get(type(instance))
        if field and getattr(instance, field, None) is None:
            setattr(instance, field, 2000 + len(self.added))


@pytest.mark.asyncio
async def test_runner_executes_producer_and_persists_success():
    session = FakeAsyncSession()
    received_material = None

    async def fake_producer(material):
        nonlocal received_material
        received_material = material
        return {
            "상태": "올림",
            "글": {
                "title": "대낮에 나타난 두려움",
                "body": "영화 리뷰 본문",
                "sources": [
                    "https://www.themoviedb.org/movie/1255",
                ],
            },
            "주문": {"approach": "분석"},
            "확인필요": [],
            "로그": [
                {
                    "회차": 0,
                    "단계": "판정관",
                    "점수": {"topic_fit": 5},
                    "결과": "올림",
                    "이유": "",
                }
            ],
        }

    material = {"title": "괴물", "year": 2006}
    persisted = await run_movie_agent_and_persist(
        session,
        topic_id=1,
        material=material,
        producer=fake_producer,
        source_site_ids={"TMDB": 11},
    )

    assert received_material is material
    assert persisted.agent_run.status == "succeeded"
    assert persisted.agent_run.outcome == "publish_candidate"
    assert persisted.draft is not None
    assert persisted.draft.title == "대낮에 나타난 두려움"
    assert session.commit_count == 2


@pytest.mark.asyncio
async def test_runner_records_failed_status_when_producer_raises():
    session = FakeAsyncSession()

    async def failing_producer(material):
        raise RuntimeError(f"LLM 호출 실패: {material['title']}")

    with pytest.raises(MovieAgentExecutionError) as exc_info:
        await run_movie_agent_and_persist(
            session,
            topic_id=1,
            material={"title": "괴물", "year": 2006},
            producer=failing_producer,
        )

    run = next(item for item in session.added if isinstance(item, AgentRun))
    assert run.status == "failed"
    assert run.error_message == "LLM 호출 실패: 괴물"
    assert exc_info.value.agent_run_id == run.agent_run_id
    assert session.commit_count == 2
