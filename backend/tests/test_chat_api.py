from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.main import create_app
from app.schemas.chat import ChatMessageCreate
from app.services import chat_service


def _db_returning(row):
    result = SimpleNamespace(first=lambda: row)
    return SimpleNamespace(execute=AsyncMock(return_value=result))


def _assistant(**fields):
    base = dict(
        chat_message_id=9001, message_body="", result_type=None, notices=None, answer_sources=None
    )
    return SimpleNamespace(**{**base, **fields})


def test_chat_routes_are_registered():
    paths = create_app(enable_dev_api=False).openapi()["paths"]
    assert set(paths["/api/v1/chat/sessions"]) == {"post"}
    assert set(paths["/api/v1/chat/sessions/{sessionId}/messages"]) == {"post"}
    assert set(paths["/api/v1/chat/jobs/{jobId}"]) == {"get"}


def test_blank_question_is_rejected():
    with pytest.raises(ValidationError):
        ChatMessageCreate(client_message_id="11111111-1111-4111-8111-111111111111", content="   ")


@pytest.mark.asyncio
async def test_job_is_processing_until_answer_is_written():
    job = await chat_service.get_job(_db_returning((_assistant(), object())), 1, 9001)
    assert job.status == "processing"
    assert job.result is None


@pytest.mark.asyncio
async def test_failed_job_reports_generation_failed():
    message = _assistant(notices=[{"code": chat_service.FAILED, "message": "x"}])
    job = await chat_service.get_job(_db_returning((message, object())), 1, 9001)
    assert job.status == "failed"
    assert job.error.code == "GENERATION_FAILED"


@pytest.mark.asyncio
async def test_completed_job_returns_answer_and_sources():
    message = _assistant(
        message_body="확인된 답변",
        result_type="answer",
        answer_sources=[{"contentId": 37, "title": "전장의 중심", "url": "https://example.com"}],
        notices=[],
    )
    job = await chat_service.get_job(_db_returning((message, object())), 1, 9001)
    body = job.model_dump(by_alias=True)
    assert body["status"] == "completed"
    assert body["result"]["type"] == "answer"
    assert body["result"]["sources"][0]["contentId"] == 37
    assert body["result"]["contextCoverage"] == "full"


@pytest.mark.asyncio
async def test_topic_switch_hides_internal_notice():
    message = _assistant(
        message_body="음악 분야에서 물어봐 주세요.",
        result_type="topic_switch_suggested",
        notices=[{"code": chat_service.TOPIC_SWITCH, "topicId": "3"}],
    )
    job = await chat_service.get_job(_db_returning((message, object())), 1, 9001)
    assert job.result.type == "topicSwitchSuggested"
    assert job.result.target_topic_id == 3
    assert job.result.notices == []


@pytest.mark.asyncio
async def test_other_users_job_is_not_found():
    with pytest.raises(chat_service.ApiError) as error:
        await chat_service.get_job(_db_returning(None), 1, 9001)
    assert error.value.status_code == 404


def test_related_contents_are_ranked_by_question_overlap():
    tokens = chat_service._tokens("알렉산더 전투 장면 사실이야?")
    alexander = SimpleNamespace(title="전장의 중심", summary="알렉산더의 돌격", body="히다스페스 전투 장면")
    other = SimpleNamespace(title="미나리", summary="가족", body="농장")
    assert chat_service._score(tokens, alexander) > chat_service._score(tokens, other) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "api_type"),
    [("needs_clarification", "needs_clarification"), ("blocked", "blocked")],
)
async def test_agent_result_types_are_reported(stored, api_type):
    message = _assistant(message_body="되묻는 문장", result_type=stored, notices=[])
    job = await chat_service.get_job(_db_returning((message, object())), 1, 9001)
    assert job.status == "completed"
    assert job.result.type == api_type


def test_question_over_500_chars_is_rejected():
    with pytest.raises(ValidationError):
        ChatMessageCreate(client_message_id="11111111-1111-4111-8111-111111111111", content="가" * 501)
