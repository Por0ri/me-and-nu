"""AI 대화: 세션 만들기, 질문 받기, 답 만들기, 답 조회.

답은 요청 경로 밖(백그라운드)에서 만든다. 질문을 받으면 빈 assistant 메시지를
먼저 만들어 그 ID를 jobId로 돌려주고, 답이 채워지면 completed가 된다.
답은 소비자 챗봇 에이전트(agent/chatbot, LangGraph)가 만든다. 근거는 현재 분야의
공개 콘텐츠와 정해 둔 공식 API 조회 결과 안에서만 쓴다.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.integrations import chatbot_facts as agent_facts
from app.integrations import chatbot_search as agent_search
from app.integrations.chatbot_agent import TurnInput, build_deps, run_turn
from app.integrations.chatbot_agent import limits as agent_limits
from app.integrations.chatbot_agent import phrases as agent_phrases
from app.integrations.chatbot_agent import prompts as agent_prompts
from app.models import (
    AgentRun,
    AgentRunStep,
    ChatMessage,
    ChatSession,
    ChatUnansweredQuestion,
    LongTermMemory,
    Tap,
    Topic,
)
from app.repositories import catalog_repository as catalog_repo
from app.repositories import content_repository as content_repo
from app.schemas.chat import (
    ChatJobError,
    ChatJobResponse,
    ChatJobResult,
    ChatMessageAccepted,
    ChatMessageCreate,
    ChatSessionResponse,
    ChatSource,
)
from app.services.feed_service import require_active_tap

logger = logging.getLogger(__name__)

FAILED = "GENERATION_FAILED"
TOPIC_SWITCH = "TOPIC_SWITCH"
INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
INSUFFICIENT_MESSAGE = agent_phrases.INSUFFICIENT
AGENT_CODE = "consumer_chat"
# DB 결과 종류 → API-053 result.type
RESULT_TYPES = {
    "answer": "answer",
    "topic_switch_suggested": "topicSwitchSuggested",
    "needs_clarification": "needs_clarification",
    "blocked": "blocked",
}


# ---------- 세션·메시지 ----------

async def create_session(
    db: AsyncSession, user_id: int, topic_id: int, anchor_content_id: int | None
) -> ChatSessionResponse:
    tap = await require_active_tap(db, user_id, topic_id)
    if anchor_content_id is not None:
        content = await content_repo.get_public_content(db, anchor_content_id, topic_id)
        if content is None:
            raise ApiError(422, "CHAT_TOPIC_MISMATCH", "대화 분야와 콘텐츠 분야가 다릅니다.")
    session = ChatSession(user_id=user_id, tap_id=tap.tap_id, anchor_content_id=anchor_content_id)
    db.add(session)
    await db.commit()
    return ChatSessionResponse(
        session_id=session.chat_session_id, topic_id=topic_id, anchor_content_id=anchor_content_id
    )


async def _owned_session(db: AsyncSession, user_id: int, session_id: int) -> ChatSession:
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.chat_session_id == session_id,
            ChatSession.user_id == user_id,
            ChatSession.status == "active",
        )
    )
    if session is None:
        raise ApiError(404, "CHAT_SESSION_NOT_FOUND", "대화를 찾을 수 없습니다.")
    return session


async def _job_for_user_message(db: AsyncSession, user_message: ChatMessage) -> ChatMessage | None:
    return await db.scalar(
        select(ChatMessage)
        .where(
            ChatMessage.chat_session_id == user_message.chat_session_id,
            ChatMessage.sender_role == "assistant",
            ChatMessage.chat_message_id > user_message.chat_message_id,
        )
        .order_by(ChatMessage.chat_message_id)
        .limit(1)
    )


async def post_message(
    db: AsyncSession, user_id: int, session_id: int, request: ChatMessageCreate
) -> tuple[ChatMessageAccepted, bool]:
    """질문을 저장하고 답 작업을 만든다. 두 번째 값이 True면 새로 답을 만들어야 한다."""
    session = await _owned_session(db, user_id, session_id)
    existing = await db.scalar(
        select(ChatMessage).where(
            ChatMessage.chat_session_id == session_id,
            ChatMessage.client_message_id == request.client_message_id,
        )
    )
    if existing is not None:
        job = await _job_for_user_message(db, existing)
        if job is not None:
            accepted = ChatMessageAccepted(
                user_message_id=existing.chat_message_id, job_id=job.chat_message_id
            )
            return accepted, False

    question = ChatMessage(
        chat_session_id=session_id,
        sender_role="user",
        message_body=request.content,
        client_message_id=request.client_message_id,
        selected_text=request.selected_text,
    )
    db.add(question)
    await db.flush()
    job = ChatMessage(chat_session_id=session_id, sender_role="assistant", message_body="")
    db.add(job)
    session.last_message_at = datetime.now(timezone.utc)
    if not session.session_title:
        session.session_title = request.content[:30]
    await db.commit()
    accepted = ChatMessageAccepted(user_message_id=question.chat_message_id, job_id=job.chat_message_id)
    return accepted, True


async def get_job(db: AsyncSession, user_id: int, job_id: int) -> ChatJobResponse:
    row = (
        await db.execute(
            select(ChatMessage, ChatSession)
            .join(ChatSession, ChatSession.chat_session_id == ChatMessage.chat_session_id)
            .where(
                ChatMessage.chat_message_id == job_id,
                ChatMessage.sender_role == "assistant",
                ChatSession.user_id == user_id,
            )
        )
    ).first()
    if row is None:
        raise ApiError(404, "CHAT_JOB_NOT_FOUND", "답변 작업을 찾을 수 없습니다.")
    message, _ = row
    notices = message.notices or []
    codes = {notice.get("code") for notice in notices}
    if FAILED in codes:
        return ChatJobResponse(
            job_id=job_id,
            status="failed",
            error=ChatJobError(code=FAILED, message="답변 생성에 실패했습니다."),
        )
    if message.result_type is None:
        return ChatJobResponse(job_id=job_id, status="processing")
    target = next((n for n in notices if n.get("code") == TOPIC_SWITCH), None)
    return ChatJobResponse(
        job_id=job_id,
        status="completed",
        result=ChatJobResult(
            type=RESULT_TYPES.get(message.result_type, "answer"),
            message_id=message.chat_message_id,
            content=message.message_body,
            sources=[ChatSource(**source) for source in message.answer_sources or []],
            notices=[n for n in notices if n.get("code") != TOPIC_SWITCH],
            context_coverage="partial" if INSUFFICIENT in codes else "full",
            target_topic_id=int(target["topicId"]) if target else None,
        ),
    )


# ---------- 답 만들기 (소비자 챗봇 에이전트, LangGraph) ----------

# 팀 테스트가 쓰는 이름을 남긴다. 실제 검색은 integrations.chatbot_search가 한다.
_tokens = agent_search.tokens
_score = agent_search.score


async def generate_answer(job_id: int) -> None:
    """백그라운드에서 답을 만든다. 요청과 다른 자체 DB 세션을 쓴다."""
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        job = await db.get(ChatMessage, job_id)
        if job is None:
            return
        try:
            await _fill_answer(db, job)
        except Exception:  # 어떤 실패든 사용자에게는 실패 상태로 알린다.
            logger.exception("chat answer failed: job=%s", job_id)
            await db.rollback()
            job = await db.get(ChatMessage, job_id)
            if job is not None:
                job.notices = [{"code": FAILED, "message": "답변 생성에 실패했습니다."}]
                await db.commit()


async def _turn_input(db: AsyncSession, session: ChatSession, job: ChatMessage, topic: Topic) -> tuple[TurnInput, ChatMessage]:
    all_topics = await catalog_repo.list_active_topics(db, None)
    others = [
        {"code": t.topic_code, "name": t.topic_name, "topicId": t.topic_id}
        for t in all_topics
        if t.topic_id != topic.topic_id
    ]
    recent = (
        await db.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.chat_session_id == session.chat_session_id,
                ChatMessage.chat_message_id < job.chat_message_id,
                func.length(ChatMessage.message_body) > 0,
            )
            .order_by(ChatMessage.chat_message_id.desc())
            .limit(agent_limits.HISTORY_TURNS * 2 + 1)
        )
    ).all()
    messages = list(reversed(recent))
    if not messages or messages[-1].sender_role != "user":
        raise RuntimeError("question not found")
    question = messages[-1]
    history = [
        {"role": m.sender_role, "text": m.message_body}
        for m in messages[:-1]
        if m.sender_role in {"user", "assistant"}
    ]
    # FR-608: 앞 대화 요약. 요약을 만드는 자리는 아직 정하지 않았다(미결). 있으면 읽기만 한다.
    memory = await db.scalar(
        select(LongTermMemory.summary)
        .where(LongTermMemory.source_chat_session_id == session.chat_session_id)
        .order_by(LongTermMemory.created_at.desc())
        .limit(1)
    )
    anchor = None
    if session.anchor_content_id:
        anchor = await agent_search.anchor_item(
            db, session.anchor_content_id, topic.topic_id, question.message_body
        )
    turn = TurnInput(
        topic_id=topic.topic_id,
        topic_code=topic.topic_code,
        topic_name=topic.topic_name,
        question=question.message_body,
        other_topics=others,
        selected_text=question.selected_text,
        anchor=anchor,
        history=history,
        memory_summary=memory,
        fact_providers=agent_facts.providers_for(topic.topic_code),
    )
    return turn, question


def _parse_time(value: str | None) -> datetime:
    return datetime.fromisoformat(value) if value else datetime.now(timezone.utc)


def _record_steps(db: AsyncSession, run: AgentRun, steps: list[dict]) -> None:
    attempts: dict[str, int] = {}
    for order, step in enumerate(steps, start=1):
        attempts[step["name"]] = attempts.get(step["name"], 0) + 1
        db.add(
            AgentRunStep(
                agent_run_id=run.agent_run_id,
                step_order=order,
                step_name=step["name"][:30],
                attempt_no=attempts[step["name"]],
                model_name=step.get("model"),
                prompt_version=step.get("promptVersion"),
                step_input={"effort": step.get("effort"), "prompt": step.get("input")},
                step_result=step.get("result"),
                decision="ok" if step.get("ok") else "error",
                started_at=_parse_time(step.get("startedAt")),
                finished_at=_parse_time(step.get("finishedAt")),
            )
        )


async def _fill_answer(db: AsyncSession, job: ChatMessage) -> None:
    session = await db.get(ChatSession, job.chat_session_id)
    tap = await db.get(Tap, session.tap_id)
    topic = await catalog_repo.get_active_topic(db, tap.topic_id)
    if topic is None:
        job.result_type = "blocked"
        job.message_body = agent_phrases.NO_TOPIC
        job.answer_sources = []
        job.notices = []
        await db.commit()
        return

    turn, question = await _turn_input(db, session, job, topic)
    run = AgentRun(
        requested_by_user_id=session.user_id,
        topic_id=topic.topic_id,
        agent_code=AGENT_CODE,
        agent_version=agent_prompts.PROMPT_VERSION,
        status="running",
        input_payload={
            "chatSessionId": session.chat_session_id,
            "questionMessageId": question.chat_message_id,
            "jobMessageId": job.chat_message_id,
        },
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.flush()
    job.agent_run_id = run.agent_run_id

    outcome = await run_turn(turn, build_deps(db, topic.topic_code))

    _record_steps(db, run, outcome.steps)
    run.node_count = min(len(outcome.steps), 32767)
    run.output_payload = {
        "resultType": outcome.result_type,
        "route": outcome.route,
        "llmCalls": outcome.llm_calls,
        "blockedBy": outcome.blocked_by,
        "unansweredReason": outcome.unanswered_reason,
    }
    run.finished_at = datetime.now(timezone.utc)
    if outcome.result_type == "failed":
        run.status = "failed"
        run.error_message = outcome.blocked_by or "generation failed"
        job.notices = [{"code": FAILED, "message": "답변 생성에 실패했습니다."}]
        await db.commit()
        return

    run.status = "succeeded"
    job.result_type = outcome.result_type
    job.message_body = outcome.message
    job.answer_sources = outcome.sources
    job.notices = outcome.notices
    job.written_at = datetime.now(timezone.utc)
    if outcome.unanswered_reason:
        db.add(
            ChatUnansweredQuestion(
                chat_message_id=question.chat_message_id,
                topic_id=topic.topic_id,
                reason=outcome.unanswered_reason,
            )
        )
    await db.commit()
