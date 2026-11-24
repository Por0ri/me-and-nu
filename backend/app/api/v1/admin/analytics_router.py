"""Read-only dashboard and analytics using the same live DB admin grants."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.admin_deps import require_admin
from app.core.config import settings
from app.core.exceptions import ApiError
from app.db.session import get_db
from app.schemas.admin_analytics import (
    AnalyticsOverviewResponse, AttentionResponse, DashboardResponse, Period,
)
from app.schemas.admin_recommendation_analytics import AdminRecommendationPerformanceResponse, Audience, EvaluationDataset
from app.schemas.common import COMMON_ERRORS, ErrorResponse
from app.services import admin_analytics_service as service
from app.services.admin_recommendation_analytics_service import get_recommendation_performance

router = APIRouter(prefix="/admin", tags=["관리자 대시보드·분석"],
    dependencies=[Depends(require_admin)], responses={
        **COMMON_ERRORS,
        403: {"model": ErrorResponse, "description": "ADMIN_REQUIRED: 관리자 조회 권한 없음"},
        503: {"model": ErrorResponse, "description": "인증·권한 검증 또는 통계 DB 조회 불가"},
    })
Database = Annotated[AsyncSession, Depends(get_db)]


def unavailable_database():
    return ApiError(503, "ADMIN_ANALYTICS_UNAVAILABLE", "통계 데이터를 조회할 수 없습니다. 잠시 후 다시 시도해 주세요.")


@router.get("/analytics/overview", response_model=AnalyticsOverviewResponse)
async def overview(db: Database, period: Annotated[Period, Query()] = "7d"):
    """Rolling UTC interval: 7/30/90/180 days, inclusive start/exclusive end."""
    try:
        return await service.overview(db, service.analytics_window(period), settings.event_data_source)
    except SQLAlchemyError:
        raise unavailable_database() from None


@router.get("/analytics/recommendation-performance", response_model=AdminRecommendationPerformanceResponse)
async def recommendation_performance(
    period: Annotated[Period, Query()] = "7d", audience: Annotated[Audience, Query()] = "all",
    dataset: Annotated[EvaluationDataset, Query()] = "offline",
):
    """Separate offline-series and prospective-content datasets; never combine their scores."""
    return await run_in_threadpool(get_recommendation_performance,
        artifact_path=(settings.admin_service_evaluation_path if dataset == "service" else settings.admin_recommendation_evaluation_path),
        period=period, audience=audience, dataset=dataset)


@router.get("/dashboard/attention", response_model=AttentionResponse)
async def attention(
    db: Database, kind: Annotated[Literal["all", "report", "inquiry", "notice"], Query()] = "all",
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 5,
):
    """Current backlog, oldest first; no user identity or inquiry/report body."""
    try:
        return await service.attention(db, now=service.analytics_window("7d").end_at,
                                       kind=kind, page=page, page_size=page_size)
    except SQLAlchemyError:
        raise unavailable_database() from None


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(db: Database, period: Annotated[Period, Query()] = "7d"):
    window = service.analytics_window(period)
    source = settings.event_data_source
    try:
        return DashboardResponse(
            meta=service.metadata(window, source), usage=await service.usage_metrics(db, window, source),
            top_contents=await service.top_contents(db, window, source),
            preference_distribution=await service.preferences(db, window),
            generation=await service.generation(db, window),
            attention=await service.attention(db, now=window.end_at),
            today_notices=service.unavailable("오늘의 공지·예약 일정", "notice_workflow_not_implemented"),
            recommendation_performance=await run_in_threadpool(get_recommendation_performance,
                artifact_path=settings.admin_recommendation_evaluation_path, period=period, audience="all", now=window.end_at),
            service_recommendation_performance=await run_in_threadpool(get_recommendation_performance,
                artifact_path=settings.admin_service_evaluation_path, period=period, audience="all", now=window.end_at, dataset="service"),
        )
    except SQLAlchemyError:
        raise unavailable_database() from None
