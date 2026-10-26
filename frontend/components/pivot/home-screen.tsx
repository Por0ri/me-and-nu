"use client";

import { useId, useRef, useState } from "react";
import { orderedHomeSlides, type HomeCardModel, type HomeDetailHandler, type HomeSlideModel, type HomeState } from "@/lib/pivot/home";
import { demoBookmarkNotice, type DemoBookmarkControls } from "@/lib/pivot/saved";
import styles from "./home.module.css";

type HomeNotice = { message: string; postId?: string };

export function HomeHeader({ title = "홈", showMascot = true, onNotice }: { title?: string; showMascot?: boolean; onNotice?: (message: string) => void } = {}) {
  const [notice, setNotice] = useState("");
  return <header className={styles.header} data-home-header="">
    <p className={styles.brand}>
      <img src="/pivot/home/chrome/logo.svg" alt="me;nu" width={103} height={20.5862} />
      <span>Little discoveries<br />for a more you</span>
    </p>
    <h1 className={styles.srOnly} tabIndex={-1}>{title}</h1>
    {showMascot ? <div className={styles.headerMascot} aria-hidden="true"><img src="/pivot/home/chrome/mascot.png" alt="" /></div> : null}
    <button type="button" className={styles.notification} aria-label="알림" aria-disabled="true"
      onClick={() => (onNotice ?? setNotice)("알림 기능은 준비 중이에요.")}><img src="/pivot/home/chrome/notification.svg" alt="" width={24} height={24} /></button>
    {notice ? <p className={styles.pendingNotice} role="status">{notice}</p> : null}
  </header>;
}

export function HomeNavigation({ onHome, onMy, onSaved, active = "home", hideAI = false, onNotice }: {
  onHome?: () => void; onMy?: () => void; onSaved?: () => void; active?: "home" | "my" | "saved"; hideAI?: boolean;
  onNotice?: (message: string) => void;
} = {}) {
  const [notice, setNotice] = useState("");
  return <div className={styles.bottomBar}>
    <nav aria-label="주요 메뉴" className={styles.navigation}>
      <button type="button" aria-current={active === "home" ? "page" : undefined} onClick={() => { if (onHome) { onHome(); return; } window.scrollTo({ top: 0, behavior: "instant" }); document.querySelector<HTMLHeadingElement>("main h1")?.focus({ preventScroll: true }); }}><span>HOME</span></button>
      <button type="button" data-saved-navigation="" aria-current={active === "saved" ? "page" : undefined}
        aria-disabled={!onSaved && active !== "saved" || undefined} onClick={() => { if (active === "saved") { onSaved?.(); return; } if (onSaved) onSaved(); else (onNotice ?? setNotice)("SAVE 화면은 준비 중이에요. 저장은 수행하지 않아요."); }}><span>SAVE</span></button>
      <button type="button" data-my-navigation="" aria-current={active === "my" ? "page" : undefined}
        aria-disabled={!onMy && active !== "my" || undefined} onClick={() => { if (active === "my") return; if (onMy) onMy(); else (onNotice ?? setNotice)("MY 화면은 준비 중이에요."); }}><span>MY</span></button>
    </nav>
    {!hideAI ? <button type="button" className={styles.aiButton} aria-label="AI 콘텐츠 탐색" aria-disabled="true"
      onClick={() => (onNotice ?? setNotice)("AI 콘텐츠 탐색은 준비 중이에요.")}><img src="/pivot/home/chrome/ai.svg" alt="" /></button> : null}
    {notice ? <p className={styles.pendingNotice} role="status">{notice}</p> : null}
  </div>;
}

export function WorkSearchField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const id = useId();
  return <div role="search" aria-label="작품 검색" className={styles.search}>
    <label htmlFor={id} className={styles.srOnly}>작품 이름</label>
    <div className={styles.searchInput}>
      <img src="/pivot/home/chrome/search.svg" alt="" width={16} height={16} />
      <input id={id} type="search" value={value} onChange={(event) => onChange(event.target.value)}
        placeholder="작품 이름으로 검색해보세요" aria-describedby={`${id}-help`} autoComplete="off" />
    </div>
    <p id={`${id}-help`} className={styles.srOnly}>입력만 가능해요. 검색 기능은 준비 중이에요.</p>
  </div>;
}

function HomeSlideImage({ image }: { image: HomeSlideModel["image"] }) {
  const [failed, setFailed] = useState(false);
  return image?.src && !failed
    ? <img className={styles.cardImage} src={image.src} alt={image.alt} loading="lazy" draggable={false}
        width={327} height={408} onError={() => setFailed(true)} />
    : <div className={styles.imagePlaceholder} role="img" aria-label="이미지 준비 중">이미지 준비 중</div>;
}

