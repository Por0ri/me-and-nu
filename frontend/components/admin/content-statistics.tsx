import styles from "./admin.module.css";

// Aggregates are not part of the admin read API. An empty ring/track means
// unavailable, never zero or a percentage calculated from the current page.
export function UnavailableChart({ label }: { label: string }) {
  return <div className={styles.emptyChart} aria-label={`${label}: 집계 준비 중`}>
    <div><span>{label}</span><strong>—</strong></div>
  </div>;
}

export function UnavailableShare({ label }: { label: string }) {
  return <div className={styles.share}>
    <div><span>{label}</span><span aria-label="집계 준비 중">—</span></div>
    <div className={styles.emptyTrack} aria-hidden="true" />
  </div>;
}

export function ContentStatistics() {
  return <section className={styles.statistics} aria-label="콘텐츠 통계" data-admin-statistics="unavailable">
    <article className={styles.statCard}>
      <div className={styles.statHeading}><h2>콘텐츠 총 조회수</h2><span>집계 준비 중</span></div>
      <div className={styles.statBody}>
        <UnavailableChart label="총 조회수" />
        <div className={styles.shares}><p>분야별 조회수</p><UnavailableShare label="애니메이션" /></div>
      </div>
    </article>
    <article className={styles.statCard}>
      <div className={styles.statHeading}><h2>콘텐츠 상태</h2><span>집계 준비 중</span></div>
      <div className={styles.statBody}>
        <UnavailableChart label="총 조회수" />
        <div className={styles.shares}>{["게시 중", "검수 대기", "예약 발행", "신고 접수"].map(label => <UnavailableShare key={label} label={label} />)}</div>
      </div>
    </article>
    <article className={styles.statCard}>
      <div className={styles.statHeading}><h2>콘텐츠 유형 분포</h2><span>집계 준비 중</span></div>
      <div className={styles.statBody}>
        <UnavailableChart label="게시 중인 총 콘텐츠" />
        <dl className={styles.typeLegend}>{[
          ["ost", "관련 OST"], ["event", "팝업 · 전시 · 이벤트"], ["character", "캐릭터 · 관계성"], ["other", "기타"],
        ].map(([icon, label]) => <div key={icon}>
          <dt><img src={`/admin/type-${icon}.svg`} alt="" />{label}</dt><dd aria-label="집계 준비 중">—</dd>
        </div>)}</dl>
      </div>
    </article>
  </section>;
}
