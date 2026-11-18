from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
        selected_text="나" * 500,
    )
    assert message.content == question
    assert message.selected_text == "나" * 500


def test_selected_text_rejects_501_characters():
    with pytest.raises(ValidationError) as error:
        ChatMessageCreate(
            client_message_id="11111111-1111-4111-8111-111111111111",
            content="이 문장 무슨 뜻이야?",
            selected_text="나" * 501,
        )
    assert error.value.errors()[0]["loc"] == ("selected_text",)
    assert error.value.errors()[0]["type"] == "string_too_long"


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
    assert {"type": "string", "maxLength": 500} in schema["properties"]["selectedText"]["anyOf"]


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


def _answer_setup(monkeypatch, *, facts, reply, lookup_failed=False, found=None):
    """_fill_answer 를 DB · 사전 · 글 찾기 · LLM 없이 돌리는 준비. (db, job, pick, ask) 를 돌려준다."""
    question = SimpleNamespace(chat_message_id=11, sender_role="user", message_body="귀멸의 칼날 감독 누구야?",
                               selected_text=None)
    session = SimpleNamespace(chat_session_id=5, user_id=3, tap_id=7, anchor_content_id=None)
    tables = {chat_service.ChatSession: session, chat_service.Tap: SimpleNamespace(topic_id=1)}
    db = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, _id: tables[model]),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [question])),
        scalar=AsyncMock(return_value=None),
        add=Mock(),
        commit=AsyncMock(),
    )
    job = SimpleNamespace(chat_session_id=5, chat_message_id=12)
    pick = AsyncMock(return_value=[])
    ask = AsyncMock(return_value=reply)
    monkeypatch.setattr(chat_service.catalog_repo, "get_active_topic", AsyncMock(return_value=SimpleNamespace(topic_id=1)))
    monkeypatch.setattr(chat_service, "_lookup_facts", AsyncMock(return_value=(facts, lookup_failed)))
    monkeypatch.setattr(chat_service, "_pick_materials", pick)
    monkeypatch.setattr(chat_service.chat_finder, "find", AsyncMock(return_value=found))
    monkeypatch.setattr(chat_service.llm, "ask_json", ask)
    return db, job, pick, ask


@pytest.mark.asyncio
async def test_answer_uses_dictionary_facts_and_skips_recent_posts(monkeypatch):
    facts = [_fact("귀멸의 칼날 · 鬼滅の刃 (TV, 2019)", "감독: 소토자키 하루오 [AniList API]",
                   ("AniList API", "https://anilist.co/anime/101922"))]
    reply = {"answer": "소토자키 하루오 감독이에요.", "used": [], "usedFacts": [1], "evidence": "sufficient"}
    db, job, pick, ask = _answer_setup(monkeypatch, facts=facts, reply=reply)

    await chat_service._fill_answer(db, job)

    assert pick.await_args.kwargs["allow_recent"] is False
    assert "[사실 1] 감독: 소토자키 하루오" in ask.await_args.args[1]
    assert job.result_type == "answer"
    assert job.message_body == "소토자키 하루오 감독이에요."
    assert job.answer_sources == [{"kind": "dictionary", "name": "AniList API", "url": "https://anilist.co/anime/101922"}]
    assert job.notices == []
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_failed,reason", [(False, "no_evidence"), (True, "fact_lookup_failed")])
async def test_question_without_evidence_is_recorded(monkeypatch, lookup_failed, reason):
    reply = {"answer": "내가 가진 자료로는 확인이 안 돼.", "used": [], "usedFacts": [], "evidence": "insufficient"}
    db, job, _, _ = _answer_setup(monkeypatch, facts=[], reply=reply, lookup_failed=lookup_failed)

    await chat_service._fill_answer(db, job)

    assert job.notices[0]["code"] == chat_service.INSUFFICIENT
    record = db.add.call_args.args[0]
    assert (record.chat_message_id, record.topic_id, record.reason) == (11, 1, reason)


def test_day_start_follows_korean_date():
    utc = timezone.utc
    # 한국 11/12 01:30 과 11/12 23:59 는 둘 다 한국 11/12 0시(= UTC 11/11 15:00)부터 센다
    assert chat_service._day_start(datetime(2026, 11, 11, 16, 30, tzinfo=utc)) == datetime(2026, 11, 11, 15, tzinfo=utc)
    assert chat_service._day_start(datetime(2026, 11, 12, 14, 59, tzinfo=utc)) == datetime(2026, 11, 11, 15, tzinfo=utc)