export function HomeCard({ card, demo, onViewDetail, bookmarks, notice, onNotice }: {
  card: HomeCardModel; demo: boolean; onViewDetail?: HomeDetailHandler; bookmarks?: DemoBookmarkControls;
  notice?: HomeNotice; onNotice?: (notice: HomeNotice) => void;
}) {
  const id = useId();
  const track = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);
  const [detailRequested, setDetailRequested] = useState(false);
  const [localActionNotice, setLocalActionNotice] = useState("");
  const actionNotice = notice ? (notice.postId === card.id ? notice.message : "") : localActionNotice;
  function setActionNotice(message: string) {
    if (onNotice) onNotice({ message, postId: card.id });
    else setLocalActionNotice(message);
  }
  const slides = orderedHomeSlides(card.slides);
  const current = Math.max(0, Math.min(active, slides.length - 1));
  const target = { postId: card.id, contentId: card.detailContentId };
  const canBookmark = demo && bookmarks?.kind === "demo";
  // Only explicit button/keyboard navigation writes scroll position. Native touch
  // and momentum are left to CSS Scroll Snap; onScroll only observes the position.
  function goTo(index: number) {
    const element = track.current;
    if (!element || index < 0 || index >= slides.length) return;
    element.scrollTo({ left: index * element.clientWidth });
  }
  return <article className={styles.card} aria-labelledby={`${id}-title`} data-home-post={card.id}>
    <div className={styles.srOnly}>
      {demo ? <p className={styles.exampleLabel}>시연용 콘텐츠</p> : null}
      <h3 id={`${id}-title`}>{card.title}</h3>
    </div>
    {slides.length ? <>
      <div className={styles.carousel} role="region" aria-roledescription="캐러셀" aria-labelledby={`${id}-title`}>
        <div id={`${id}-track`} ref={track} className={styles.slideTrack} tabIndex={0} data-home-track=""
          aria-label="좌우로 슬라이드 탐색" aria-describedby={`${id}-help`}
          onScroll={(event) => {
            const element = event.currentTarget;
            if (element.clientWidth) setActive(Math.round(element.scrollLeft / element.clientWidth));
          }}
          onKeyDown={(event) => {
            if (event.target !== event.currentTarget) return;
            const index = event.key === "ArrowRight" ? current + 1 : event.key === "ArrowLeft" ? current - 1
              : event.key === "Home" ? 0 : event.key === "End" ? slides.length - 1 : null;
            if (index === null) return;
            event.preventDefault(); goTo(index);
          }}>
          {slides.map((slide, index) => <div key={slide.id} className={styles.slide} role="group"
            aria-roledescription="슬라이드" aria-label={`${index + 1} / ${slides.length}`} data-home-slide={slide.id}>
            <figure>
              <HomeSlideImage key={slide.image?.src ?? "missing"} image={slide.image} />
              <figcaption className={slide.image ? styles.srOnly : styles.summary}>{slide.description}</figcaption>
            </figure>
          </div>)}
        </div>
        <div className={styles.slideControls} role="group" aria-label={`${card.title} 슬라이드 이동`}>
          <button type="button" className={styles.slideButton} aria-label="이전 슬라이드" aria-controls={`${id}-track`}
            disabled={current === 0} onClick={() => goTo(current - 1)}>이전</button>
          <p className={styles.position} aria-live="polite" aria-atomic="true" data-home-position="">
            <span className={styles.srOnly}>현재 슬라이드 </span>{current + 1} / {slides.length}
          </p>
          <button type="button" className={styles.slideButton} aria-label="다음 슬라이드" aria-controls={`${id}-track`}
            disabled={current === slides.length - 1} onClick={() => goTo(current + 1)}>다음</button>
        </div>
        <div className={styles.indicators} role="group" aria-label={`${card.title} 슬라이드 위치`}>
          {slides.map((slide, index) => <button key={slide.id} type="button" className={styles.indicator}
            aria-label={`${index + 1}번 슬라이드 보기`} aria-current={current === index ? "true" : undefined}
            aria-controls={`${id}-track`} onClick={() => goTo(index)}><span /></button>)}
        </div>
        <p id={`${id}-help`} className={styles.srOnly}>좌우로 넘기거나 방향키·이전·다음 버튼으로 탐색하세요. 위아래로 스크롤하면 다른 게시물이 이어져요.</p>
      </div>
    </> : <p className={styles.noSlides} role="status">아직 표시할 슬라이드가 없어요.</p>}
    <div className={styles.actions} role="group" aria-label={`${card.title} 반응 기능`}>
      {([['share', '공유'], ['like', '좋아요'], ['save', '저장']] as const).map(([icon, label]) =>
        <button key={icon} type="button" aria-label={label} aria-disabled={icon === "save" && canBookmark ? undefined : true}
          aria-pressed={icon === "save" && canBookmark ? bookmarks.has(target) : undefined}
          data-demo-bookmark={icon === "save" && canBookmark ? card.detailContentId : undefined}
          onClick={() => {
            if (icon === "save" && canBookmark) { const saved = bookmarks.has(target); bookmarks.toggle(target); setActionNotice(demoBookmarkNotice(saved)); }
            else setActionNotice(`${label} 기능은 준비 중이에요. 아직 반영되지 않아요.`);
          }}>
          <span className={icon === "share" ? undefined : styles.actionCircle} data-bookmarked={icon === "save" && canBookmark ? bookmarks.has(target) : undefined}><img src={`/pivot/home/chrome/${icon}.svg`} alt="" width={icon === "share" ? 28 : 24} height={icon === "share" ? 28 : 24} /></span>
        </button>)}
    </div>
    {actionNotice ? <p role="status" className={styles.actionNotice}>{actionNotice}</p> : null}
    <div className={styles.detail}>
      <button type="button" className={styles.detailButton} data-home-detail={card.detailContentId}
        aria-label={`${card.title} 자세히 보기`} aria-describedby={!onViewDetail ? `${id}-detail-help` : undefined}
        onClick={() => {
          if (onViewDetail) onViewDetail({ postId: card.id, contentId: card.detailContentId });
          else setDetailRequested(true);
        }}><span>자세히 보기<img src="/pivot/home/chrome/detail-arrow.svg" alt="" width={8} height={9} /></span></button>
      {!onViewDetail ? <p id={`${id}-detail-help`} className={detailRequested ? styles.detailHelp : styles.srOnly} role="status">
        {detailRequested ? "자세한 콘텐츠 화면을 준비하고 있어요." : "상세 화면은 준비 중이에요."}
      </p> : null}
    </div>
  </article>;
}

