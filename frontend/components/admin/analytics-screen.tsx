"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { analyticsAudiences, analyticsHref, parseAnalyticsQuery, type AnalyticsAudience, type AnalyticsQuery } from "@/lib/admin-analytics";
import { ReportPeriodFilter } from "./report-period-filter";
import { UnavailableShare } from "./content-statistics";
import { DeviceDistribution, MetricCard, PendingStatistic, PreferenceDistribution, RankingPanel, SignupFunnel, StatisticsPanel } from "./statistics-panels";
import shared from "./admin.module.css";
import styles from "./statistics.module.css";

const models = ["v0.5", "EASE 단독", "태그 취향", "인기순"];

export function AnalyticsRoute() {
  const params = useSearchParams(), router = useRouter();
  return <AnalyticsScreen query={parseAnalyticsQuery(params)} navigate={query => router.replace(analyticsHref(query), { scroll: false })} />;
}

export function AnalyticsScreen({ query, navigate }: { query: AnalyticsQuery; navigate: (query: AnalyticsQuery) => void }) {
  if (query.view === "performance") return <RecommendationPerformance query={query} navigate={navigate} />;
  return <div className={styles.analytics} data-analytics-view="overview">
    <div className={`${shared.heading} ${styles.analyticsHeading}`}>
      <div><div className={styles.titleRow}><h1>데이터 분석</h1><ReportPeriodFilter value={query.period} onChange={period => navigate({ ...query, period })} /></div>
        <p>사용자 행동과 콘텐츠 반응을 분석해 추천 경험을 개선하세요.</p></div>
      <Link href={analyticsHref({ ...query, view: "performance" })} className={styles.viewLink}>추천 성능<img src="/admin/stat-forward.svg" alt="" /></Link>
    </div>
    <div className={styles.analyticsTop}><PreferenceDistribution /><DeviceDistribution /></div>
    <div className={styles.analyticsMiddle}><SignupFunnel /><RankingPanel /></div>
    <div className={styles.analyticsBottom}><RankingPanel title="인기 검색 키워드 TOP5" /><RankingPanel title="검색 결과 없는 검색어 TOP5" />
      <div className={styles.sessionMetrics}><MetricCard title="평균 세션 지속 시간" compact /><MetricCard title="재방문율 (리텐션)" compact /></div>
    </div>
    <p className={styles.availability} role="status">선택한 기간의 집계 준비 중 · —는 0이 아닌 확인할 수 없는 값입니다.</p>
  </div>;
}

function DisclosureIcon() {
  return <><img className={styles.collapsedIcon} src="/admin/stat-collapsed.svg" alt="" /><img className={styles.expandedIcon} src="/admin/stat-expanded.svg" alt="" /></>;
}

function RecommendationPerformance({ query, navigate }: { query: AnalyticsQuery; navigate: (query: AnalyticsQuery) => void }) {
  return <div className={styles.performance} data-analytics-view="performance">
    <Link href={analyticsHref({ ...query, view: "overview" })} className={styles.viewLink}><img src="/admin/stat-back.svg" alt="" />데이터 분석</Link>
    <div className={`${shared.heading} ${styles.performanceHeading}`}><div><h1>추천 성능</h1><p>작품 추천 · 애니메이션 · 오프라인 평가</p></div>
      <label className={styles.audience}><span className={shared.srOnly}>추천 성능 사용자 범위</span><select value={query.audience} onChange={event => navigate({ ...query, audience: event.target.value as AnalyticsAudience })}>
        {Object.entries(analyticsAudiences).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select><img src="/admin/sort-down.svg" alt="" /></label>
    </div>
    <div className={styles.performanceGrid}>
      <div className={styles.performanceMain}>
        <div className={styles.performanceMetrics}><MetricCard title="시리즈 NDCG@10" highlight /><MetricCard title="시리즈 Hit@10" /><MetricCard title="평가 표본" /></div>
        <StatisticsPanel title="모델별 NDCG@10 비교" className={styles.comparison}>
          <div className={styles.modelRows}>{models.map(label => <UnavailableShare key={label} label={label} />)}</div><PendingStatistic />
          <details open className={styles.disclosure}><summary><DisclosureIcon />상세 수치 보기</summary>
            <div className={styles.numericTable} role="region" aria-label="추천 성능 상세 수치 표, 가로 스크롤 가능" tabIndex={0}>
              <table><caption className={shared.srOnly}>모델별 평가 수치 · 집계 준비 중</caption><thead><tr><th scope="col">추천 방식</th><th scope="col">NDCG@10</th><th scope="col">HIT@10</th><th scope="col">인기순 대비</th></tr></thead>
                <tbody>{models.map(model => <tr key={model}><th scope="row">{model}</th><td>—</td><td>—</td><td>—</td></tr>)}</tbody>
              </table>
            </div>
          </details>
        </StatisticsPanel>
      </div>
      <StatisticsPanel title="평가 조건" className={styles.conditions}>
        <dl>{["평가 데이터", "입력 조건", "집계 단위", "공통 규칙", "정답 기간", "정답 기준", "후보·추천 수", "기록 출처"].map(label => <div key={label}><dt>{label}</dt><dd>—</dd></div>)}</dl><PendingStatistic />
        <details open className={styles.disclosure}><summary><DisclosureIcon />NDCG@10은 어떻게 읽나요?</summary>
          <p>정답 시리즈가 추천 상위 10개에서 앞에 나올수록 높은 점수를 줍니다.<br />1위는 1.0, 2위는 약 0.63, 10위는 약 0.29이며, 10위 밖이면 0입니다.<br />평가 수치와 조건은 집계 연결 후 확인할 수 있습니다.</p>
        </details>
      </StatisticsPanel>
    </div>
    <p className={styles.availability} role="status">{analyticsAudiences[query.audience]} 평가 집계 준비 중 · —는 0이 아닌 확인할 수 없는 값입니다.</p>
  </div>;
}
