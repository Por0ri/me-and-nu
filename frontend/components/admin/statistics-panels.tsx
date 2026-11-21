import Link from "next/link";
import type { ReactNode } from "react";
import type { AnalyticsMeta, Availability, Distribution, FunnelStep, Metric, Ranking } from "@/lib/admin-statistics-api";
import { adminDate } from "@/lib/admin-content";
import { UnavailableChart } from "./content-statistics";
import shared from "./admin.module.css";
import styles from "./statistics.module.css";

export function StatisticsPanel({ title, description, className = "", children, availability = "unavailable" }: {
  title: string; description?: string; className?: string; children: ReactNode; availability?: Availability;
}) {
  return <section className={`${shared.statCard} ${styles.panel} ${className}`} aria-label={title} data-statistics={availability}>
    <div className={styles.panelHeading}><h2>{title}</h2>{description && <p>{description}</p>}</div>{children}
  </section>;
}

export function PendingStatistic() { return <span className={styles.pending}>집계 준비 중</span>; }

const reasons: Record<string, string> = {
  empty_denominator: "비율을 계산할 대상이 없어요.", no_samples: "선택한 기간에 응답 표본이 없어요.",
  not_collected: "아직 수집하지 않는 지표예요.", session_boundaries_not_collected: "세션 시작·종료 기록이 없어 계산할 수 없어요.",
  notice_workflow_not_implemented: "공지·예약 일정 연결을 준비하고 있어요.",
  observed_activity_only: "활동이 기록된 회원 기준이에요.", open_segments_not_pageviews: "같은 페이지의 여러 열람 구간을 포함해요.",
  feed_impression_proxy_and_unscoped_account_cohort: "피드 노출 기록과 공유 회원 데이터를 기준으로 계산해요.",
  feed_impression_proxy: "피드에서 콘텐츠 노출이 기록된 회원 기준이에요.",
  current_answers_not_historical_snapshot: "취향 수정이 반영된 현재 답변 기준이에요.",
  search_logging_best_effort: "저장된 검색 기록 기준이며 누락이 있을 수 있어요.",
  observed_window_return_rate: "직전 같은 길이의 기간과 비교한 재방문 관측률이에요.",
  importer_does_not_record_all_live_generation_attempts: "기록된 작업만 표시하며 전체 생성 현황과 다를 수 있어요.",
};

export function StatisticNote({ availability, reason, definition }: { availability?: Availability; reason?: string | null; definition?: string }) {
  const label = availability === "available" ? "집계 기준" : availability === "partial" ? "관측 범위 안내" : "집계 준비 중";
  return <div className={styles.statisticNote}>
    <span>{reason ? reasons[reason] ?? (availability === "unavailable" ? "현재 값을 확인할 수 없어요." : "관측된 기록 범위의 수치예요.") : label}</span>
    {definition && <details><summary>집계 기준 보기</summary><p>{definition}</p></details>}
  </div>;
}

export function formatMetric(metric?: Metric) {
  if (!metric || metric.availability === "unavailable" || metric.value === null) return "—";
  const value = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: metric.unit === "percent" ? 2 : 1 }).format(metric.value);
  return `${value}${metric.unit === "percent" ? "%" : metric.unit === "seconds" ? "초" : ""}`;
}

export function MetricCard({ title, description, highlight = false, compact = false, metric }: {
  title: string; description?: string; highlight?: boolean; compact?: boolean; metric?: Metric;
}) {
  const value = formatMetric(metric);
  return <section className={`${styles.metric} ${highlight ? styles.highlight : ""} ${compact ? styles.compactMetric : ""}`} aria-label={title} data-statistics={metric?.availability ?? "unavailable"}>
    <h2>{title}</h2><strong aria-label={`${title}: ${value === "—" ? "집계 준비 중" : value}`}>{value}</strong>
    {description && <p className={styles.metricDescription}>{description}</p>}
    {metric?.denominator !== null && metric?.denominator !== undefined && <p className={styles.metricDescription}>관측 {metric.numerator?.toLocaleString("ko-KR")} / 대상 {metric.denominator.toLocaleString("ko-KR")}</p>}
    <StatisticNote availability={metric?.availability} reason={metric?.reason} definition={metric?.definition} />
  </section>;
}

