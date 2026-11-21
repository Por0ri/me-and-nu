"use client";

import Link from "next/link";
import { useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { analyticsHref, parseAnalyticsQuery, type AnalyticsQuery } from "@/lib/admin-analytics";
import { loadAdminOverview, type AnalyticsOverviewResponse } from "@/lib/admin-statistics-api";
import type { AdminReadState } from "@/lib/admin-content";
import { ReportPeriodFilter } from "./report-period-filter";
import { AdminStatus } from "./admin-status";
import { useAdminRead } from "./use-admin-read";
import { RecommendationPerformance } from "./recommendation-performance";
import { AnalyticsScope, DeviceDistribution, MetricCard, PreferenceDistribution, RankingPanel, SignupFunnel } from "./statistics-panels";
import shared from "./admin.module.css";
import styles from "./statistics.module.css";

export function AnalyticsRoute() {
  const params = useSearchParams(), router = useRouter();
  return <AnalyticsScreen query={parseAnalyticsQuery(params)} navigate={query => router.replace(analyticsHref(query), { scroll: false })} />;
}

export function AnalyticsScreen({ query, navigate }: { query: AnalyticsQuery; navigate: (query: AnalyticsQuery) => void }) {
  return query.view === "performance" ? <RecommendationPerformance query={query} navigate={navigate} /> : <ConnectedOverview query={query} navigate={navigate} />;
}

function ConnectedOverview({ query, navigate }: { query: AnalyticsQuery; navigate: (query: AnalyticsQuery) => void }) {
  const read = useCallback((signal: AbortSignal) => loadAdminOverview(query.period, signal), [query.period]);
  const { state, retry } = useAdminRead(`analytics:${query.period}`, read);
  return <AnalyticsOverviewView query={query} navigate={navigate} state={state} retry={retry} />;
}

export function AnalyticsOverviewView({ query, navigate, state, retry }: {
  query: AnalyticsQuery; navigate: (query: AnalyticsQuery) => void; state: AdminReadState<AnalyticsOverviewResponse>; retry: () => void;
}) {
  return <div className={styles.analytics} data-analytics-view="overview" data-admin-state={state.status}>
    <div className={`${shared.heading} ${styles.analyticsHeading}`}>
      <div><div className={styles.titleRow}><h1>데이터 분석</h1><ReportPeriodFilter value={query.period} onChange={period => navigate({ ...query, period })} /></div>
        <p>사용자 행동과 콘텐츠 반응을 분석해 추천 경험을 개선하세요.</p></div>
      <Link href={analyticsHref({ ...query, view: "performance" })} className={styles.viewLink}>추천 성능<img src="/admin/stat-forward.svg" alt="" /></Link>
    </div>
    {state.status !== "ready" ? <AdminStatus subject="통계" state={state} retry={retry} /> : <>
      <AnalyticsScope meta={state.data.meta} />
      <div className={styles.analyticsTop}><PreferenceDistribution data={state.data.preferenceDistribution} /><DeviceDistribution data={state.data.deviceDistribution} /></div>
      <div className={styles.analyticsMiddle}><SignupFunnel steps={state.data.funnel} /><RankingPanel data={state.data.topContents} /></div>
      <div className={styles.analyticsBottom}><RankingPanel title="인기 검색 키워드 TOP5" data={state.data.popularSearches} /><RankingPanel title="검색 결과 없는 검색어 TOP5" data={state.data.zeroResultSearches} />
        <div className={styles.sessionMetrics}><MetricCard title="평균 세션 지속 시간" metric={state.data.averageSessionDuration} compact /><MetricCard title="기간 간 재방문 관측률" metric={state.data.returningUserRate} compact /></div>
      </div>
    </>}
  </div>;
}
