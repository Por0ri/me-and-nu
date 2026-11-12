from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.integrations.dictionary import FactLine
from app.main import create_app
from app.schemas.chat import ChatMessageAccepted, ChatMessageCreate
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


def test_question_allows_500_korean_characters_after_trimming():
    question = "가" * 500
    message = ChatMessageCreate(
        client_message_id="11111111-1111-4111-8111-111111111111",
        content=f" \n{question}\t ",
        selected_text="나" * 2000,
    )
    assert message.content == question
    assert message.selected_text == "나" * 2000


def test_question_rejects_501_korean_characters():
    with pytest.raises(ValidationError) as error:
        ChatMessageCreate(
            client_message_id="11111111-1111-4111-8111-111111111111",
            content="가" * 501,
        )
    assert error.value.errors()[0]["loc"] == ("content",)
    assert error.value.errors()[0]["type"] == "string_too_long"


@pytest.mark.parametrize("content", [None, 123, []])
def test_non_string_question_returns_validation_error(content):
    with pytest.raises(ValidationError):
        ChatMessageCreate(
            client_message_id="11111111-1111-4111-8111-111111111111",
            content=content,
        )


def test_openapi_documents_question_and_selection_limits():
    schema = create_app(enable_dev_api=False).openapi()["components"]["schemas"]["ChatMessageCreate"]
    assert schema["properties"]["content"]["maxLength"] == 500
    assert {"type": "string", "maxLength": 2000} in schema["properties"]["selectedText"]["anyOf"]


@pytest.mark.parametrize("question,expected_status", [
    (" \n" + "가" * 500 + "\t ", 202),
    ("가" * 501, 422),
    ("🙂" * 500, 202),
    ("🙂" * 501, 422),
    (" \n\t ", 422),
])
def test_question_http_boundary_validates_before_scheduling_answer(monkeypatch, question, expected_status):
    api = create_app(enable_dev_api=False)
    database = object()

    async def override_database():
        yield database

    api.dependency_overrides[get_db] = override_database
    api.dependency_overrides[require_onboarding] = lambda: SimpleNamespace(user_id=23)
    api.dependency_overrides[require_csrf] = lambda: None
    post_message = AsyncMock(return_value=(ChatMessageAccepted(user_message_id=10, job_id=11), False))
    generate_answer = AsyncMock()
    monkeypatch.setattr(chat_service, "post_message", post_message)
    monkeypatch.setattr(chat_service, "generate_answer", generate_answer)
    with TestClient(api) as client:
        response = client.post("/api/v1/chat/sessions/1/messages", json={
            "clientMessageId": "11111111-1111-4111-8111-111111111111",
            "content": question,
        })
    assert response.status_code == expected_status
    if expected_status == 202:
        post_message.assert_awaited_once()
        assert post_message.await_args.args[3].content == question.strip()
        assert response.json()["jobId"] == 11
    else:
        post_message.assert_not_awaited()
        assert response.json()["code"] == "VALIDATION_ERROR"
    generate_answer.assert_not_awaited()


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
async def test_completed_job_splits_dictionary_sources():
    message = _assistant(
        message_body="소토자키 하루오 감독이에요.",
        result_type="answer",
        answer_sources=[
            {"contentId": 37, "title": "전장의 중심", "url": "https://example.com"},
            {"kind": "dictionary", "name": "AniList API", "url": "https://anilist.co/anime/101922"},
        ],
        notices=[],
    )
    job = await chat_service.get_job(_db_returning((message, object())), 1, 9001)
    body = job.model_dump(by_alias=True)["result"]
    assert [s["contentId"] for s in body["sources"]] == [37]
    assert body["factSources"] == [{"name": "AniList API", "url": "https://anilist.co/anime/101922"}]


def _fact(subject, text, *sources):
    return FactLine(subject=subject, text=text, sources=tuple(sources))


def test_fact_block_numbers_lines_under_subject_headers():
    facts = [_fact("귀멸의 칼날", "감독: 소토자키 하루오"), _fact("귀멸의 칼날", "화수: 26"), _fact("탄지로", "생일: 7월 14일")]
    assert chat_service._fact_block(facts) == (
        "## 귀멸의 칼날\n[사실 1] 감독: 소토자키 하루오\n[사실 2] 화수: 26\n## 탄지로\n[사실 3] 생일: 7월 14일"
    )


def test_numbers_keep_valid_llm_numbers_once():
    assert chat_service._numbers([2, "1", 2, 9, 0, True, "x"], 3) == [2, 1]
    assert chat_service._numbers(None, 3) == []


def test_fact_sources_follow_used_facts_without_repeating_urls():
    anilist = ("AniList API", "https://anilist.co/anime/1")
    facts = [_fact("a", "1", anilist), _fact("a", "2", anilist, ("위키데이터", "https://w/Q1")), _fact("a", "3")]
    assert chat_service._fact_sources(facts, [2, 1, 3]) == [
        {"kind": "dictionary", "name": "AniList API", "url": "https://anilist.co/anime/1"},
        {"kind": "dictionary", "name": "위키데이터", "url": "https://w/Q1"},
    ]


@pytest.mark.asyncio
async def test_answer_uses_dictionary_facts_and_skips_recent_posts(monkeypatch):
    question = SimpleNamespace(sender_role="user", message_body="귀멸의 칼날 감독 누구야?", selected_text=None)
    session = SimpleNamespace(chat_session_id=5, tap_id=7, anchor_content_id=None)
    tables = {chat_service.ChatSession: session, chat_service.Tap: SimpleNamespace(topic_id=1)}
    db = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, _id: tables[model]),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [question])),
        commit=AsyncMock(),
    )
    job = SimpleNamespace(chat_session_id=5, chat_message_id=2)
    facts = [_fact("귀멸의 칼날 · 鬼滅の刃 (TV, 2019)", "감독: 소토자키 하루오 [AniList API]",
                   ("AniList API", "https://anilist.co/anime/101922"))]
    pick = AsyncMock(return_value=[])
    ask = AsyncMock(return_value={"answer": "소토자키 하루오 감독이에요.", "used": [], "usedFacts": [1],
                                  "evidence": "sufficient"})
    monkeypatch.setattr(chat_service.catalog_repo, "get_active_topic", AsyncMock(return_value=SimpleNamespace(topic_id=1)))
    monkeypatch.setattr(chat_service, "_lookup_facts", AsyncMock(return_value=facts))
    monkeypatch.setattr(chat_service, "_pick_materials", pick)
    monkeypatch.setattr(chat_service.llm, "ask_json", ask)

    await chat_service._fill_answer(db, job)

    assert pick.await_args.kwargs["allow_recent"] is False
    assert "[사실 1] 감독: 소토자키 하루오" in ask.await_args.args[1]
    assert job.result_type == "answer"
    assert job.message_body == "소토자키 하루오 감독이에요."
    assert job.answer_sources == [{"kind": "dictionary", "name": "AniList API", "url": "https://anilist.co/anime/101922"}]
    assert job.notices == []
