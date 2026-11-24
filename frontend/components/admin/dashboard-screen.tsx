"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { analyticsHref, parseAnalyticsQuery } from "@/lib/admin-analytics";
import { adminDate, type AdminReadState } from "@/lib/admin-content";
import { loadAdminAttention, loadAdminDashboard, type AttentionKind, type AttentionResponse, type DashboardResponse, type RecommendationPerformance, type ReportPeriod } from "@/lib/admin-statistics-api";
import { AdminStatus } from "./admin-status";
import { useAdminRead } from "./use-admin-read";
import { ReportPeriodFilter } from "./report-period-filter";
import { AnalyticsScope, MetricCard, PreferenceDistribution, RankingPanel, StatisticNote, StatisticsPanel, formatMetric } from "./statistics-panels";
import shared from "./admin.module.css";
import styles from "./statistics.module.css";

const attentionFilters = { all: "전체", report: "신고", inquiry: "문의", notice: "예약 공지" } as const;
type DashboardData = { dashboard: DashboardResponse; attention: AttentionResponse };

export function DashboardScreen() {
  const params = useSearchParams(), router = useRouter(), period = parseAnalyticsQuery(params).period;
  return <ConnectedDashboard key={period} period={period} changePeriod={next => router.replace(`/admin/dashboard${next === "7d" ? "" : `?period=${next}`}`, { scroll: false })} />;
}

function ConnectedDashboard({ period, changePeriod }: { period: ReportPeriod; changePeriod: (period: ReportPeriod) => void }) {
  const [filter, setFilter] = useState<AttentionKind>("all"), [page, setPage] = useState(1);
  const read = useCallback(async (signal: AbortSignal): Promise<DashboardData> => {
    // Keep access state for the whole screen together; a rejected queue request
    // must not leave a previously authorized dashboard visible.
    const [dashboard, attention] = await Promise.all([
      loadAdminDashboard(period, signal),
      filter === "all" && page === 1 ? Promise.resolve(null) : loadAdminAttention({ kind: filter, page, pageSize: 5 }, signal),
    ]);
    return { dashboard, attention: attention ?? dashboard.attention };
  }, [period, filter, page]);
  const { state, retry } = useAdminRead(`dashboard:${period}:${filter}:${page}`, read);
  const lastPage = state.status === "ready" ? Math.max(1, Math.ceil(state.data.attention.knownTotal / state.data.attention.pageSize)) : null;
  useEffect(() => {
    if (lastPage !== null && page > lastPage) setPage(lastPage);
  }, [lastPage, page]);
  return <DashboardView period={period} changePeriod={changePeriod} state={state} retry={retry} filter={filter} page={page}
    changeFilter={kind => { setFilter(kind); setPage(1); }} changePage={setPage} />;
}

function RecommendationSummary({ performance, period }: { performance: RecommendationPerformance; period: ReportPeriod }) {
  const current = performance.models.find(model => model.modelId === performance.currentModelId);
  const service = performance.source === "service_feedback";
  const title = service ? "서비스 추천 품질" : "작품 추천 품질";
  return <section className={`${styles.metric} ${styles.highlight}`} aria-label={title} data-statistics={performance.availability}>
    <h2>{title}</h2><p className={styles.metricDescription}>{service ? "이후 긍정 반응 · 콘텐츠 NDCG@10" : "오프라인 평가 · 시리즈 NDCG@10"}</p>
    {current && performance.evaluatedAt && <p className={styles.metricDescription}>{current.label} · {adminDate(performance.evaluatedAt)} (KST)</p>}
    {service && performance.availability === "available" && <p className={styles.metricDescription}>{performance.dataSource === "local_demo" ? "Checking 시험 데이터" : performance.dataSource === "production" ? "서비스 운영 데이터" : "평가 출처 미확인"}</p>}
    {performance.availability === "available" && current ? <dl className={styles.recommendationSummary}>{["existing", "new"].map(audience => {
      const score = current.scores.find(score => score.audience === audience);
      return <div key={audience}><dt>{audience === "existing" ? (service ? "기존 사용자" : "기존 입력") : (service ? "신규 사용자" : "신규 입력")}</dt><dd>{score ? score.ndcgAt10.toFixed(4) : "—"}</dd></div>;
    })}</dl> : <p className={styles.emptyStatistic}>평가 결과 준비 중</p>}
    {service && performance.pipeline && <p className={styles.metricDescription}>{performance.pipeline.dataSource === "local_demo" ? "Checking 시험 데이터 · " : ""}{performance.pipeline.state === "failed" ? "자동 평가 확인 필요" : `관찰 ${performance.pipeline.observationDays}일 · 집단별 최소 ${performance.pipeline.minSamples}명`}</p>}
    <Link href={analyticsHref({ view: "performance", period, audience: "all", ...(service ? { dataset: "service" as const } : {}) })} className={styles.summaryLink}>추천 성능 상세 보기 →</Link>
  </section>;
}