export function HomeFeed({ cards, demo, onViewDetail, bookmarks, notice, onNotice }: {
  cards: readonly HomeCardModel[]; demo: boolean; onViewDetail?: HomeDetailHandler; bookmarks?: DemoBookmarkControls;
  notice?: HomeNotice; onNotice?: (notice: HomeNotice) => void;
}) {
  return <section aria-label="콘텐츠 피드" className={styles.feed}>
    <h2 className={styles.srOnly}>콘텐츠 피드</h2>
    <ul>{cards.map((card) => <li key={card.id}><HomeCard card={card} demo={demo} onViewDetail={onViewDetail} bookmarks={bookmarks} notice={notice} onNotice={onNotice} /></li>)}</ul>
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

export function PivotHomeScreen({ state, demo, query, onQueryChange, onRetry, onRestart, onViewDetail, onMy, onSaved, bookmarks }: {
  state: HomeState;
  demo: boolean;
  query: string;
  onQueryChange: (value: string) => void;
  onRetry: () => void;
  onRestart?: () => void;
  onViewDetail?: HomeDetailHandler;
  onMy?: () => void;
  onSaved?: () => void;
  bookmarks?: DemoBookmarkControls;
}) {
  // Keep only the latest feedback, at its existing screen or card location.
  const [notice, setNotice] = useState<HomeNotice>({ message: "" });
  function pending(message: string) { setNotice({ message }); }
  return <main className={styles.screen} data-home-state={state.status} aria-busy={state.status === "loading"}>
    <HomeHeader onNotice={pending} />
    {demo ? <aside className={styles.demoNotice} aria-label="시연용 피드 안내">
      <strong>시연용 예시 피드</strong>
      <p>실제 추천 결과가 아니에요. 선택한 답변과 관계없이 같은 예시를 보여드려요.</p>
    </aside> : null}
    <WorkSearchField value={query} onChange={onQueryChange} />
    {state.status === "ready" ? <HomeFeed cards={state.home.cards} demo={demo} onViewDetail={onViewDetail} bookmarks={bookmarks} notice={notice} onNotice={setNotice} /> : <HomeStatus state={state} onRetry={onRetry} />}
    {demo && onRestart ? <footer className={styles.footer}><button type="button" onClick={onRestart} className={styles.button}>처음부터 시연</button></footer> : null}
    <HomeNavigation onMy={onMy} onSaved={onSaved} onNotice={pending} />
    {notice.message && !notice.postId ? <p className={styles.pendingNotice} role="status">{notice.message}</p> : null}
  </main>;
}
