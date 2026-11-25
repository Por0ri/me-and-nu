"""토론방 API (설계 docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 7장, 임시 번호 D-xx).

기능 스위치 DEBATE_ENABLED가 켜진 서버에서만 등록된다 (app.main.create_app).
모두 온보딩을 마친 회원만 쓸 수 있고, 바꾸는 요청에는 X-CSRF-Token이 필요하다.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context, require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.schemas.debate import (
    DebateCreateRequest, DebateListResponse, DebateMessageRequest, DebateMessagesResponse, DebateMessageView,
    DebateSearchResponse, DebateSideRequest, DebateSummary, DebateVoteResponse, VoteTicketsResponse,
)
from app.schemas.onboarding import OnboardingWorksResponse
from app.services import debate_room_service as room
from app.services import debate_service as service
from app.services import onboarding_catalog_service
from app.services.debate_hub import hub

router = APIRouter(tags=["토론방"])

Db = Annotated[AsyncSession, Depends(get_db)]
Member = Annotated[UserAccount, Depends(require_onboarding)]
Csrf = Annotated[None, Depends(require_csrf)]
DebateId = Annotated[int, Path(alias="debateId", gt=0, le=9223372036854775807)]


def _error(code: str, message: str, description: str) -> dict:
    return {"model": ErrorResponse, "description": description, "content": {"application/json": {"example": {"code": code, "message": message}}}}


NOT_FOUND = {404: _error("DEBATE_NOT_FOUND", "토론을 찾을 수 없습니다.", "없는 토론")}
NOT_OPEN = {409: _error("DEBATE_NOT_OPEN", "진행 중인 토론방이 아닙니다.", "투표 중이거나 끝난 토론")}
NOT_JOINED = {403: _error("DEBATE_NOT_JOINED", "토론에 참여한 사람만 할 수 있어요. 먼저 토론에 참여해 주세요.", "토론방에 참여하지 않음")}


@router.post(
    "/debates", status_code=status.HTTP_201_CREATED, response_model=DebateSummary, summary="토론 주제 올리기 (D-01)",
    description="제목(1~60자), 양쪽 선택지(각 1~8자, 서로 달라야 함), 간단한 설명(선택, 100자), 관련 작품(그 분야 작품 목록에서 1~2개, 필수)을 받는다. "
                "올린 순간부터 7일 동안 개설 투표를 받는다. 투표 중이거나 토론 중(열린 48시간)인 내 주제가 이미 있으면 409 DEBATE_ACTIVE_LIMIT.",
    responses={**COMMON_ERRORS, 404: _error("TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.", "없는 분야"),
               409: _error("DEBATE_ACTIVE_LIMIT", "투표 중이거나 토론 중인 내 주제가 이미 있습니다.", "진행 중 주제 한도")},
)
async def create_debate(payload: DebateCreateRequest, db: Db, user: Member, _: Csrf) -> DebateSummary:
    return await service.create_debate(db, user.user_id, payload)


@router.get(
    "/debates", response_model=DebateListResponse, summary="토론 목록 (D-02·03·04)",
    description="status=voting 주제 투표(sort=recent 최신순 · votes 개설 임박순), status=open 지금 열린 토론방, "
                "status=ended 지난 토론방(48시간이 끝난 방, 끝난 뒤 9일까지. 기준 표를 못 받은 주제는 바로 삭제돼 없다). "
                "topicId·entityId(작품)·q(제목·선택지 글자)로 거른다. "
                "voting 최신순과 ended만 cursor로 다음 쪽을 준다.",
    responses=COMMON_ERRORS,
)
async def list_debates(
    db: Db, user: Member,
    status_: Annotated[Literal["voting", "open", "ended"], Query(alias="status")] = "voting",
    sort: Annotated[Literal["recent", "votes"], Query()] = "recent",
    topic_id: Annotated[int | None, Query(alias="topicId", gt=0, le=9223372036854775807)] = None,
    entity_id: Annotated[int | None, Query(alias="entityId", gt=0, le=9223372036854775807)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> DebateListResponse:
    return await service.list_debates(
        db, user.user_id, status=status_, sort=sort, topic_id=topic_id, entity_id=entity_id,
        q=q, cursor=cursor, limit=limit,
    )


@router.get(
    "/debates/search", response_model=DebateSearchResponse, summary="토론 검색 (D-16)",
    description="입력창 하나: 이름·별칭이 맞는 작품(최대 5개)과, 제목·선택지·연결 작품에 글자가 들어간 토론(최대 30개)을 함께 준다. "
                "record=true(사용자가 검색을 확정했을 때)면 검색어와 결과 수를 debate_search_log에 남긴다. 누가 검색했는지는 남기지 않는다.",
    responses={**COMMON_ERRORS, 404: _error("TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.", "없는 분야")},
)
async def search_debates(
    db: Db, user: Member,
    topic_id: Annotated[int, Query(alias="topicId", gt=0, le=9223372036854775807)],
    q: Annotated[str, Query(max_length=100)] = "",
    record: bool = False,
    picked_entity_id: Annotated[int | None, Query(alias="pickedEntityId", gt=0, le=9223372036854775807)] = None,
) -> DebateSearchResponse:
    return await service.search(db, user.user_id, topic_id=topic_id, q=q, record=record, picked_entity_id=picked_entity_id)


@router.get(
    "/debates/works", response_model=OnboardingWorksResponse, summary="주제 올리기용 작품 검색 (D-17)",
    description="그 분야의 작품 목록에서 이름·별칭으로 찾는다. 검색어가 비어 있으면 빈 목록이다.",
    responses={**COMMON_ERRORS, 404: _error("TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.", "없는 분야")},
)
async def search_works(
    db: Db, _user: Member,
    topic_id: Annotated[int, Query(alias="topicId", gt=0, le=9223372036854775807)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> OnboardingWorksResponse:
    return await onboarding_catalog_service.works(db, topic_id, q=q, cursor=cursor, limit=limit)


@router.get("/debates/{debateId}", response_model=DebateSummary, summary="토론 상세 (D-05)", responses={**COMMON_ERRORS, **NOT_FOUND})
async def get_debate(debate_id: DebateId, db: Db, user: Member) -> DebateSummary:
    return await service.get_debate(db, user.user_id, debate_id)


@router.delete(
    "/debates/{debateId}", status_code=status.HTTP_204_NO_CONTENT, summary="내 주제 삭제 (D-06)",
    description="작성자만, 투표 중일 때(토론방이 열리기 전)만 지울 수 있다. 남의 주제는 상태와 상관없이 404 DEBATE_NOT_FOUND(있는지도 알려 주지 않음). "
                "받은 표도 함께 지우고, 투표한 사람에게 투표권을 1개씩 돌려준다(최대치를 넘지 않음). "
                "열렸거나 끝난 주제는 409 DEBATE_DELETE_NOT_ALLOWED, 지우는 사이 표가 계속 바뀌면 409 DEBATE_BUSY(잠시 후 다시 시도).",
    responses={**COMMON_ERRORS, **NOT_FOUND, 409: _error("DEBATE_DELETE_NOT_ALLOWED", "토론방으로 열렸거나 끝난 주제는 지울 수 없습니다.", "지울 수 없는 상태 · 표가 계속 바뀜(DEBATE_BUSY)")},
)
async def delete_debate(debate_id: DebateId, db: Db, user: Member, _: Csrf) -> None:
    await service.delete_debate(db, user.user_id, debate_id)


@router.put(
    "/debates/{debateId}/vote", response_model=DebateVoteResponse, summary="개설 투표 (D-07)",
    description="투표권 1개를 쓴다. 이미 투표했으면 투표권을 쓰지 않고 그대로 200. 이 표로 기준 표 수를 채우면 바로 토론방이 열리고(opened=true) "
                "투표한 사람과 작성자에게 debate_opened 알림이 간다.",
    responses={**COMMON_ERRORS, **NOT_FOUND, 409: _error("DEBATE_VOTE_TICKETS_EMPTY", "투표권이 없습니다. 18분 뒤에 1개 충전됩니다.", "투표권 없음 또는 투표가 끝난 주제(DEBATE_NOT_VOTING)")},
)
async def vote(debate_id: DebateId, db: Db, user: Member, _: Csrf) -> DebateVoteResponse:
    return await service.vote(db, user.user_id, debate_id)


@router.delete(
    "/debates/{debateId}/vote", response_model=DebateVoteResponse, summary="개설 투표 취소 (D-08)",
    description="투표 중일 때만 취소할 수 있고 투표권 1개를 바로 돌려준다(최대치를 넘지 않음). 화면이 다시 조회하지 않도록 바뀐 토론과 투표권을 200으로 돌려준다. "
                "투표하지 않은 주제도 그대로 200.",
    responses={**COMMON_ERRORS, **NOT_FOUND, 409: _error("DEBATE_NOT_VOTING", "이미 열렸거나 끝난 주제라 취소할 수 없습니다.", "취소할 수 없는 상태")},
)
async def cancel_vote(debate_id: DebateId, db: Db, user: Member, _: Csrf) -> DebateVoteResponse:
    return await service.cancel_vote(db, user.user_id, debate_id)


@router.put(
    "/debates/{debateId}/side", response_model=DebateSummary, summary="진영 고르기·바꾸기 (D-09)",
    description="열린 토론방에서 A나 B 진영을 고른다. 채팅과는 별개라 언제든 바꿀 수 있고, 같은 진영을 다시 골라도 그대로 200. "
                "바뀌면 방에 연결된 사람들에게 sides 이벤트로 새 비율을 보낸다.",
    responses={**COMMON_ERRORS, **NOT_FOUND, **NOT_OPEN, **NOT_JOINED},
)
async def choose_side(debate_id: DebateId, payload: DebateSideRequest, db: Db, user: Member, _: Csrf) -> DebateSummary:
    return await room.choose_side(db, user.user_id, debate_id, payload.side)


@router.delete(
    "/debates/{debateId}/side", response_model=DebateSummary, summary="진영 취소 (D-20)",
    description="고른 진영을 취소한다(채팅방의 '투표 취소'). 고른 진영이 없어도 그대로 200. "
                "취소되면 방에 연결된 사람들에게 sides 이벤트로 새 비율을 보낸다.",
    responses={**COMMON_ERRORS, **NOT_FOUND, **NOT_OPEN, **NOT_JOINED},
)
async def cancel_side(debate_id: DebateId, db: Db, user: Member, _: Csrf) -> DebateSummary:
    return await room.cancel_side(db, user.user_id, debate_id)


@router.get(
    "/debates/{debateId}/messages", response_model=DebateMessagesResponse, summary="채팅 불러오기 (D-10)",
    description="참여한 뒤의 최근 채팅을 오래된 순으로 준다(참여하기 전 채팅은 보이지 않는다). 더 이전 채팅은 nextCursor로 이어서 받는다. "
                "끝난 방의 채팅은 남기지 않으므로 409 DEBATE_NOT_OPEN.",
    responses={**COMMON_ERRORS, **NOT_FOUND, **NOT_OPEN, **NOT_JOINED},
)
async def list_messages(
    debate_id: DebateId, db: Db, user: Member,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DebateMessagesResponse:
    return await room.list_messages(db, user.user_id, debate_id, cursor=cursor, limit=limit)


@router.post(
    "/debates/{debateId}/messages", status_code=status.HTTP_201_CREATED, response_model=DebateMessageView, summary="채팅 보내기 (D-11)",
    description="앞뒤 공백을 지운 1~300자. 진영을 고르지 않았어도 보낼 수 있다(side=null). 한 사람이 한 방에서 10초에 5개까지 보낼 수 있고, "
                "넘으면 429 DEBATE_MESSAGE_RATE_LIMITED. 저장이 끝나면 방에 연결된 사람들에게 chat 이벤트로 보낸다.",
    responses={**COMMON_ERRORS, **NOT_FOUND, **NOT_OPEN, **NOT_JOINED,
               429: _error("DEBATE_MESSAGE_RATE_LIMITED", "메시지를 너무 빨리 보내고 있어요. 3초 뒤에 다시 보내 주세요.", "보내기 속도 제한")},
)
async def send_message(debate_id: DebateId, payload: DebateMessageRequest, db: Db, user: Member, _: Csrf) -> DebateMessageView:
    return await room.send_message(db, user.user_id, debate_id, payload.body)


class DebateStreamResponse(StreamingResponse):
    """연결이 어떻게 끝나든(정상 종료·끊김·응답이 시작되기 전에 떠남) 허브 구독을 푸는 SSE 응답.

    본문(stream_events)의 finally는 본문이 한 번이라도 돌기 시작해야 실행된다. 브라우저가 응답을 받기 전에
    떠나면 본문이 시작되지도 않아서, 응답 자체가 끝날 때 한 번 더 푼다 (두 번 풀어도 괜찮다).
    """

    def __init__(self, plan: room.StreamPlan) -> None:
        # 프록시(Caddy·nginx)가 모아 두지 않고 바로 흘려보내게 한다.
        super().__init__(room.stream_events(plan), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
        self.subscriber = plan.subscriber

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            hub.unsubscribe(self.subscriber)


def _resume_point(last_event_id: str | None, after: int | None) -> int | None:
    """브라우저가 다시 붙을 때 보내는 Last-Event-ID(마지막으로 받은 채팅 번호)와 ?after= 중 큰 값부터 다시 보낸다."""
    points = [after] if after is not None else []
    if last_event_id is not None and last_event_id.strip().isdigit():
        points.append(min(int(last_event_id.strip()), 9223372036854775807))
    return max(points) if points else None


@router.get(
    "/debates/{debateId}/stream", summary="실시간 스트림 (D-12, SSE)",
    description="text/event-stream으로 이벤트를 보낸다. chat(id=메시지 번호, 새 채팅) · sides(진영 비율) · closed(방 종료) · "
                "session-expired(로그인 세션 만료) · resync(놓친 채팅이 너무 많음: 채팅을 다시 불러올 것). 20초마다 빈 신호(: ping)를 보낸다. "
                "after(처음 연결) 또는 Last-Event-ID(다시 연결)보다 뒤의 채팅을 먼저 보낸다. 한 사람이 동시에 3개까지 열 수 있고 넘으면 429 DEBATE_STREAM_LIMIT.",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "이벤트 스트림"}, **COMMON_ERRORS, **NOT_FOUND, **NOT_OPEN, **NOT_JOINED,
               429: _error("DEBATE_STREAM_LIMIT", "동시에 열 수 있는 실시간 연결 수를 넘었습니다. 다른 창을 닫고 다시 시도해 주세요.", "동시 연결 한도")},
)
async def stream(
    debate_id: DebateId, db: Db, user: Member,
    context: Annotated[AuthContext, Depends(get_auth_context)],
    after: Annotated[int | None, Query(ge=0, le=9223372036854775807)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=30)] = None,
) -> StreamingResponse:
    # prepare_stream이 끝나면 DB 연결을 돌려주고 세션의 객체도 만료된다. 필요한 값은 그 전에 꺼낸다.
    plan = await room.prepare_stream(
        db, user.user_id, debate_id,
        resume_after=_resume_point(last_event_id, after), session_expires_at=context.session.expires_at,
    )
    return DebateStreamResponse(plan)


@router.put(
    "/debates/{debateId}/membership", response_model=DebateSummary, summary="토론방 참여 (D-18)",
    description="열린 토론방에 참여한다. 참여한 사람만 채팅·진영 고르기·실시간 연결을 할 수 있고, 참여한 순간부터의 채팅만 보인다. "
                "이미 참여 중이면 그대로 200.",
    responses={**COMMON_ERRORS, **NOT_FOUND, **NOT_OPEN},
)
async def join_room(debate_id: DebateId, db: Db, user: Member, _: Csrf) -> DebateSummary:
    return await room.join_room(db, user.user_id, debate_id)


@router.delete(
    "/debates/{debateId}/membership", response_model=DebateSummary, summary="토론방 나가기 (D-19)",
    description="토론방을 나간다. 참여 기록과 고른 진영이 지워지고(진영 비율에서 빠진다), 다시 참여하면 그때부터의 채팅만 보인다. "
                "보낸 채팅은 다른 사람 화면에 남는다. 참여 중이 아니어도 그대로 200.",
    responses={**COMMON_ERRORS, **NOT_FOUND, **NOT_OPEN},
)
async def leave_room(debate_id: DebateId, db: Db, user: Member, _: Csrf) -> DebateSummary:
    return await room.leave_room(db, user.user_id, debate_id)


@router.get("/users/me/debate-tickets", response_model=VoteTicketsResponse, summary="내 투표권 (D-13)", responses=COMMON_ERRORS)
async def my_tickets(db: Db, user: Member) -> VoteTicketsResponse:
    return await service.get_tickets(db, user.user_id)


@router.get("/users/me/debates", response_model=DebateListResponse, summary="내가 올린 주제 (D-14)", responses=COMMON_ERRORS,
            description="투표 중이거나 토론 중인 내 주제만 준다. 끝난 토론방은 지난 토론방(D-04)에만 보인다.")
async def my_debates(db: Db, user: Member) -> DebateListResponse:
    return await service.list_my_debates(db, user.user_id)


@router.get("/users/me/debate-votes", response_model=DebateListResponse, summary="내가 투표한 주제 (D-15)",
            description="아직 투표 중이거나 열려 있는 주제만. 투표 중인 것을 먼저 보여준다.", responses=COMMON_ERRORS)
async def my_votes(db: Db, user: Member) -> DebateListResponse:
    return await service.list_my_votes(db, user.user_id)
