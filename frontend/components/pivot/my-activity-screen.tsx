"use client";

import type { ActivityState, ActivityTab } from "@/lib/pivot/my-activity";
import type { HomeDetailHandler } from "@/lib/pivot/home";
import { HomeHeader, HomeNavigation } from "./home-screen";
import { ContentListCard } from "./content-list-card";
import homeStyles from "./home.module.css";
import savedStyles from "./saved.module.css";
import styles from "./my-activity.module.css";

const tabs = [{ id: "likes", label: "좋아요한 콘텐츠" }, { id: "recent-views", label: "최근 본 콘텐츠" }] as const;
export function MyActivityScreen({ state, tab, demo, onTab, onMy, onHome, onSaved, onViewDetail, onRetry, onMore }: {
  state: ActivityState; tab: ActivityTab; demo: boolean; onTab: (tab: ActivityTab) => void;
  onMy: () => void; onHome: () => void; onSaved: () => void; onViewDetail: HomeDetailHandler; onRetry: () => void; onMore: () => void;
}) {
  const ready = !demo && state.status === "ready" && state.page.origin === "service" ? state : null;
  const empty = ready?.page.cards.length === 0 && ready.page.nextCursor === null;
  const label = tabs.find(item => item.id === tab)!.label;
  return <main className={`${homeStyles.screen} ${styles.screen}`} data-activity-state={empty ? "empty" : state.status} data-activity-mode={demo ? "demo" : "service"}>
    <HomeHeader title="내 활동" showMascot={false} onBack={onMy} />
    <div className={styles.tabs} role="tablist" aria-label="내 활동 목록">
      {tabs.map((item, index) => <button type="button" key={item.id} id={`activity-tab-${item.id}`} role="tab"
        aria-selected={tab === item.id} aria-controls="activity-panel" tabIndex={tab === item.id ? 0 : -1}
        onClick={() => onTab(item.id)} onKeyDown={event => {
          const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : ["ArrowLeft", "ArrowRight"].includes(event.key) ? 1 - index : null;
          if (next === null) return;
          event.preventDefault(); onTab(tabs[next].id);
          document.getElementById(`activity-tab-${tabs[next].id}`)?.focus();
        }}>{item.label}</button>)}
    </div>
    <section id="activity-panel" role="tabpanel" aria-labelledby={`activity-tab-${tab}`} aria-busy={state.status === "loading" || ready?.loadingMore || undefined}>
      {ready && ready.page.cards.length > 0 ? <ul className={savedStyles.grid} aria-label={`${label} 목록`}>
        {ready.page.cards.map(card => <li key={`${card.topicId}:${card.contentId}`}><ContentListCard key={card.image?.src ?? "missing"} card={card} surface="activity" onViewDetail={onViewDetail} /></li>)}
      </ul> : empty ? <div className={savedStyles.empty}>
        <p role="status">{tab === "likes" ? "좋아요한 콘텐츠가 아직 없어요" : "최근 30일 동안 본 콘텐츠가 없어요"}</p>
        <button type="button" className={savedStyles.emptyButton} onClick={onHome}>콘텐츠 보러가기</button>
        <div className={savedStyles.emptyMascot} aria-hidden="true"><img src="/pivot/home/chrome/mascot.png" alt="" width={1448} height={1086} draggable={false} /></div>
      </div> : <div className={homeStyles.status}>
        <p role={state.status === "error" ? "alert" : "status"}>{demo ? "Demo에서는 실제 좋아요·최근 본 기록을 조회하지 않아요. 로그인 후 내 활동을 확인해주세요."
          : state.status === "loading" ? `${label}를 불러오고 있어요.` : ready?.page.nextCursor ? "다음 목록을 확인해주세요."
          : state.status === "ready" ? "활동 목록을 표시할 수 없어요." : state.message}</p>
        {!demo && state.status === "error" ? <button type="button" className={homeStyles.button} onClick={onRetry}>다시 시도</button> : null}
      </div>}
      {ready?.moreError ? <p role="alert" className={styles.message}>{ready.moreError}</p> : null}
      {ready?.page.nextCursor ? <button type="button" className={`${homeStyles.button} ${styles.more}`} disabled={ready.loadingMore} onClick={onMore}>
        {ready.loadingMore ? "불러오는 중…" : ready.moreError ? "추가 목록 다시 시도" : "더 보기"}
      </button> : null}
    </section>
    <HomeNavigation active="my" onHome={onHome} onMy={onMy} onSaved={onSaved} hideAI />
  </main>;
}