export function DashboardView({ period, changePeriod, state, retry, filter, page, changeFilter, changePage }: {
  period: ReportPeriod; changePeriod: (period: ReportPeriod) => void; state: AdminReadState<DashboardData>; retry: () => void;
  filter: AttentionKind; page: number; changeFilter: (kind: AttentionKind) => void; changePage: (page: number) => void;
}) {
  const data = state.status === "ready" ? state.data.dashboard : null;
  const attention = state.status === "ready" ? state.data.attention : null;
  const counts = attention ? { all: attention.reports + attention.inquiries, report: attention.reports, inquiry: attention.inquiries, notice: null } : null;
  const pageCount = attention ? Math.max(1, Math.ceil(attention.knownTotal / attention.pageSize)) : 1;
  const categories: Record<string, string> = { wrong_info: "잘못된 정보", spoiler: "스포일러", rights: "권리 침해", inappropriate: "부적절한 내용", other: "기타", service: "서비스 문의", bug: "오류 문의" };
  return <div className={styles.dashboard} data-admin-dashboard data-admin-state={state.status}>
    <div className={`${shared.heading} ${styles.dashboardHeading}`}><div className={styles.titleRow}><h1>대시보드</h1><ReportPeriodFilter value={period} onChange={changePeriod} /></div><p>me;nu 서비스의 핵심 지표와 운영 현황을 한눈에 확인하세요.</p></div>
    {state.status !== "ready" || !data || !attention ? <AdminStatus subject="대시보드" state={state as Exclude<AdminReadState<DashboardData>, { status: "ready" }>} retry={retry} /> : page > pageCount ? <AdminStatus subject="대시보드" state={{ status: "loading" }} /> : <>
      <AnalyticsScope meta={data.meta} />
      <div className={styles.dashboardMetrics}><MetricCard title="이용 회원" metric={data.usage.activeUsers} /><MetricCard title="온보딩 완료 후 피드 노출 관측률" metric={data.usage.signupFeedObservationRate} />
        <MetricCard title="콘텐츠 열람 구간 수" metric={data.usage.contentReadSegments} /><RecommendationSummary performance={data.serviceRecommendationPerformance ?? data.recommendationPerformance} period={period} /></div>
      <div className={styles.dashboardGrid}>
        <div className={styles.dashboardColumn}>
          <StatisticsPanel title="지금 확인이 필요한 항목" description="현재 미처리 항목 · 오래된 접수부터 표시" className={styles.attention} availability={attention.availability}>
            <div className={styles.attentionFilters} role="group" aria-label="확인 항목 분류">{Object.entries(attentionFilters).map(([kind, label]) =>
              <button key={kind} type="button" aria-pressed={filter === kind} onClick={() => changeFilter(kind as AttentionKind)}>{label}<span>{counts?.[kind as AttentionKind]?.toLocaleString("ko-KR") ?? "—"}</span></button>)}</div>
            {attention.availability === "unavailable" ? <div className={styles.pendingList}><p>예약 공지 연결을 준비하고 있어요.</p></div> : attention.items.length === 0 ? <div className={styles.pendingList}><p>미처리 {attentionFilters[filter]} 항목이 없습니다.</p></div> :
              <ol className={styles.attentionList} aria-label="확인 필요 항목 목록">{attention.items.map(item => <li key={`${item.kind}:${item.recordId}`} data-attention-item>
                <div><strong>{item.kind === "report" ? "신고" : "문의"} #{item.recordId}</strong><span>{categories[item.category] ?? "기타"} · {item.status === "reviewing" ? "검토 중" : "접수"}</span></div>
                <time dateTime={item.createdAt}>{adminDate(item.createdAt)}</time>
                {item.contentId && <Link href={`/admin/contents/${item.contentId}`}>콘텐츠 확인</Link>}
              </li>)}</ol>}
            <StatisticNote availability={attention.availability} reason={attention.reason} definition="전체 건수는 확인 가능한 신고·문의 합계이며, 예약 공지는 포함하지 않습니다." />
            <nav className={shared.pagination} aria-label="확인 항목 페이지"><button type="button" disabled={page <= 1 || filter === "notice"} aria-label="이전 페이지" onClick={() => changePage(page - 1)}>‹</button>
              <span aria-live="polite">{filter === "notice" ? "—" : `${page} / ${pageCount}`}</span><button type="button" disabled={page >= pageCount || filter === "notice"} aria-label="다음 페이지" onClick={() => changePage(page + 1)}>›</button></nav>
          </StatisticsPanel>
          <RankingPanel data={data.topContents} />
        </div>
        <div className={styles.dashboardColumn}>
          {data.serviceRecommendationPerformance && <RecommendationSummary performance={data.recommendationPerformance} period={period} />}
          <PreferenceDistribution data={data.preferenceDistribution} />
          <StatisticsPanel title="에이전트 콘텐츠 생성" description="최근 24시간 · 기록된 작업 상태" className={styles.generation} availability={data.generation.availability}>
            <p className={styles.generationCounts}>성공 <strong>{data.generation.succeeded}</strong> · 진행 중 <strong>{data.generation.inProgress}</strong> · 실패 <strong>{data.generation.failed}</strong> · 취소 <strong>{data.generation.cancelled}</strong></p>
            <StatisticNote availability={data.generation.availability} reason={data.generation.reason} definition={data.generation.definition} />
          </StatisticsPanel>
          <StatisticsPanel title="오늘의 알림 및 공지" className={styles.today} availability={data.todayNotices.availability}>
            <p className={styles.todayDescription}>남은 예약 일정 <strong>{formatMetric(attention.scheduledNotices)}</strong></p>
            <StatisticNote availability={data.todayNotices.availability} reason={data.todayNotices.reason} definition={data.todayNotices.definition} />
          </StatisticsPanel>
        </div>
      </div>
    </>}
  </div>;
}
