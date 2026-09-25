from collections.abc import Iterable

import pytest

from app.models import (
    AgentRun,
    AgentRunSource,
    AgentRunStep,
    Draft,
    JudgmentLog,
)
from app.services.movie_agent_persistence import (
    complete_movie_agent_run,
    fail_movie_agent_run,
    start_movie_agent_run,
)


class FakeAsyncSession:
    def __init__(self):
        self.added: list[object] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.refresh_count = 0
        self.flush_count = 0

    def add(self, instance: object) -> None:
        self.added.append(instance)
        self._assign_id(instance)

    async def flush(self) -> None:
        self.flush_count += 1
        for instance in self.added:
            self._assign_id(instance)

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def refresh(self, instance: object) -> None:
        self.refresh_count += 1
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
            setattr(instance, field, 1000 + len(self.added))

    def instances(self, expected_type: type) -> Iterable[object]:
        return (
            item for item in self.added if isinstance(item, expected_type)
        )


@pytest.mark.asyncio
async def test_start_movie_agent_run_saves_running_record():
    session = FakeAsyncSession()

    run = await start_movie_agent_run(
        session,
        topic_id=1,
        input_payload={"title": "괴물", "year": 2006},
    )

    assert run.status == "running"
    assert run.topic_id == 1
    assert run.input_payload["title"] == "괴물"
    assert run.agent_run_id is not None
    assert session.commit_count == 1
    assert session.rollback_count == 0


@pytest.mark.asyncio
async def test_complete_movie_agent_run_saves_draft_logs_and_sources():
    session = FakeAsyncSession()
    run = await start_movie_agent_run(
        session,
        topic_id=1,
        input_payload={
            "title": "괴물",
            "year": 2006,
            "source_links": [
                "https://www.themoviedb.org/movie/1255",
                "https://ko.wikipedia.org/wiki/괴물_(2006년_영화)",
            ],
        },
    )

    result = await complete_movie_agent_run(
        session,
        agent_run=run,
        source_site_ids={"TMDB": 11, "한국어 위키백과": 12},
        result={
            "상태": "올림",
            "글": {
                "title": "대낮에 나타난 두려움",
                "body": "영화 리뷰 본문",
                "sources": [
                    "https://www.themoviedb.org/movie/1255",
                    "https://ko.wikipedia.org/wiki/괴물_(2006년_영화)",
                ],
            },
            "주문": {
                "approach": "분석",
                "temperature": "건조",
                "layout": "판단 먼저",
            },
            "확인필요": ["사람이 확인할 문장"],
            "로그": [
                {
                    "회차": 0,
                    "단계": "판정관",
                    "점수": {"topic_fit": 5, "promo": 1},
                    "결과": "올림",
                    "이유": "",
                }
            ],
        },
    )

    assert result.agent_run.status == "succeeded"
    assert result.agent_run.outcome == "publish_candidate"
    assert result.agent_run.node_count == 1
    assert result.step_count == 1
    assert result.source_count == 2
    assert result.judgment_count == 1
    assert result.draft is not None
    assert result.draft.status == "approved"
    assert result.draft.production_type == "ai"
    assert result.draft.factcheck_result["requires_review"] is True

    sources = list(session.instances(AgentRunSource))
    assert {source.source_site_id for source in sources} == {11, 12}
    assert len(list(session.instances(Draft))) == 1
    assert len(list(session.instances(JudgmentLog))) == 1
    assert session.commit_count == 2


@pytest.mark.asyncio
async def test_non_article_outcome_does_not_create_draft():
    session = FakeAsyncSession()
    run = await start_movie_agent_run(
        session,
        topic_id=1,
        input_payload={"title": "테스트 영화"},
    )

    result = await complete_movie_agent_run(
        session,
        agent_run=run,
        result={"상태": "재료부족", "이유": "줄거리 부족", "로그": []},
    )

    assert result.agent_run.outcome == "insufficient_material"
    assert result.draft is None
    assert len(list(session.instances(Draft))) == 0


@pytest.mark.asyncio
async def test_fail_movie_agent_run_records_error():
    session = FakeAsyncSession()
    run = await start_movie_agent_run(
        session,
        topic_id=1,
        input_payload={"title": "괴물"},
    )

    failed = await fail_movie_agent_run(
        session,
        agent_run=run,
        error=RuntimeError("LLM 호출 실패"),
    )

    assert failed.status == "failed"
    assert failed.outcome is None
    assert failed.error_message == "LLM 호출 실패"
    assert failed.finished_at is not None
