"use client";

import { useId } from "react";
import type { HomeCardModel, HomeState } from "@/lib/pivot/home";
import styles from "./home.module.css";

export function HomeHeader() {
  return <header className={styles.header}>
    <p className={styles.brand}>me;nu</p>
    <h1 tabIndex={-1}>홈</h1>
  </header>;
}

export function WorkSearchField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const id = useId();
  return <div role="search" aria-label="작품 검색" className={styles.search}>
    <label htmlFor={id} className={styles.srOnly}>작품 이름</label>
    <input id={id} type="search" value={value} onChange={(event) => onChange(event.target.value)}
      placeholder="작품 이름으로 검색해보세요" aria-describedby={`${id}-help`} autoComplete="off" />
    <p id={`${id}-help`}>입력만 가능해요. 검색 기능은 준비 중이에요.</p>
  </div>;
}

export function HomeCard({ card, demo }: { card: HomeCardModel; demo: boolean }) {
  return <article className={styles.card}>
    {card.image ? <img className={styles.cardImage} src={card.image.src} alt={card.image.alt} loading="lazy" /> : null}
    <div className={styles.cardBody}>
      {demo ? <p className={styles.exampleLabel}>시연용 콘텐츠</p> : null}
      <h3>{card.title}</h3>
      <p className={styles.summary}>{card.summary}</p>
    </div>
  </article>;
}

export function HomeFeed({ cards, demo }: { cards: readonly HomeCardModel[]; demo: boolean }) {
  return <section aria-label="콘텐츠 피드" className={styles.feed}>
    <h2 className={styles.srOnly}>콘텐츠 피드</h2>
    <ul>{cards.map((card) => <li key={card.id}><HomeCard card={card} demo={demo} /></li>)}</ul>
  </section>;
}

export function HomeStatus({ state, onRetry }: { state: Exclude<HomeState, { status: "ready" }>; onRetry: () => void }) {
  if (state.status === "loading") return <p className={styles.status} role="status">콘텐츠를 불러오고 있어요.</p>;
  if (state.status === "empty") return <p className={styles.status} role="status">아직 표시할 콘텐츠가 없어요.</p>;
  return <section className={styles.status} aria-label={state.status === "error" ? "콘텐츠 불러오기 오류" : "콘텐츠 연결 안내"}>
    <p role={state.status === "error" ? "alert" : "status"}>{state.message}</p>
    {state.status === "error" ? <button type="button" onClick={onRetry} className={styles.button}>다시 시도</button> : null}
  </section>;
}

export function PivotHomeScreen({ state, demo, query, onQueryChange, onRetry, onRestart }: {
  state: HomeState;
  demo: boolean;
  query: string;
  onQueryChange: (value: string) => void;
  onRetry: () => void;
  onRestart?: () => void;
}) {
  return <main className={styles.screen} data-home-state={state.status} aria-busy={state.status === "loading"}>
    <HomeHeader />
    {demo ? <aside className={styles.demoNotice} aria-label="시연용 피드 안내">
      <strong>시연용 예시 피드</strong>
      <p>실제 추천 결과가 아니에요. 선택한 답변과 관계없이 같은 예시를 보여드려요.</p>
    </aside> : null}
    <WorkSearchField value={query} onChange={onQueryChange} />
    {state.status === "ready" ? <HomeFeed cards={state.home.cards} demo={demo} /> : <HomeStatus state={state} onRetry={onRetry} />}
    {demo && onRestart ? <footer className={styles.footer}><button type="button" onClick={onRestart} className={styles.button}>처음부터 시연</button></footer> : null}
  </main>;
}