@pytest.mark.asyncio
async def test_new_question_over_daily_limit_is_rejected(monkeypatch):
    monkeypatch.setattr(chat_service.settings, "chat_daily_question_limit", 2)
    session = SimpleNamespace(chat_session_id=5)
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[session, None, 2]),  # 대화 · 같은 질문 없음 · 오늘 2번
                         execute=AsyncMock())
    request = ChatMessageCreate(client_message_id="11111111-1111-4111-8111-111111111111", content="또 질문")
    with pytest.raises(chat_service.ApiError) as error:
        await chat_service.post_message(db, 1, 5, request)
    assert (error.value.status_code, error.value.code) == (429, "CHAT_DAILY_LIMIT")


@pytest.mark.asyncio
async def test_daily_count_locks_the_user_first(monkeypatch):
    """한꺼번에 보낸 질문이 모두 '0번'으로 세지지 않게, 세기 전에 그 사용자 줄을 잠근다."""
    from sqlalchemy.dialects import postgresql

    monkeypatch.setattr(chat_service.settings, "chat_daily_question_limit", 30)
    monkeypatch.setattr(chat_service.settings, "chat_daily_total_limit", 0)
    order = []
    db = SimpleNamespace(execute=AsyncMock(side_effect=lambda stmt: order.append(("lock", stmt))),
                         scalar=AsyncMock(side_effect=lambda stmt: order.append(("count", stmt)) or 0))
    await chat_service._check_daily_limit(db, 7)
    assert [step for step, _ in order] == ["lock", "count"]
    sql = str(order[0][1].compile(dialect=postgresql.dialect()))
    assert "FROM user_account" in sql and sql.rstrip().endswith("FOR UPDATE")


@pytest.mark.asyncio
async def test_zero_daily_limit_means_no_limit(monkeypatch):
    monkeypatch.setattr(chat_service.settings, "chat_daily_question_limit", 0)
    monkeypatch.setattr(chat_service.settings, "chat_daily_total_limit", 0)
    db = SimpleNamespace(scalar=AsyncMock(), execute=AsyncMock())
    await chat_service._check_daily_limit(db, 1)
    db.scalar.assert_not_awaited()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_total_limit_rejects_when_everyone_used_it_up(monkeypatch):
    """한 사람은 한도가 남아도, 모든 사용자를 합친 수가 상한이면 다른 코드로 429."""
    from sqlalchemy.dialects import postgresql

    monkeypatch.setattr(chat_service.settings, "chat_daily_question_limit", 30)
    monkeypatch.setattr(chat_service.settings, "chat_daily_total_limit", 3)
    counts = []
    db = SimpleNamespace(execute=AsyncMock(),
                         scalar=AsyncMock(side_effect=lambda stmt: counts.append(stmt) or (1 if len(counts) == 1 else 3)))
    with pytest.raises(chat_service.ApiError) as error:
        await chat_service._check_daily_limit(db, 7)
    assert (error.value.status_code, error.value.code) == (429, "CHAT_SERVICE_DAILY_LIMIT")
    mine, everyone = (str(stmt.compile(dialect=postgresql.dialect())) for stmt in counts)
    assert "chat_session.user_id" in mine and "user_id" not in everyone


@pytest.mark.asyncio
async def test_service_total_limit_works_without_per_user_limit(monkeypatch):
    monkeypatch.setattr(chat_service.settings, "chat_daily_question_limit", 0)
    monkeypatch.setattr(chat_service.settings, "chat_daily_total_limit", 5000)
    db = SimpleNamespace(execute=AsyncMock(), scalar=AsyncMock(return_value=4999))
    await chat_service._check_daily_limit(db, 7)  # 4,999번째까지는 받는다
    db.execute.assert_not_awaited()  # 한 사람 한도가 꺼져 있으면 사용자 잠금도 하지 않는다
    db.scalar.return_value = 5000
    with pytest.raises(chat_service.ApiError) as error:
        await chat_service._check_daily_limit(db, 7)
    assert error.value.code == "CHAT_SERVICE_DAILY_LIMIT"


