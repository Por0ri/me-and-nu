"""토론방 API (설계 docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 7장, 임시 번호 D-xx).

기능 스위치 DEBATE_ENABLED가 켜진 서버에서만 등록된다 (app.main.create_app).
모두 온보딩을 마친 회원만 쓸 수 있고, 바꾸는 요청에는 X-CSRF-Token이 필요하다.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf, require_onboarding
from app.db.session import get_db
from app.models import UserAccount
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.schemas.debate import (
    DebateCreateRequest, DebateListResponse, DebateSearchResponse, DebateSummary, DebateVoteResponse, VoteTicketsResponse,
)
from app.schemas.onboarding import OnboardingWorksResponse
from app.services import debate_service as service
from app.services import onboarding_catalog_service

router = APIRouter(tags=["토론방"])

Db = Annotated[AsyncSession, Depends(get_db)]
Member = Annotated[UserAccount, Depends(require_onboarding)]
Csrf = Annotated[None, Depends(require_csrf)]
DebateId = Annotated[int, Path(alias="debateId", gt=0, le=9223372036854775807)]


def _error(code: str, message: str, description: str) -> dict:
    return {"model": ErrorResponse, "description": description, "content": {"application/json": {"example": {"code": code, "message": message}}}}


NOT_FOUND = {404: _error("DEBATE_NOT_FOUND", "토론을 찾을 수 없습니다.", "없는 토론")}


@router.post(
    "/debates", status_code=status.HTTP_201_CREATED, response_model=DebateSummary, summary="토론 주제 올리기 (D-01)",
    description="제목(1~60자), 양쪽 선택지(각 1~20자, 서로 달라야 함), 간단한 설명(선택, 200자), 관련 작품(그 분야 작품 목록에서 0~3개)을 받는다. "
                "올린 순간부터 7일 동안 개설 투표를 받는다. 투표 중인 내 주제가 이미 있으면 409 DEBATE_ACTIVE_LIMIT.",
    responses={**COMMON_ERRORS, 404: _error("TOPIC_NOT_FOUND", "Topic을 찾을 수 없습니다.", "없는 분야"),
               409: _error("DEBATE_ACTIVE_LIMIT", "투표 중인 내 주제가 이미 있습니다.", "진행 중 주제 한도")},
)
async def create_debate(payload: DebateCreateRequest, db: Db, user: Member, _: Csrf) -> DebateSummary:
    return await service.create_debate(db, user.user_id, payload)


@router.get(
    "/debates", response_model=DebateListResponse, summary="토론 목록 (D-02·03·04)",
    description="status=voting 주제 투표(sort=recent 최신순 · votes 개설 임박순), status=open 지금 열린 토론방, "
                "status=ended 지난 토론방(result=closed 방 종료 · expired 자동 소각). topicId·entityId(작품)·q(제목·선택지 글자)로 거른다. "
                "voting 최신순과 ended만 cursor로 다음 쪽을 준다.",
    responses=COMMON_ERRORS,
)
async def list_debates(
    db: Db, user: Member,
    status_: Annotated[Literal["voting", "open", "ended"], Query(alias="status")] = "voting",
    sort: Annotated[Literal["recent", "votes"], Query()] = "recent",
    result: Annotated[Literal["all", "closed", "expired"], Query()] = "all",
    topic_id: Annotated[int | None, Query(alias="topicId", gt=0, le=9223372036854775807)] = None,
    entity_id: Annotated[int | None, Query(alias="entityId", gt=0, le=9223372036854775807)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> DebateListResponse:
    return await service.list_debates(
        db, user.user_id, status=status_, sort=sort, result=result, topic_id=topic_id, entity_id=entity_id,
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
    description="작성자만, 투표 중이고 표가 0개일 때만 지울 수 있다. 다른 사람의 투표권이 걸린 주제는 지우지 않는다.",
    responses={**COMMON_ERRORS, **NOT_FOUND, 409: _error("DEBATE_DELETE_NOT_ALLOWED", "표를 받았거나 투표가 끝난 주제는 지울 수 없습니다.", "지울 수 없는 상태")},
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


@router.get("/users/me/debate-tickets", response_model=VoteTicketsResponse, summary="내 투표권 (D-13)", responses=COMMON_ERRORS)
async def my_tickets(db: Db, user: Member) -> VoteTicketsResponse:
    return await service.get_tickets(db, user.user_id)


@router.get("/users/me/debates", response_model=DebateListResponse, summary="내가 올린 주제 (D-14)", responses=COMMON_ERRORS)
async def my_debates(db: Db, user: Member) -> DebateListResponse:
    return await service.list_my_debates(db, user.user_id)


@router.get("/users/me/debate-votes", response_model=DebateListResponse, summary="내가 투표한 주제 (D-15)",
            description="아직 투표 중이거나 열려 있는 주제만. 투표 중인 것을 먼저 보여준다.", responses=COMMON_ERRORS)
async def my_votes(db: Db, user: Member) -> DebateListResponse:
    return await service.list_my_votes(db, user.user_id)
