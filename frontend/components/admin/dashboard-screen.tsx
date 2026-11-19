"use client";

import { useState } from "react";
import { MetricCard, PendingStatistic, PreferenceDistribution, RankingPanel, StatisticsPanel } from "./statistics-panels";
import shared from "./admin.module.css";
import styles from "./statistics.module.css";

const attentionFilters = ["전체", "신고", "문의", "예약 공지"] as const;

export function DashboardScreen() {
  const [filter, setFilter] = useState<typeof attentionFilters[number]>("전체");
  return <div className={styles.dashboard} data-admin-dashboard>
    <div className={`${shared.heading} ${styles.dashboardHeading}`}><h1>대시보드</h1><p>me;nu 서비스의 핵심 지표와 운영 현황을 한눈에 확인하세요.</p></div>
    <div className={styles.dashboardMetrics}><MetricCard title="이용 회원" description="피드 진입 또는 콘텐츠 열람" /><MetricCard title="가입 후 피드 진입률" />
      <MetricCard title="콘텐츠 조회 수" description="콘텐츠 상세 열람 합계" /><MetricCard title="작품 추천 품질" description="시리즈 NDCG@10" /></div>
    <div className={styles.dashboardGrid}>
      <div className={styles.dashboardColumn}>
        <StatisticsPanel title="지금 확인이 필요한 항목" description="분류별로 우선 확인할 항목 표시" className={styles.attention}>
          <div className={styles.attentionFilters} role="group" aria-label="확인 항목 분류">{attentionFilters.map(label =>
            <button key={label} type="button" aria-pressed={filter === label} onClick={() => setFilter(label)}>{label}<span aria-label="집계 준비 중">—</span></button>)}</div>
          <div className={styles.pendingList} role="status" aria-live="polite"><strong>—</strong><p>{filter} 항목 집계 준비 중</p></div>
          <nav className={shared.pagination} aria-label="확인 항목 페이지"><button type="button" disabled aria-label="이전 페이지"><img src="/admin/previous.svg" alt="" /></button><button type="button" disabled aria-label="페이지 집계 준비 중">—</button><button type="button" disabled aria-label="다음 페이지"><img src="/admin/next.svg" alt="" /></button></nav>
        </StatisticsPanel>
        <RankingPanel />
      </div>
      <div className={styles.dashboardColumn}>
        <PreferenceDistribution />
        <StatisticsPanel title="에이전트 콘텐츠 생성" description="최근 24시간 · 생성 작업 결과" className={styles.generation}>
          <div className={styles.generationTrack} aria-hidden="true" />
          <p className={styles.generationCounts}>성공 <strong>—</strong> · 진행 중 <strong>—</strong> · 실패 <strong>—</strong></p><PendingStatistic />
        </StatisticsPanel>
        <StatisticsPanel title="오늘의 알림 및 공지" className={styles.today}><p className={styles.todayDescription}>남은 예약 일정 <strong>—</strong></p><PendingStatistic /></StatisticsPanel>
      </div>
    </div>
    <p className={styles.availability}>집계 준비 중 · —는 0이 아닌 확인할 수 없는 값입니다.</p>
  </div>;
}
