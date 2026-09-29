"""AI 대화: 세션 만들기, 질문 받기, 답 만들기(제한형 RAG), 답 조회.

답은 요청 경로 밖(백그라운드)에서 만든다. 질문을 받으면 빈 assistant 메시지를
먼저 만들어 그 ID를 jobId로 돌려주고, 답이 채워지면 completed가 된다.
근거는 현재 분야의 공개 콘텐츠 안에서만 찾는다(보고 있는 글 + 질문과 겹치는 글).
"""

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.integrations import llm
from app.models import ChatMessage, ChatSession, Tap, Topic
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
INSUFFICIENT_MESSAGE = "확인된 자료가 충분하지 않아 단정하지 않았어요."
MAX_RELATED = 3
BODY_LIMIT = 3500


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
            type="topicSwitchSuggested" if message.result_type == "topic_switch_suggested" else "answer",
            message_id=message.chat_message_id,
            content=message.message_body,
            sources=[ChatSource(**source) for source in message.answer_sources or []],
            notices=[n for n in notices if n.get("code") != TOPIC_SWITCH],
            context_coverage="partial" if INSUFFICIENT in codes else "full",
            target_topic_id=int(target["topicId"]) if target else None,
        ),
    )


# ---------- 답 만들기 ----------

def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[0-9A-Za-z가-힣]{2,}", text.lower()))


def _score(question_tokens: set[str], content) -> int:
    haystack = f"{content.title} {content.summary or ''} {(content.body or '')[:2000]}".lower()
    return sum(1 for token in question_tokens if token in haystack)


async def _source_url(db: AsyncSession, content_id: int) -> str:
    source = await content_repo.get_primary_source(db, content_id)
    return source[0].source_url if source else ""


def _instructions(topic: Topic, other_topics: list[Topic]) -> str:
    others = ", ".join(f"{t.topic_code}({t.topic_name})" for t in other_topics)
    return (
        f"너는 콘텐츠 큐레이션 서비스 me;nu의 '{topic.topic_name}' 분야 AI 도우미다.\n"
        "규칙:\n"
        "- [자료]에 적힌 내용 안에서만 답한다. 자료에 없는 사실은 지어내지 말고 "
        "\"제가 가진 자료로는 확인되지 않아요\"라고 말한다.\n"
        "- 사실인지 묻는 질문에는 자료와 맞는 부분과 확인되지 않는 부분을 나누어 말한다.\n"
        f"- 질문이 '{topic.topic_name}'이 아니라 다른 분야({others})에 관한 것이면 "
        "답하지 말고 그 분야로 옮기길 권한다.\n"
        "- 한국어 존댓말, 3~5문장. 과장하거나 광고하지 않는다.\n"
        "- '자료 1' 같은 번호는 답변 문장에 쓰지 않는다. '이 글', '관련 글'처럼 말하고 번호는 used에만 적는다.\n"
        "반드시 아래 JSON 하나로만 답한다:\n"
        '{"answer": "답변", "used": [답에 쓴 자료 번호], '
        '"evidence": "sufficient" 또는 "insufficient", '
        '"switchTopic": 다른 분야 질문이면 그 분야 code, 아니면 null}'
    )


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


async def _pick_materials(db: AsyncSession, session: ChatSession, topic_id: int, question: ChatMessage) -> list:
    """보고 있는 글을 먼저 두고, 질문과 많이 겹치는 같은 분야 글을 더한다."""
    materials = []
    if session.anchor_content_id:
        anchor = await content_repo.get_public_content(db, session.anchor_content_id, topic_id)
        if anchor is not None:
            materials.append(anchor)
    candidates = await content_repo.list_public_contents(
        db, topic_id, subtopic_id=None, after=None, limit=100
    )
    question_tokens = _tokens(f"{question.message_body} {question.selected_text or ''}")
    taken = {m.content_id for m in materials}
    scored = sorted(
        ((_score(question_tokens, c), c) for c in candidates if c.content_id not in taken),
        key=lambda pair: pair[0],
        reverse=True,
    )
    related = [c for score, c in scored[:MAX_RELATED] if score > 0]
    if not related and not materials:
        # 홈에서 막연하게 물으면 최근 글을 자료로 쓴다.
        related = [c for _, c in scored[:MAX_RELATED]]
    return materials + related


async def _fill_answer(db: AsyncSession, job: ChatMessage) -> None:
    session = await db.get(ChatSession, job.chat_session_id)
    tap = await db.get(Tap, session.tap_id)
    topic = await catalog_repo.get_active_topic(db, tap.topic_id)
    all_topics = await catalog_repo.list_active_topics(db, None)
    other_topics = [t for t in all_topics if t.topic_id != topic.topic_id]

    recent = (
        await db.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.chat_session_id == session.chat_session_id,
                ChatMessage.chat_message_id < job.chat_message_id,
                func.length(ChatMessage.message_body) > 0,
            )
            .order_by(ChatMessage.chat_message_id.desc())
            .limit(5)
        )
    ).all()
    history = list(reversed(recent))
    if not history or history[-1].sender_role != "user":
        raise RuntimeError("question not found")
    question = history[-1]

    materials = await _pick_materials(db, session, topic.topic_id, question)
    blocks = []
    for number, content in enumerate(materials, start=1):
        text = content.body or content.summary or ""
        label = "지금 보고 있는 글" if content.content_id == session.anchor_content_id else "관련 글"
        blocks.append(f"[자료 {number}] ({label}) {content.title}\n{text[:BODY_LIMIT]}")
    past = "\n".join(
        f"{'사용자' if m.sender_role == 'user' else 'AI'}: {m.message_body}" for m in history[:-1]
    )
    parts = ["[자료]\n" + ("\n\n".join(blocks) or "(자료 없음)")]
    if past:
        parts.append(f"[앞선 대화]\n{past}")
    if question.selected_text:
        parts.append(f"[사용자가 고른 문장]\n{question.selected_text}")
    parts.append(f"[질문]\n{question.message_body}")

    reply = await llm.ask_json(_instructions(topic, other_topics), "\n\n".join(parts))
    answer = str(reply.get("answer") or "").strip()
    if not answer:
        raise RuntimeError("empty answer")

    switch = next((t for t in other_topics if t.topic_code == reply.get("switchTopic")), None)
    notices: list[dict[str, str]] = []
    sources: list[dict] = []
    if switch is not None:
        job.result_type = "topic_switch_suggested"
        notices.append({"code": TOPIC_SWITCH, "topicId": str(switch.topic_id)})
    else:
        job.result_type = "answer"
        used = [n for n in reply.get("used") or [] if isinstance(n, int) and 1 <= n <= len(materials)]
        for number in dict.fromkeys(used):
            content = materials[number - 1]
            sources.append({
                "contentId": content.content_id,
                "title": content.title,
                "url": await _source_url(db, content.content_id),
            })
        if reply.get("evidence") == "insufficient" or not sources:
            notices.append({"code": INSUFFICIENT, "message": INSUFFICIENT_MESSAGE})
    job.message_body = answer
    job.answer_sources = sources
    job.notices = notices
    job.written_at = datetime.now(timezone.utc)
    await db.commit()
