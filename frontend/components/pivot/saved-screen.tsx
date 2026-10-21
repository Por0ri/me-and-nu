"use client";

import { useId, useState } from "react";
import type { HomeDetailHandler } from "@/lib/pivot/home";
import type { SavedCard, SavedState } from "@/lib/pivot/saved";
import { HomeHeader, HomeNavigation } from "./home-screen";
import homeStyles from "./home.module.css";
import styles from "./saved.module.css";

export function SavedContentCard({ card, onViewDetail }: { card: SavedCard; onViewDetail: HomeDetailHandler }) {
  const id = useId(); const [failed, setFailed] = useState(false);
  return <button type="button" className={styles.card} data-saved-content={card.contentId} data-saved-post={card.postId}
    aria-labelledby={id} onClick={() => onViewDetail({ postId: card.postId, contentId: card.contentId })}>
    <span id={id} className={homeStyles.srOnly}>{card.title} 자세히 보기</span>
    {card.image?.src && !failed ? <img src={card.image.src} alt="" width={158} height={203} loading="lazy" draggable={false} onError={() => setFailed(true)} />
      : <span className={styles.placeholder}><span>이미지 준비 중</span><span>{card.title}</span></span>}
  </button>;
}

export function PivotSavedScreen({ state, demo, onHome, onMy, onViewDetail, onRetry }: {
  state: SavedState; demo: boolean; onHome: () => void; onMy: () => void; onViewDetail: HomeDetailHandler; onRetry: () => void;
}) {
  const list = state.status === "ready" && state.saved.origin === (demo ? "demo" : "service") ? state.saved : null;
  return <main className={homeStyles.screen} data-saved-state={state.status} data-saved-mode={demo ? "demo" : "service"}
    aria-busy={state.status === "loading"}>
    <HomeHeader title="저장한 콘텐츠" showMascot={false} />
    {list ? <ul className={styles.grid} aria-label="저장한 콘텐츠 목록">
      {list.cards.map((card) => <li key={card.contentId}><SavedContentCard key={card.image?.src ?? "missing"} card={card} onViewDetail={onViewDetail} /></li>)}
    </ul> : <section className={`${homeStyles.status} ${styles.status}`} aria-label="저장 목록 상태">
      <p role={state.status === "error" || state.status === "ready" ? "alert" : "status"}>{state.status === "loading" ? "저장 목록을 불러오고 있어요."
        : state.status === "empty" ? "아직 저장한 콘텐츠가 없어요."
        : state.status === "ready" ? "저장 목록을 표시할 수 없어요." : state.message}</p>
      {state.status === "error" ? <button type="button" className={homeStyles.button} onClick={onRetry}>다시 시도</button> : null}
      {state.status === "empty" ? <button type="button" className={homeStyles.button} onClick={onHome}>HOME에서 둘러보기</button> : null}
    </section>}
    {demo ? <p className={styles.demoHelp}>Demo 예시 저장 목록이에요. 북마크 선택은 이번 시연에서만 유지되고, 새로고침하거나 시연을 다시 시작하면 초기화돼요. 서버에는 저장하지 않아요.</p> : null}
    <HomeNavigation active="saved" onHome={onHome} onMy={onMy} hideAI />
  </main>;
}