@pytest.mark.asyncio
async def test_chat_answer_needs_no_evidence(monkeypatch):
    reply = {"kind": "chat", "answer": "탄지로는 정말 다정한 주인공이죠!", "used": [], "usedFacts": [], "evidence": None}
    db, job, _, _ = _answer_setup(monkeypatch, facts=[], reply=reply)
    await chat_service._fill_answer(db, job)
    assert job.result_type == "answer"
    assert job.notices == [{"code": chat_service.GENERAL}]
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_out_of_scope_is_blocked_without_sources(monkeypatch):
    facts = [_fact("a", "1", ("AniList API", "https://anilist.co/anime/1"))]
    reply = {"kind": "out_of_scope", "answer": "애니 이야기라면 도와드릴게요.", "used": [1], "usedFacts": [1],
             "evidence": None}
    db, job, _, _ = _answer_setup(monkeypatch, facts=facts, reply=reply)
    await chat_service._fill_answer(db, job)
    assert job.result_type == "blocked"
    assert job.answer_sources == []
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_kind_is_treated_as_fact(monkeypatch):
    reply = {"kind": "something", "answer": "답", "used": [], "usedFacts": [], "evidence": "sufficient"}
    db, job, _, _ = _answer_setup(monkeypatch, facts=[], reply=reply)
    await chat_service._fill_answer(db, job)
    assert job.notices[0]["code"] == chat_service.INSUFFICIENT


@pytest.mark.asyncio
@pytest.mark.parametrize("result_type,notices,coverage", [
    ("answer", [{"code": "GENERAL_ANSWER"}], "none"),
    ("blocked", [{"code": "GENERAL_ANSWER"}], "none"),
    ("answer", [{"code": "NO_MATCHING_CONTENT"}], "none"),
    ("answer", [{"code": "EVIDENCE_INSUFFICIENT", "message": "x"}], "partial"),
    ("answer", [], "full"),
])
async def test_job_coverage_hides_internal_notices(result_type, notices, coverage):
    message = _assistant(message_body="답", result_type=result_type, notices=notices, answer_sources=[])
    job = await chat_service.get_job(_db_returning((message, object())), 1, 9001)
    assert job.result.type == "answer"
    assert job.result.context_coverage == coverage
    assert all(notice.get("code") not in chat_service.INTERNAL_NOTICES for notice in job.result.notices)


# ---------- 글 찾기 (카드로 답하기) ----------

def _found(*content_ids, base_missing=False):
    contents = [SimpleNamespace(content_id=content_id, title=f"글 {content_id}", summary=f"요약 {content_id}",
                                content_type="event", published_at=None) for content_id in content_ids]
    return chat_service.chat_finder.Found(conditions=["작품: 나루토"], contents=contents, base_missing=base_missing)


@pytest.mark.asyncio
async def test_find_answer_keeps_picked_cards_in_order(monkeypatch):
    reply = {"kind": "find", "answer": "나루토면 이 두 개부터 봐.", "picks": [2, "1", 2, 9], "used": [], "usedFacts": []}
    db, job, pick, ask = _answer_setup(monkeypatch, facts=[], reply=reply, found=_found(301, 302))

    await chat_service._fill_answer(db, job)

    prompt = ask.await_args.args[1]
    assert "[찾은 글]\n찾은 조건: 작품: 나루토" in prompt and "[글 2] 글 302 (행사)" in prompt
    assert pick.await_args.kwargs["allow_recent"] is False  # 찾은 글이 있으면 최근 글을 따로 넣지 않는다
    assert job.answer_sources == [{"kind": "card", "contentId": 302}, {"kind": "card", "contentId": 301}]
    assert job.notices == []
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_find_without_matching_post_is_recorded(monkeypatch):
    reply = {"kind": "find", "answer": "나루토 굿즈 글은 아직 없어.", "picks": [], "used": [], "usedFacts": []}
    db, job, _, _ = _answer_setup(monkeypatch, facts=[], reply=reply, found=_found(301))

    await chat_service._fill_answer(db, job)

    assert job.notices == [{"code": chat_service.NO_CONTENT}]
    assert db.add.call_args.args[0].reason == "no_evidence"


