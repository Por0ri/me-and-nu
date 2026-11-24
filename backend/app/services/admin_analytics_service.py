"""Bounded SQL aggregates over existing records; no writes or synthetic metrics."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, literal, or_, select, true, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentRun, Content, ContentReport, Inquiry, OnboardingAnswersRecord,
    SearchLog, Tap, UserAccount, UserEvent,
)
from app.schemas.admin_analytics import (
    AnalyticsMeta, AnalyticsOverviewResponse, AnalyticsWindow, AttentionItem,
    AttentionResponse, Distribution, DistributionItem, FunnelStep,
    GenerationSummary, Metric, Period, Ranking, RankingItem, UsageMetrics,
)

PERIOD_DAYS = {"7d": 7, "30d": 30, "3m": 90, "6m": 180}


def analytics_window(period: Period, *, now: datetime | None = None) -> AnalyticsWindow:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Analytics clock must include a timezone")
    end = now.astimezone(timezone.utc)
    return AnalyticsWindow(period=period, start_at=end - timedelta(days=PERIOD_DAYS[period]), end_at=end)


def metadata(window: AnalyticsWindow, source: str) -> AnalyticsMeta:
    return AnalyticsMeta(generated_at=window.end_at, window=window, event_data_source=source)


def unavailable(definition: str, reason: str, *, unit="count") -> Metric:
    return Metric(availability="unavailable", reason=reason, definition=definition, unit=unit)


def _between(column, window):
    return and_(column >= window.start_at, column < window.end_at)


def _events(window, source):
    return and_(UserEvent.data_source == source, _between(UserEvent.occurred_at, window),
                UserEvent.received_at < window.end_at)


def _feed_observed():
    return and_(UserEvent.event_type == "impression", UserEvent.event_value["surface"].as_string() == "home_feed")


def _activity_observed():
    return or_(_feed_observed(), UserEvent.event_type == "open")


def _ratio(numerator, denominator, definition, *, reason=None):
    if not denominator:
        return Metric(availability="unavailable", reason="empty_denominator", unit="percent",
                      definition=definition, numerator=numerator, denominator=denominator)
    return Metric(availability="partial" if reason else "available", reason=reason,
                  value=round(100 * numerator / denominator, 4), unit="percent",
                  definition=definition, numerator=numerator, denominator=denominator)


async def usage_metrics(db: AsyncSession, window: AnalyticsWindow, source: str) -> UsageMetrics:
    active = await db.scalar(select(func.count(func.distinct(UserEvent.user_id))).where(
        _events(window, source), _activity_observed()))
    opens = await db.scalar(select(func.count()).select_from(UserEvent).where(
        _events(window, source), UserEvent.event_type == "open"))
    # Cohort = completed onboarding in this window; numerator cannot include
    # pre-onboarding impressions or users from an unrelated acquisition cohort.
    cohort = _between(UserAccount.onboarding_completed_at, window)
    joined_query = select(func.count()).select_from(UserAccount).where(cohort)
    entered_query = select(func.count(func.distinct(UserEvent.user_id))).join(
        UserAccount, UserAccount.user_id == UserEvent.user_id).where(
            _events(window, source), _feed_observed(), cohort,
            UserEvent.occurred_at >= UserAccount.onboarding_completed_at)
    # One PostgreSQL statement snapshot keeps the numerator within its cohort
    # even when onboarding/events commit while this request is running.
    joined, entered = (await db.execute(select(joined_query.scalar_subquery(), entered_query.scalar_subquery()))).one()
    return UsageMetrics(
        active_users=Metric(availability="partial", reason="observed_activity_only",
                            value=active or 0, definition="기간 내 홈 피드 노출 또는 상세 open 로그가 있는 고유 회원 수"),
        content_read_segments=Metric(availability="partial", reason="open_segments_not_pageviews",
                             value=opens or 0, definition="기간 내 상세 open 로그 수. 화면 재진입·가시성 구간별 반복을 포함하며 고유 페이지뷰가 아닙니다."),
        signup_feed_observation_rate=_ratio(entered or 0, joined or 0,
            "기간 내 온보딩 완료 회원 중 완료 이후 홈 피드 노출이 기록된 비율. 분모는 연결 DB의 회원 기록입니다.",
            reason="feed_impression_proxy_and_unscoped_account_cohort"),
    )


async def top_contents(db, window, source) -> Ranking:
    count = func.count(UserEvent.user_event_id)
    rows = (await db.execute(select(Content.content_id, Content.title, count.label("count"))
        .join(UserEvent, UserEvent.content_id == Content.content_id)
        .where(_events(window, source), UserEvent.event_type == "open")
        .group_by(Content.content_id, Content.title)
        .order_by(count.desc(), Content.content_id.asc()).limit(5))).all()
    return Ranking(availability="partial", reason="open_segments_not_pageviews",
                   definition="상세 open 로그 수 TOP 5. 동일 수치는 콘텐츠 ID 오름차순.",
                   items=[RankingItem(rank=i, content_id=r[0], label=r[1], count=r[2])
                          for i, r in enumerate(rows, 1)])


async def search_ranking(db, window, source, *, empty_only=False) -> Ranking:
    count = func.count(SearchLog.search_log_id)
    statement = select(SearchLog.search_text, count.label("count")).where(
        SearchLog.data_source == source, _between(SearchLog.searched_at, window))
    if empty_only:
        statement = statement.where(SearchLog.result_count == 0)
    rows = (await db.execute(statement.group_by(SearchLog.search_text)
        .order_by(count.desc(), SearchLog.search_text.asc()).limit(5))).all()
    return Ranking(availability="partial", reason="search_logging_best_effort",
        definition=("검색 결과 0건으로 실행된 검색어별 횟수 TOP 5" if empty_only else "실제로 실행된 검색어별 횟수 TOP 5")
                   + ". 저장된 검색 문자열을 그대로 그룹화하며 동률은 문자열 오름차순입니다.",
        items=[RankingItem(rank=i, label=r[0], count=r[1]) for i, r in enumerate(rows, 1)])


async def preferences(db, window) -> Distribution:
    # V1 q1 and V2 consumption_type share meaning. Keep answer versions separate
    # in storage; only map their display categories during this aggregate.
    choice = case((OnboardingAnswersRecord.schema_version == 2, OnboardingAnswersRecord.consumption_type),
                  else_=OnboardingAnswersRecord.q1_choice)
    category = case((choice.in_(["ost", "relationships", "goods"]), choice), else_="other")
    rows = (await db.execute(select(category.label("category"), func.count())
        .join(Tap, Tap.tap_id == OnboardingAnswersRecord.tap_id)
        .join(UserAccount, UserAccount.user_id == Tap.user_id)
        .where(_between(UserAccount.onboarding_completed_at, window),
               Tap.deleted_at.is_(None), UserAccount.account_status.in_(["active", "warned"]),
               OnboardingAnswersRecord.created_at < window.end_at)
        .group_by(category))).all()
    counts = dict(rows)
    total = sum(counts.values())
    return Distribution(availability="partial" if total else "unavailable",
        reason="current_answers_not_historical_snapshot" if total else "no_samples",
        definition="기간 내 온보딩 완료한 활성·주의 회원의 현재 유효 Tap별 선호 답변. 수정된 답변을 포함하며 회원별 비중이 아닙니다.",
        sample_size=total,
        items=[DistributionItem(key=key, label=label, count=counts.get(key, 0),
               percentage=round(counts.get(key, 0) * 100 / total, 4))
               for key, label in [("ost", "관련 OST"), ("relationships", "캐릭터 · 관계성"),
                                  ("goods", "굿즈 · 구매 정보"), ("other", "기타")]] if total else [])


async def funnel(db, window, source) -> list[FunnelStep]:
    cohort = _between(UserAccount.joined_at, window)
    registered_query = select(func.count()).select_from(UserAccount).where(cohort)
    completed_query = select(func.count()).select_from(UserAccount).where(
        cohort, UserAccount.onboarding_completed_at >= UserAccount.joined_at,
        UserAccount.onboarding_completed_at < window.end_at)
    entered_query = (select(func.count(func.distinct(UserEvent.user_id)))
        .join(UserAccount, UserAccount.user_id == UserEvent.user_id)
        .where(cohort, _events(window, source), _feed_observed(),
               UserAccount.onboarding_completed_at >= UserAccount.joined_at,
               UserEvent.occurred_at >= UserAccount.onboarding_completed_at))
    registered, completed, entered = (await db.execute(select(
        registered_query.scalar_subquery(), completed_query.scalar_subquery(), entered_query.scalar_subquery(),
    ))).one()
    return [
        FunnelStep(key="signupStarted", label="가입 시작", metric=unavailable("가입 시작 이벤트", "not_collected")),
        FunnelStep(key="accountCreated", label="계정 생성", metric=Metric(availability="available", value=registered,
                   definition="기간 내 생성된 계정 수. OAuth 계정 생성 시점이며 온보딩 완료와 다릅니다.")),
        FunnelStep(key="onboardingCompleted", label="온보딩 완료", metric=Metric(availability="available", value=completed,
                   definition="같은 계정 생성 코호트 중 조회 종료 전에 온보딩을 완료한 계정 수")),
        FunnelStep(key="feedObserved", label="피드 노출 관측", metric=Metric(availability="partial", value=entered,
                   reason="feed_impression_proxy", definition="같은 코호트 중 온보딩 완료 후 홈 피드 노출이 기록된 계정 수")),
    ]


async def returning_users(db, window, source) -> Metric:
    previous = AnalyticsWindow(period=window.period, start_at=window.start_at - (window.end_at - window.start_at), end_at=window.start_at)
    # Late-arriving previous-window events known by this response may be used.
    previous_users = select(UserEvent.user_id).where(
        UserEvent.data_source == source, _between(UserEvent.occurred_at, previous),
        UserEvent.received_at < window.end_at, _activity_observed()).distinct().subquery()
    denominator_query = select(func.count()).select_from(previous_users)
    numerator_query = select(func.count(func.distinct(UserEvent.user_id))).where(
        _events(window, source), _activity_observed(), UserEvent.user_id.in_(select(previous_users.c.user_id)))
    denominator, numerator = (await db.execute(select(
        denominator_query.scalar_subquery(), numerator_query.scalar_subquery(),
    ))).one()
    return _ratio(numerator, denominator,
        "직전 동일 길이 기간의 관측 이용 회원 중 이번 기간에도 관측된 회원 비율. D1/D7 가입 코호트 리텐션이 아닙니다.",
        reason="observed_window_return_rate")


async def generation(db, window) -> GenerationSummary:
    start = window.end_at - timedelta(hours=24)
    rows = (await db.execute(select(AgentRun.status, func.count()).where(
        AgentRun.created_at >= start, AgentRun.created_at < window.end_at).group_by(AgentRun.status))).all()
    counts = dict(rows)
    return GenerationSummary(window_start_at=start, window_end_at=window.end_at,
        succeeded=counts.get("succeeded", 0), in_progress=counts.get("queued", 0) + counts.get("running", 0),
        failed=counts.get("failed", 0), cancelled=counts.get("cancelled", 0))


def _aware(value):
    # PostgreSQL returns aware timestamps; SQLite fixtures store their UTC naive equivalent.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def attention(db, *, now, kind="all", page=1, page_size=5) -> AttentionResponse:
    reports = select(literal("report").label("kind"), ContentReport.report_id.label("record_id"),
        ContentReport.status, ContentReport.reason_code.label("category"), ContentReport.created_at,
        ContentReport.content_id).where(ContentReport.status.in_(["received", "reviewing"]), ContentReport.created_at < now)
    inquiries = select(literal("inquiry").label("kind"), Inquiry.inquiry_id.label("record_id"),
        Inquiry.status, Inquiry.inquiry_type.label("category"), Inquiry.created_at,
        literal(None).label("content_id")).where(Inquiry.status == "received", Inquiry.created_at < now)
    records = union_all(reports, inquiries).cte("attention_records")
    counts = select(
        func.count(case((records.c.kind == "report", 1))).label("reports"),
        func.count(case((records.c.kind == "inquiry", 1))).label("inquiries"),
    ).select_from(records).cte("attention_counts")
    page_rows = select(records)
    if kind != "all":
        page_rows = page_rows.where(records.c.kind == kind)
    page_rows = (page_rows.order_by(records.c.created_at.asc(), records.c.kind.asc(), records.c.record_id.asc())
                 .offset((page - 1) * page_size).limit(page_size).subquery("attention_page"))
    # Counts and rows must share one PostgreSQL statement snapshot. Separate
    # reads can disagree when a submission or resolution commits between them.
    # The outer join preserves counts when the selected page has no records.
    statement = select(counts.c.reports, counts.c.inquiries, *page_rows.c).select_from(
        counts.outerjoin(page_rows, true())
    ).order_by(page_rows.c.created_at.asc(), page_rows.c.kind.asc(), page_rows.c.record_id.asc())
    result = (await db.execute(statement)).mappings().all()
    report_count, inquiry_count = result[0]["reports"], result[0]["inquiries"]
    rows = [{key: row[key] for key in page_rows.c.keys()} for row in result if row["record_id"] is not None]
    total = report_count + inquiry_count if kind == "all" else {"report": report_count, "inquiry": inquiry_count}.get(kind, 0)
    return AttentionResponse(generated_at=now,
        availability="unavailable" if kind == "notice" else "partial" if kind == "all" else "available",
        reason="notice_workflow_not_implemented" if kind in ("all", "notice") else None,
        known_total=total, reports=report_count, inquiries=inquiry_count,
        scheduled_notices=unavailable("남은 예약 공지 수", "notice_workflow_not_implemented"),
        page=page, page_size=page_size,
        items=[AttentionItem(**{**dict(row), "created_at": _aware(row["created_at"])}) for row in rows])


async def overview(db: AsyncSession, window: AnalyticsWindow, source: str) -> AnalyticsOverviewResponse:
    # An AsyncSession must not execute concurrent statements on one connection.
    return AnalyticsOverviewResponse(
        meta=metadata(window, source), usage=await usage_metrics(db, window, source),
        funnel=await funnel(db, window, source), preference_distribution=await preferences(db, window),
        device_distribution=Distribution(availability="unavailable", reason="not_collected", definition="기기 유형별 이용 비중"),
        top_contents=await top_contents(db, window, source),
        popular_searches=await search_ranking(db, window, source),
        zero_result_searches=await search_ranking(db, window, source, empty_only=True),
        returning_user_rate=await returning_users(db, window, source),
        average_session_duration=unavailable("활동 세션 평균 지속 시간", "session_boundaries_not_collected", unit="seconds"),
    )
