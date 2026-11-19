import type { ReactNode } from "react";
import { UnavailableChart, UnavailableShare } from "./content-statistics";
import shared from "./admin.module.css";
import styles from "./statistics.module.css";

export function StatisticsPanel({ title, description, className = "", children }: {
  title: string; description?: string; className?: string; children: ReactNode;
}) {
  return <section className={`${shared.statCard} ${styles.panel} ${className}`} aria-label={title} data-statistics="unavailable">
    <div className={styles.panelHeading}><h2>{title}</h2>{description && <p>{description}</p>}</div>
    {children}
  </section>;
}

export function PendingStatistic() {
  return <span className={styles.pending}>집계 준비 중</span>;
}

export function MetricCard({ title, description, highlight = false, compact = false }: {
  title: string; description?: string; highlight?: boolean; compact?: boolean;
}) {
  return <section className={`${styles.metric} ${highlight ? styles.highlight : ""} ${compact ? styles.compactMetric : ""}`} aria-label={title} data-statistics="unavailable">
    <h2>{title}</h2><strong aria-label={`${title}: 집계 준비 중`}>—</strong>
    <p><PendingStatistic />{description && <span className={styles.metricDescription}>{description}</span>}</p>
  </section>;
}

export function PreferenceDistribution() {
  return <StatisticsPanel title="온보딩 취향 유형 분포" description="전체 응답자의 유형 비중" className={styles.preference}>
    <div className={styles.distribution}>
      <div className={styles.ring}><UnavailableChart label="응답자" /></div>
      <dl className={styles.legend}>{["관련 OST", "캐릭터 · 관계성", "굿즈 · 구매 정보", "기타"].map((label, index) =>
        <div key={label}><dt><img src={`/admin/stat-legend-${index + 1}.svg`} alt="" />{label}</dt><dd aria-label={`${label}: 집계 준비 중`}>—</dd></div>)}</dl>
    </div><PendingStatistic />
  </StatisticsPanel>;
}

export function DeviceDistribution() {
  return <StatisticsPanel title="이용 기기 비중" className={styles.device}>
    <div className={styles.distribution}>
      <div className={styles.ring}><UnavailableChart label="이용 기기" /></div>
      <dl className={`${styles.legend} ${styles.deviceLegend}`}>{[["mobile", "모바일"], ["desktop", "PC(웹)"], ["tablet", "태블릿"]].map(([icon, label]) =>
        <div key={icon}><dt><span className={styles.deviceIcon}><img src={`/admin/stat-${icon}.svg`} alt="" /></span>{label}</dt><dd aria-label={`${label}: 집계 준비 중`}>—</dd></div>)}</dl>
    </div><PendingStatistic />
  </StatisticsPanel>;
}

export function RankingPanel({ title = "조회 수 TOP 5 콘텐츠" }: { title?: string }) {
  return <StatisticsPanel title={title} className={styles.ranking}>
    <ol aria-label={`${title}: 집계 준비 중`} className={styles.rankList}>{Array.from({ length: 5 }, (_, index) =>
      <li key={index}><span className={styles.rank}>{index + 1}</span><span className={styles.rankLabel}>—</span><span aria-hidden="true">—</span></li>)}</ol>
    <PendingStatistic />
  </StatisticsPanel>;
}

export function SignupFunnel() {
  return <StatisticsPanel title="회원가입 전환 퍼널" description="가입 후 콘텐츠 탐색까지의 흐름" className={styles.funnel}>
    <div className={styles.funnelRows}>{["가입 시작", "회원가입 완료", "취향 질문 완료", "피드 진입"].map(label => <UnavailableShare key={label} label={label} />)}</div>
    <PendingStatistic />
  </StatisticsPanel>;
}