@pytest.mark.asyncio
async def test_find_asking_back_for_a_base_work_is_not_recorded(monkeypatch):
    reply = {"kind": "find", "answer": "뭐랑 비슷한 거?", "picks": [], "used": [], "usedFacts": []}
    db, job, _, _ = _answer_setup(monkeypatch, facts=[], reply=reply, found=_found(base_missing=True))

    await chat_service._fill_answer(db, job)

    assert job.notices == [{"code": chat_service.NO_CONTENT}]
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_find_without_candidates_is_a_general_answer(monkeypatch):
    reply = {"kind": "find", "answer": "이 글 말고 다른 건 홈에서 물어봐.", "picks": [1], "used": [], "usedFacts": []}
    db, job, _, _ = _answer_setup(monkeypatch, facts=[], reply=reply, found=None)

    await chat_service._fill_answer(db, job)

    assert job.answer_sources == []
    assert job.notices == [{"code": chat_service.GENERAL}]


@pytest.mark.asyncio
async def test_follow_up_search_gets_previous_question_and_shown_cards(monkeypatch):
    reply = {"kind": "find", "answer": "더 있어.", "picks": [1], "used": [], "usedFacts": []}
    db, job, _, _ = _answer_setup(monkeypatch, facts=[], reply=reply, found=_found(303))
    earlier = SimpleNamespace(sender_role="user", message_body="판타지 콘텐츠 찾아줘", selected_text=None)
    answer = SimpleNamespace(sender_role="assistant", message_body="이거 봐.",
                             answer_sources=[{"kind": "card", "contentId": 301}, {"kind": "dictionary", "url": "x"}])
    now = SimpleNamespace(chat_message_id=11, sender_role="user", message_body="더 보여줘", selected_text=None)
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [now, answer, earlier]))  # 최신순

    await chat_service._fill_answer(db, job)

    kwargs = chat_service.chat_finder.find.await_args.kwargs
    assert (kwargs["question"], kwargs["previous"], kwargs["shown"]) == ("더 보여줘", "판타지 콘텐츠 찾아줘", [301])
    assert (kwargs["user_id"], kwargs["tap_id"], kwargs["anchor_content_id"]) == (3, 7, None)


@pytest.mark.asyncio
async def test_completed_job_returns_cards_apart_from_sources(monkeypatch):
    message = _assistant(message_body="이거 봐.", result_type="answer", notices=[], answer_sources=[
        {"kind": "card", "contentId": 41}, {"contentId": 37, "title": "전장의 중심", "url": "https://example.com"},
    ])
    cards = AsyncMock(return_value=[])
    monkeypatch.setattr(chat_service, "_cards", cards)
    session = SimpleNamespace(tap_id=7)

    job = await chat_service.get_job(_db_returning((message, session)), 3, 9001)

    assert cards.await_args.args[1:] == (3, session, [41])
    assert [source.content_id for source in job.result.sources] == [37]


@pytest.mark.asyncio
async def test_cards_keep_pick_order_and_drop_hidden_or_disliked_posts(monkeypatch):
    posts = {41: SimpleNamespace(content_id=41), 42: SimpleNamespace(content_id=42), 44: SimpleNamespace(content_id=44)}
    monkeypatch.setattr(chat_service.recommendation_repo, "contents_by_id", AsyncMock(return_value=posts))
    monkeypatch.setattr(chat_service.recommendation_repo, "negative_content_ids", AsyncMock(return_value={42}))
    monkeypatch.setattr(chat_service, "feed_card", AsyncMock(side_effect=lambda db, content, **_: content.content_id))
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(tap_id=7, topic_id=1)))

    cards = await chat_service._cards(db, 3, SimpleNamespace(tap_id=7), [44, 43, 42, 41])

    assert cards == [44, 41]  # 43 은 지금 공개 중이 아니고, 42 는 '그만볼래요'


@pytest.mark.asyncio
async def test_llm_call_caps_output_tokens(monkeypatch):
    """"길게 써 줘" 같은 주입으로 답 하나의 비용이 커지지 않게 출력 상한을 같이 보낸다."""
    import json

    import httpx

    from app.integrations import llm

    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": '{"answer": "응"}'}]}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(llm.settings, "llm_key", "test-key")
    assert await llm.ask_json("지시", "질문") == {"answer": "응"}
    assert sent["max_output_tokens"] == llm.MAX_OUTPUT_TOKENS == 2000
