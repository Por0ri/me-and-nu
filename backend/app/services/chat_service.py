"""AI 대화: 세션 만들기, 질문 받기, 답 만들기(제한형 RAG), 답 조회.

답은 요청 경로 밖(백그라운드)에서 만든다. 질문을 받으면 빈 assistant 메시지를
먼저 만들어 그 ID를 jobId로 돌려주고, 답이 채워지면 completed가 된다.
근거는 두 곳에서만 찾는다: 현재 분야의 공개 콘텐츠(보고 있는 글 + 질문과 겹치는 글)와
정보사전(질문 속 이름 + 보고 있는 글의 작품에 대한 사실, 출처 · 근거 주소 포함).
"""

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.integrations import dictionary, llm
from app.models import ChatMessage, ChatSession, ContentEntityLink, EntityExternalId, Tap
from app.repositories import catalog_repository as catalog_repo
from app.repositories import content_repository as content_repo
from app.repositories.content_work_repository import MENU_WORK_PROVIDER
from app.schemas.chat import (
    ChatFactSource,
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
TOPIC_SWITCH = "TOPIC_SWITCH"  # 예전 답(분야 바꾸기 제안)을 읽을 때만 쓴다
INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
INSUFFICIENT_MESSAGE = "확인된 자료가 충분하지 않아 단정하지 않았어요."
MAX_RELATED = 3
BODY_LIMIT = 3500
# answer_sources 안에서 정보사전 근거를 우리 글 근거와 구분하는 표시
DICTIONARY_SOURCE = "dictionary"
MAX_FACT_SOURCES = 5


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
    stored = message.answer_sources or []
    return ChatJobResponse(
        job_id=job_id,
        status="completed",
        result=ChatJobResult(
            type="topicSwitchSuggested" if message.result_type == "topic_switch_suggested" else "answer",
            message_id=message.chat_message_id,
            content=message.message_body,
            sources=[ChatSource(**s) for s in stored if s.get("kind") != DICTIONARY_SOURCE],
            fact_sources=[
                ChatFactSource(name=s["name"], url=s["url"]) for s in stored if s.get("kind") == DICTIONARY_SOURCE
            ],
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


def _instructions() -> str:
    return (
        "너는 애니메이션 콘텐츠 큐레이션 서비스 me;nu의 AI 도우미다.\n"
        "규칙:\n"
        "- [자료](우리 글)와 [사전](출처가 붙은 작품 · 캐릭터 · 인물 · 곡 사실) 안에서만 답한다. "
        "둘 다에 없는 사실은 지어내지 말고 \"제가 가진 자료로는 확인되지 않아요\"라고 말한다.\n"
        "- 작품 정보 · 캐릭터 · 성우 · 제작진 · 방영 일정 같은 사실은 [사전]을 먼저 쓴다. "
        "'출처마다 값이 다름'이라고 적힌 사실은 단정하지 말고 출처마다 다르다고 함께 말한다.\n"
        "- 시즌 · 극장판이 여러 개인 작품은 어느 시즌 이야기인지 밝힌다.\n"
        "- 사실인지 묻는 질문에는 자료와 맞는 부분과 확인되지 않는 부분을 나누어 말한다.\n"
        "- 애니메이션과 관계없는 질문에는 답하지 않고, 애니메이션 이야기라면 도와줄 수 있다고 짧게 말한다.\n"
        "- 이름은 [사전]에 한국어 이름이 있으면 한국어로, 없으면 적힌 원어 그대로 쓴다. 이름을 새로 지어 옮기지 않는다.\n"
        "- 한국어 존댓말, 3~5문장. 과장하거나 광고하지 않는다.\n"
        "- '자료 1', '사실 3' 같은 번호와 주소(URL)는 답변 문장에 쓰지 않는다 (근거는 화면에 따로 보인다). "
        "'이 글', '관련 글'처럼 말하고 번호는 used · usedFacts에만 적는다.\n"
        "반드시 아래 JSON 하나로만 답한다:\n"
        '{"answer": "답변", "used": [답에 쓴 자료 번호], "usedFacts": [답에 쓴 사전 사실 번호], '
        '"evidence": "sufficient" 또는 "insufficient"}'
    )


def _fact_block(facts: list[dictionary.FactLine]) -> str:
    """사전 사실을 대상별 묶음으로: 머리에 대상 이름, 아래에 [사실 n] 한 줄씩."""
    lines: list[str] = []
    subject = None
    for number, fact in enumerate(facts, start=1):
        if fact.subject != subject:
            subject = fact.subject
            lines.append(f"## {subject}")
        lines.append(f"[사실 {number}] {fact.text}")
    return "\n".join(lines)


def _numbers(values: object, limit: int) -> list[int]:
    """LLM 이 적은 번호 중 1~limit 안의 것만, 처음 나온 순서대로 (글자로 쓴 숫자도 받는다)."""
    picked: list[int] = []
    for value in values if isinstance(values, list) else []:
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value)
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= limit and value not in picked:
            picked.append(value)
    return picked


def _fact_sources(facts: list[dictionary.FactLine], used: list[int]) -> list[dict[str, str]]:
    """답에 쓴 사실의 근거 주소 (같은 주소는 한 번만, 최대 MAX_FACT_SOURCES 개)."""
    picked: list[dict[str, str]] = []
    for number in used:
        for name, url in facts[number - 1].sources:
            if all(source["url"] != url for source in picked):
                picked.append({"kind": DICTIONARY_SOURCE, "name": name, "url": url})
    return picked[:MAX_FACT_SOURCES]


def _prompt(materials: list, anchor_content_id: int | None, facts: list[dictionary.FactLine],
            history: list[ChatMessage], question: ChatMessage) -> str:
    blocks = []
    for number, content in enumerate(materials, start=1):
        text = content.body or content.summary or ""
        label = "지금 보고 있는 글" if content.content_id == anchor_content_id else "관련 글"
        blocks.append(f"[자료 {number}] ({label}) {content.title}\n{text[:BODY_LIMIT]}")
    past = "\n".join(
        f"{'사용자' if m.sender_role == 'user' else 'AI'}: {m.message_body}" for m in history[:-1]
    )
    parts = [
        "[자료]\n" + ("\n\n".join(blocks) or "(자료 없음)"),
        "[사전]\n" + (_fact_block(facts) or "(찾은 사실 없음)"),
    ]
    if past:
        parts.append(f"[앞선 대화]\n{past}")
    if question.selected_text:
        parts.append(f"[사용자가 고른 문장]\n{question.selected_text}")
    parts.append(f"[질문]\n{question.message_body}")
    return "\n\n".join(parts)


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


async def _anchor_work_ids(db: AsyncSession, content_id: int | None) -> list[int]:
    """보고 있는 글이 다루는 작품의 me;nu 작품 번호 (주 작품 먼저)."""
    if content_id is None:
        return []
    numbers = await db.scalars(
        select(EntityExternalId.external_id)
        .join(ContentEntityLink, ContentEntityLink.entity_id == EntityExternalId.entity_id)
        .where(ContentEntityLink.content_id == content_id, EntityExternalId.provider == MENU_WORK_PROVIDER)
        .order_by(ContentEntityLink.sort_order)
    )
    return [int(number) for number in numbers if number.isdigit()]


async def _lookup_facts(
    db: AsyncSession, session: ChatSession, question: ChatMessage, history: list[ChatMessage]
) -> list[dictionary.FactLine]:
    """질문(+ 고른 문장) 속 이름과 보고 있는 글의 작품으로 사전 사실을 찾는다."""
    work_ids = await _anchor_work_ids(db, session.anchor_content_id)
    text = " ".join(part for part in (question.message_body, question.selected_text) if part)
    result = await dictionary.lookup(text, work_ids)
    if not result.targets:
        # "그럼 성우는?" 처럼 이름 없이 이어 물으면 바로 앞 질문의 이름으로 찾는다
        previous = next((m for m in reversed(history[:-1]) if m.sender_role == "user"), None)
        if previous is not None:
            result = await dictionary.lookup(previous.message_body, work_ids)
    return result.lines


async def _pick_materials(
    db: AsyncSession, session: ChatSession, topic_id: int, question: ChatMessage, *, allow_recent: bool = True
) -> list:
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
    if not related and not materials and allow_recent:
        # 홈에서 막연하게 물으면 최근 글을 자료로 쓴다 (사전 사실이 있으면 관계없는 글을 섞지 않는다).
        related = [c for _, c in scored[:MAX_RELATED]]
    return materials + related


async def _fill_answer(db: AsyncSession, job: ChatMessage) -> None:
    session = await db.get(ChatSession, job.chat_session_id)
    tap = await db.get(Tap, session.tap_id)
    topic = await catalog_repo.get_active_topic(db, tap.topic_id)

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

    facts = await _lookup_facts(db, session, question, history)
    materials = await _pick_materials(db, session, topic.topic_id, question, allow_recent=not facts)
    prompt = _prompt(materials, session.anchor_content_id, facts, history, question)
    reply = await llm.ask_json(_instructions(), prompt)
    answer = str(reply.get("answer") or "").strip()
    if not answer:
        raise RuntimeError("empty answer")

    sources: list[dict] = []
    for number in _numbers(reply.get("used"), len(materials)):
        content = materials[number - 1]
        sources.append({
            "contentId": content.content_id,
            "title": content.title,
            "url": await _source_url(db, content.content_id),
        })
    fact_sources = _fact_sources(facts, _numbers(reply.get("usedFacts"), len(facts)))
    notices: list[dict[str, str]] = []
    if reply.get("evidence") == "insufficient" or not (sources or fact_sources):
        notices.append({"code": INSUFFICIENT, "message": INSUFFICIENT_MESSAGE})
    job.result_type = "answer"
    job.message_body = answer
    job.answer_sources = sources + fact_sources
    job.notices = notices
    job.written_at = datetime.now(timezone.utc)
    await db.commit()
