"""Read contracts for the PR #90 dashboard. Unknown is never encoded as zero."""

from typing import Literal

from pydantic import AwareDatetime, Field

from app.schemas.common import CamelModel

Period = Literal["7d", "30d", "3m", "6m"]
Availability = Literal["available", "partial", "unavailable"]


class AnalyticsWindow(CamelModel):
    period: Period
    start_at: AwareDatetime
    end_at: AwareDatetime
    timezone: Literal["UTC"] = "UTC"


class AnalyticsMeta(CamelModel):
    generated_at: AwareDatetime
    window: AnalyticsWindow
    event_data_source: str
    scope: Literal["connected_database"] = "connected_database"
    limitations: list[str] = Field(default_factory=lambda: [
        "shared_database_not_environment_isolated",
        "event_collection_coverage_not_guaranteed",
        "account_and_operations_records_not_source_scoped",
        "deleted_accounts_and_cascaded_events_not_recoverable",
    ])


class Metric(CamelModel):
    availability: Availability
    reason: str | None = None
    value: float | int | None = None
    unit: Literal["count", "percent", "seconds"] = "count"
    definition: str
    numerator: int | None = None
    denominator: int | None = None


class UsageMetrics(CamelModel):
    active_users: Metric
    signup_feed_observation_rate: Metric
    content_read_segments: Metric


class RankingItem(CamelModel):
    rank: int = Field(ge=1)
    label: str
    count: int = Field(ge=0)
    content_id: int | None = None


class Ranking(CamelModel):
    availability: Availability
    reason: str | None = None
    definition: str
    items: list[RankingItem] = Field(default_factory=list)


class DistributionItem(CamelModel):
    key: str
    label: str
    count: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)


class Distribution(CamelModel):
    availability: Availability
    reason: str | None = None
    definition: str
    sample_size: int | None = None
    items: list[DistributionItem] = Field(default_factory=list)


class FunnelStep(CamelModel):
    key: str
    label: str
    metric: Metric


class GenerationSummary(CamelModel):
    window_start_at: AwareDatetime
    window_end_at: AwareDatetime
    availability: Availability = "partial"
    reason: str = "importer_does_not_record_all_live_generation_attempts"
    definition: str = "최근 24시간에 생성된 agent_run의 현재 상태별 기록 수. 전체 생성 시스템 성공률이 아닙니다."
    succeeded: int = 0
    in_progress: int = 0
    failed: int = 0
    cancelled: int = 0


class AttentionItem(CamelModel):
    kind: Literal["report", "inquiry"]
    record_id: int
    status: str
    category: str
    created_at: AwareDatetime
    content_id: int | None = None


class AttentionResponse(CamelModel):
    generated_at: AwareDatetime
    availability: Availability
    reason: str | None = None
    # Known total excludes notices, whose storage/workflow does not exist yet.
    known_total: int
    reports: int
    inquiries: int
    scheduled_notices: Metric
    page: int
    page_size: int
    items: list[AttentionItem]


class AnalyticsOverviewResponse(CamelModel):
    meta: AnalyticsMeta
    usage: UsageMetrics
    funnel: list[FunnelStep]
    preference_distribution: Distribution
    device_distribution: Distribution
    top_contents: Ranking
    popular_searches: Ranking
    zero_result_searches: Ranking
    returning_user_rate: Metric
    average_session_duration: Metric


class DashboardResponse(CamelModel):
    meta: AnalyticsMeta
    usage: UsageMetrics
    top_contents: Ranking
    preference_distribution: Distribution
    generation: GenerationSummary
    attention: AttentionResponse
    today_notices: Metric
    # Defined by the same contract as the performance detail endpoint.
    recommendation_performance: "AdminRecommendationPerformanceResponse"


from app.schemas.admin_recommendation_analytics import AdminRecommendationPerformanceResponse  # noqa: E402

DashboardResponse.model_rebuild()