export function PreferenceDistribution({ data }: { data?: Distribution }) {
  const colors = ["#9b7ae5", "#c7acfb", "#decfff", "#efe7ff"];
  let offset = 0;
  const gradient = data?.items.map((item, index) => { const start = offset; offset += item.percentage; return `${colors[index % colors.length]} ${start}% ${offset}%`; }).join(", ");
  const ready = data && data.availability !== "unavailable" && data.sampleSize !== null && data.sampleSize > 0;
  return <StatisticsPanel title="온보딩 취향 유형 분포" description="선택 기간에 온보딩을 마친 회원의 현재 답변 · 관심 분야별 표본" className={styles.preference} availability={data?.availability}>
    <div className={styles.distribution}>
      <div className={styles.ring}>{ready ? <div className={styles.donut} style={{ background: `conic-gradient(${gradient})` }} role="img" aria-label={`현재 취향 답변 ${data.sampleSize}개`}>
        <div><span>답변 표본</span><strong>{data.sampleSize?.toLocaleString("ko-KR")}</strong></div>
      </div> : <UnavailableChart label="답변 표본" />}</div>
      <dl className={styles.legend}>{(ready ? data.items : ["관련 OST", "캐릭터 · 관계성", "굿즈 · 구매 정보", "기타"].map((label, i) => ({ key: String(i), label, percentage: null, count: null }))).map((item, index) =>
        <div key={item.key}><dt><span className={styles.legendDot} style={{ background: colors[index % colors.length] }} />{item.label}</dt>
          <dd>{item.percentage === null ? "—" : `${item.percentage.toFixed(1)}%`}</dd></div>)}</dl>
    </div><StatisticNote availability={data?.availability} reason={data?.reason} definition={data?.definition} />
  </StatisticsPanel>;
}

export function DeviceDistribution({ data }: { data?: Distribution }) {
  return <StatisticsPanel title="이용 기기 비중" className={styles.device} availability={data?.availability}>
    <div className={styles.distribution}><div className={styles.ring}><UnavailableChart label="이용 기기" /></div>
      <dl className={`${styles.legend} ${styles.deviceLegend}`}>{[["mobile", "모바일"], ["desktop", "PC(웹)"], ["tablet", "태블릿"]].map(([icon, label]) =>
        <div key={icon}><dt><span className={styles.deviceIcon}><img src={`/admin/stat-${icon}.svg`} alt="" /></span>{label}</dt><dd>—</dd></div>)}</dl>
    </div><StatisticNote availability={data?.availability} reason={data?.reason} definition={data?.definition} />
  </StatisticsPanel>;
}

export function RankingPanel({ title = "열람 구간 TOP 5 콘텐츠", data }: { title?: string; data?: Ranking }) {
  return <StatisticsPanel title={title} className={styles.ranking} availability={data?.availability}>
    {data && data.availability !== "unavailable" && data.items.length === 0 ? <p className={styles.emptyStatistic}>선택한 기간에 기록이 없습니다.</p> :
      <ol aria-label={title} className={styles.rankList}>{data?.items.map(item => <li key={item.rank}><span className={styles.rank}>{item.rank}</span>
        <span className={styles.rankLabel}>{item.contentId ? <Link href={`/admin/contents/${item.contentId}`}>{item.label}</Link> : item.label}</span><span>{item.count.toLocaleString("ko-KR")}회</span></li>)}</ol>}
    <StatisticNote availability={data?.availability} reason={data?.reason} definition={data?.definition} />
  </StatisticsPanel>;
}

export function SignupFunnel({ steps }: { steps?: FunnelStep[] }) {
  const denominator = steps?.find(step => step.key === "accountCreated")?.metric.value;
  return <StatisticsPanel title="회원가입 전환 퍼널" description="같은 기간에 생성된 계정이 온보딩·피드 노출까지 도달한 기록" className={styles.funnel} availability={steps ? "partial" : "unavailable"}>
    <div className={styles.liveFunnel}>{steps?.map(step => {
      const value = step.metric.value;
      return <div key={step.key} data-funnel-step={step.key}><div><span>{step.label}</span><strong>{formatMetric(step.metric)}</strong></div>
        <div className={styles.funnelTrack} aria-hidden="true">{value !== null && denominator !== null && denominator !== undefined && denominator > 0 && <span style={{ width: `${Math.min(100, 100 * value / denominator)}%` }} />}</div>
        <StatisticNote availability={step.metric.availability} reason={step.metric.reason} definition={step.metric.definition} />
      </div>;
    })}</div>
  </StatisticsPanel>;
}

export function AnalyticsScope({ meta }: { meta: AnalyticsMeta }) {
  const sources: Record<string, string> = { production: "서비스 기록", local_demo: "서비스·테스트 공유 기록", automated_test: "자동 검증 기록", imported: "가져온 기록" };
  return <div className={styles.scope} data-analytics-scope>
    <p>{adminDate(meta.window.startAt)} ~ {adminDate(meta.window.endAt)} (KST, 종료 시각 제외) · {sources[meta.eventDataSource] ?? meta.eventDataSource}</p>
    <p>여러 서비스 환경의 기록이 함께 포함될 수 있어요. 관측 범위 안내를 확인해 주세요. —는 0이 아닌 확인할 수 없는 값입니다.</p>
  </div>;
}
