import type { CSSProperties } from "react";
import { UnavailableChart, UnavailableShare } from "./content-statistics";
import shared from "./admin.module.css";
import styles from "./members.module.css";

// Optional presentation data is used only by the isolated UI fixture harness.
export type MemberStatisticsData = { domainShare: number; newCount: number; newShare: number; newDomainShare: number; activeCount: number; activeShare: number };
function Share({ label, value }: { label: string; value?: number }) {
  if (value === undefined) return <UnavailableShare label={label} />;
  return <div className={shared.share}><div><span>{label}</span><span>{value}%</span></div><div className={shared.emptyTrack}><div className={styles.bar} style={{ width: `${value}%` }} /></div></div>;
}
function Chart({ label, value, share }: { label: string; value?: string; share?: number }) {
  if (share === undefined) return <UnavailableChart label={label} />;
  return <div className={styles.chart} style={{ "--share": `${share}%` } as CSSProperties} aria-label={`${label} ${value}`}><div><span>{label}</span><strong>{value}</strong></div></div>;
}
export function MemberStatistics({ data }: { data?: MemberStatisticsData }) {
  return <section className={`${shared.statistics} ${styles.statistics}`} aria-label="회원 통계" data-member-statistics={data ? "ready" : "unavailable"}>
    <article className={shared.statCard}><div className={shared.statHeading}><h2>분야별 사용자 현황</h2><span>{data ? "현재는 애니메이션 사용자 뿐이에요" : "집계 준비 중"}</span></div>
      <div className={shared.statBody}><Chart label="애니메이션" value={`${data?.domainShare}%`} share={data?.domainShare} /><div className={shared.shares}><Share label="애니메이션" value={data?.domainShare} /></div></div></article>
    <article className={shared.statCard}><div className={shared.statHeading}><h2>신규 가입자 현황</h2><span>{data ? <><b>▲ {data.newCount}명</b> · 지난 7일 대비</> : "집계 준비 중"}</span></div>
      <div className={shared.statBody}><Chart label="신규 가입자" value={`${data?.newCount}명`} share={data?.newShare} /><div className={shared.shares}><Share label="신규 가입자" value={data?.newShare} /><p>분야별 신규 가입자</p><Share label="애니메이션" value={data?.newDomainShare} /></div></div></article>
    <article className={shared.statCard}><div className={shared.statHeading}><h2>활성 회원 현황</h2><span>{data ? <><b>▲ {data.activeCount}명</b> · 지난 7일 대비</> : "집계 준비 중"}</span></div>
      <div className={shared.statBody}><Chart label="활성 회원" value={`${data?.activeCount}명`} share={data?.activeShare} /><div className={shared.shares}><Share label="활성 회원" value={data?.activeShare} /></div></div></article>
  </section>;
}
