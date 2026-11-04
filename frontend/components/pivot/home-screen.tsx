"use client";

import { useEffect, useId, useRef, useState } from "react";
import { orderedHomeSlides, type HomeCardModel, type HomeDetailHandler, type HomeSlideModel, type HomeState, type HomeWorkSuggestion } from "@/lib/pivot/home";
import type { RecentSearch } from "@/lib/pivot/recent-search";
import { demoBookmarkNotice } from "@/lib/pivot/saved";
import { dismissActionNotice } from "@/lib/pivot/action-notice";
import { bookmarkPending, bookmarkValue, type BookmarkControls } from "@/lib/pivot/bookmark-service";
import { ContentCarousel } from "./content-carousel";
import styles from "./home.module.css";
import { HomeAIButton } from "./home-ai-button";
import { useHomeExposure, useInteractions } from "./interaction-context";

type HomeNotice = { message: string; postId?: string };

export function HomeHeader({ title = "홈", showMascot = true, onNotice, onBack }: {
  title?: string; showMascot?: boolean; onNotice?: (message: string) => void;
  /** A round back button before the logo (My Page sub-pages such as 프로필 수정, 2026-11 design). */
  onBack?: () => void;
} = {}) {
  const [notice, setNotice] = useState("");
  return <header className={styles.header} data-home-header="">
    <p className={styles.brand}>
      {onBack ? <button type="button" className={styles.headerBack} aria-label="뒤로가기" onClick={onBack}>
        <span><img src="/pivot/detail/back.svg" alt="" width={24} height={24} /></span>
      </button> : null}
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
  const [localNotice, setLocalNotice] = useState({ message: "" });
  const notice = localNotice.message;
  function setNotice(message: string) { setLocalNotice({ message }); }
  useEffect(() => {
    if (onNotice) return;
    return dismissActionNotice(localNotice.message, () => {
      setLocalNotice(current => current === localNotice ? { message: "" } : current);
    });
  }, [localNotice, onNotice]);
  return <div className={styles.bottomBar}>
    <nav aria-label="주요 메뉴" className={styles.navigation}>
      <button type="button" aria-current={active === "home" ? "page" : undefined} onClick={() => { if (onHome) { onHome(); return; } window.scrollTo({ top: 0, behavior: "instant" }); document.querySelector<HTMLHeadingElement>("main h1")?.focus({ preventScroll: true }); }}><span>HOME</span></button>
      <button type="button" data-saved-navigation="" aria-current={active === "saved" ? "page" : undefined}
        aria-disabled={!onSaved && active !== "saved" || undefined} onClick={() => { if (active === "saved") { onSaved?.(); return; } if (onSaved) onSaved(); else (onNotice ?? setNotice)("SAVE 화면은 준비 중이에요. 저장은 수행하지 않아요."); }}><span>SAVE</span></button>
      <button type="button" data-my-navigation="" aria-current={active === "my" ? "page" : undefined}
        aria-disabled={!onMy && active !== "my" || undefined} onClick={() => { if (active === "my") return; if (onMy) onMy(); else (onNotice ?? setNotice)("MY 화면은 준비 중이에요."); }}><span>MY</span></button>
    </nav>
    {!hideAI ? <HomeAIButton onNotice={onNotice ?? setNotice} /> : null}
    {notice ? <p className={styles.pendingNotice} role="status">{notice}</p> : null}
  </div>;
}

export type SearchNotice = { message: string; id: number };

/** Home search (service only). open: typing, with the back button, the panel and the dimmed screen.
 *  result: the cards found by the last search, shown alone instead of the feed ("no result" when empty). */
export type HomeSearchView = {
  open: boolean;
  result: { label: string; cards: readonly HomeCardModel[] } | null;
  suggestions: readonly HomeWorkSuggestion[];
  recent: readonly RecentSearch[];
  onOpen(): void;
  onBack(): void;
  onClear(): void;
  onClose(): void;
  onPick(entry: RecentSearch): void;
  onRemoveRecent(entry: RecentSearch): void;
  onClearRecent(): void;
};

function CloseIcon({ size = 20 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <path d="M6.4 19 5 17.6l5.6-5.6L5 6.4 6.4 5l5.6 5.6L17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6Z" fill="currentColor" />
  </svg>;
}

// Under the box: recent searches while it is empty, up to three matching works while typing.
function SearchPanel({ id, query, search }: { id: string; query: string; search: HomeSearchView }) {
  if (query.trim()) {
    return search.suggestions.length ? <div id={id} className={styles.searchPanel}>
      <ul aria-label="관련 작품">{search.suggestions.map((work) => <li key={work.id}>
        <button type="button" className={styles.searchPick} onClick={() => search.onPick({ label: work.name, workId: work.id })}>{work.name}</button>
      </li>)}</ul>
    </div> : null;
  }
  return search.recent.length ? <div id={id} className={styles.searchPanel}>
    <div className={styles.recentHeader}>
      <h2>최근 검색</h2>
      <button type="button" onClick={search.onClearRecent}>모두 삭제</button>
    </div>
    <ul aria-label="최근 검색">{search.recent.map((entry) => <li key={`${entry.workId ?? ""}:${entry.label}`} className={styles.recentItem}>
      <button type="button" className={styles.searchPick} onClick={() => search.onPick(entry)}>{entry.label}</button>
      <button type="button" className={styles.recentRemove} aria-label={`최근 검색 ${entry.label} 삭제`} onClick={() => search.onRemoveRecent(entry)}><CloseIcon /></button>
    </li>)}</ul>
  </div> : null;
}

export function WorkSearchField({ value, onChange, onSubmit, notice, onNoticeEnd, search, active = false }: {
  value: string; onChange: (value: string) => void; onSubmit?: (value: string) => void;
  notice?: SearchNotice | null; onNoticeEnd?: () => void;
  search?: HomeSearchView; active?: boolean;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  return <div role="search" aria-label="작품 검색" className={styles.search} data-search-active={active || undefined}>
    <label htmlFor={id} className={styles.srOnly}>작품 이름</label>
    <div className={styles.searchRow}>
      {search && active ? <button type="button" className={styles.searchBack} aria-label="검색 나가기" onClick={search.onBack}>
        <img src="/pivot/detail/back.svg" alt="" width={20} height={20} />
      </button> : null}
      <div className={styles.searchInput}>
        <img src="/pivot/home/chrome/search.svg" alt="" width={16} height={16} />
        <input ref={input} id={id} type="search" value={value} onChange={(event) => onChange(event.target.value)}
          onFocus={search?.onOpen}
          onKeyDown={(event) => {
            if (search && event.key === "Escape") { event.preventDefault(); search.onBack(); input.current?.blur(); return; }
            // Korean input: Enter that finishes a composing syllable is not a search.
            if (onSubmit && event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); onSubmit(value); if (search) input.current?.blur(); }
          }}
          enterKeyHint={onSubmit ? "search" : undefined} aria-expanded={search ? search.open : undefined}
          aria-controls={search?.open ? `${id}-panel` : undefined}
          placeholder="작품 이름으로 검색해보세요" aria-describedby={`${id}-help`} autoComplete="off" />
        {search && value ? <button type="button" className={styles.searchClear} aria-label="검색어 지우기"
          onClick={() => { search.onClear(); input.current?.focus(); }}><CloseIcon size={22} /></button> : null}
      </div>
    </div>
    <p id={`${id}-help`} className={styles.srOnly}>{search
      ? "작품 이름을 쓰면 관련 작품을 보여 드려요. 작품을 고르거나 Enter를 누르면 그 작품 글만 보여 드려요."
      : onSubmit ? "작품 이름을 쓰고 Enter를 누르면 그 작품 글을 보여 드려요."
      : "입력만 가능해요. 검색 기능은 준비 중이에요."}</p>
    {search?.open ? <SearchPanel id={`${id}-panel`} query={value} search={search} /> : null}
    {notice ? <p key={notice.id} className={styles.searchNotice} role="status" onAnimationEnd={onNoticeEnd}>{notice.message}</p> : null}
  </div>;
}

function SearchEmpty({ label }: { label: string }) {
  return <div className={styles.searchEmpty}>
    <img className={styles.searchEmptyMascot} src="/pivot/home/search/empty-mascot@1x.png"
      srcSet="/pivot/home/search/empty-mascot@1x.png 1x, /pivot/home/search/empty-mascot@2x.png 2x, /pivot/home/search/empty-mascot@3x.png 3x"
      alt="" width={115} height={69} />
    <div className={styles.searchEmptyCard} role="status">
      <div className={styles.searchEmptyBody}>
        <p className={styles.searchEmptyTitle}>‘{label}’ 검색어에 대한 결과가 없어요</p>
        <p className={styles.searchEmptyHelp}>작품 이름을 확인하거나 다른 검색어로 다시 검색해 보세요</p>
      </div>
    </div>
  </div>;
}

function HomeSlideImage({ image }: { image: HomeSlideModel["image"] }) {
  const [failed, setFailed] = useState(false);
  return image?.src && !failed
    ? <img className={styles.cardImage} src={image.src} alt={image.alt} loading="lazy" draggable={false}
        width={327} height={408} onError={() => setFailed(true)} />
    : <div className={styles.imagePlaceholder} role="img" aria-label="이미지 준비 중">이미지 준비 중</div>;
}

export function HomeCard({ card, demo, onViewDetail, bookmarks, notice, onNotice, impressions = true }: {
  card: HomeCardModel; demo: boolean; onViewDetail?: HomeDetailHandler; bookmarks?: BookmarkControls;
  notice?: HomeNotice; onNotice?: (notice: HomeNotice) => void; impressions?: boolean;
}) {
  const id = useId();
  const track = useRef<HTMLDivElement>(null);
  const article = useRef<HTMLElement>(null);
  const interactions = useInteractions();

  const [active, setActive] = useState(0);
  const [detailRequested, setDetailRequested] = useState(false);
  const [localNotice, setLocalNotice] = useState({ message: "" });
  const localActionNotice = localNotice.message;
  useEffect(() => {
    if (onNotice) return;
    return dismissActionNotice(localNotice.message, () => {
      setLocalNotice(current => current === localNotice ? { message: "" } : current);
    });
  }, [localNotice, onNotice]);
  const actionNotice = notice ? (notice.postId === card.id ? notice.message : "") : localActionNotice;
  function setActionNotice(message: string) {
    if (onNotice) onNotice({ message, postId: card.id });
    else setLocalNotice({ message });
  }
  const slides = orderedHomeSlides(card.slides);
  const serviceCarousel = !demo && slides.some((slide) => !!slide.layout || slide.image?.protected);
  const current = Math.max(0, Math.min(active, slides.length - 1));
  const target = { postId: card.id, contentId: card.detailContentId, ...(card.topicId === undefined ? {} : { topicId: card.topicId }), recommendationId: card.recommendationId, recommendationReason: card.recommendationReason };
  const trackSlide = useHomeExposure(article, target, demo, impressions);
  const canLike = !demo && !!interactions && !!card.topicId;
  const reaction = interactions?.reactions.value(target, card);
  const liking = canLike && interactions.reactions.isPending(target, "like");
  async function toggleLike() {
    if (!canLike || liking) return;
    try { await interactions.reactions.toggleLike(target, card, "home_feed"); setActionNotice(interactions.reactions.value(target, card).liked ? "좋아요를 눌렀어요." : "좋아요를 취소했어요."); }
    catch (error) { setActionNotice(error instanceof Error ? error.message : "좋아요를 저장하지 못했어요."); }
  }
  function recordSlide(index: number) {
    trackSlide(index, slides.length);
  }
  const canBookmark = !!bookmarks && (demo ? bookmarks.kind === "demo" : bookmarks.kind === "service" && !!card.topicId);
  const saved = canBookmark && bookmarkValue(bookmarks!, target, card.saved);
  const saving = canBookmark && bookmarkPending(bookmarks, target);
  async function toggleBookmark() {
    if (!canBookmark || !bookmarks || saving) return;
    if (bookmarks.kind === "demo") {
      bookmarks.toggle(target); setActionNotice(demoBookmarkNotice(saved)); return;
    }
    setActionNotice(saved ? "저장을 취소하고 있어요." : "저장하고 있어요.");
    try {
      const nowSaved = await bookmarks.toggle(target, card.saved);
      setActionNotice(nowSaved ? "저장했어요." : "저장을 취소했어요.");
    } catch (error) {
      setActionNotice(error instanceof Error ? error.message : "저장하지 못했어요. 다시 시도해 주세요.");
    }
  }
  // Only explicit button/keyboard navigation writes scroll position. Native touch
  // and momentum are left to CSS Scroll Snap; onScroll only observes the position.
  function goTo(index: number) {
    const element = track.current;
    if (!element || index < 0 || index >= slides.length) return;
    element.scrollTo({ left: index * element.clientWidth });
  }
  return <article ref={article} className={styles.card} aria-labelledby={`${id}-title`} data-home-post={card.id}>
    {card.notices?.some(notice => notice.code === "DEV_PREVIEW") ? <p className={styles.previewBadge} data-content-preview="">
      {card.notices.filter(notice => notice.code === "DEV_PREVIEW" || notice.code === "REVIEW_REQUIRED").map(notice => notice.message).join(" · ")}
    </p> : null}
    <div className={styles.srOnly}>
      {demo ? <p className={styles.exampleLabel}>시연용 콘텐츠</p> : null}
      <h3 id={`${id}-title`}>{card.title}</h3>
    </div>
    {serviceCarousel ? <ContentCarousel key={card.id} slides={slides} title={card.title} labelledBy={`${id}-title`} scope="home" onSlideChange={recordSlide} /> : slides.length ? <>
      <div className={styles.carousel} role="region" aria-roledescription="캐러셀" aria-labelledby={`${id}-title`}>
        <div id={`${id}-track`} ref={track} className={styles.slideTrack} tabIndex={0} data-home-track=""
          aria-label="좌우로 슬라이드 탐색" aria-describedby={`${id}-help`}
          onScroll={(event) => {
            const element = event.currentTarget;
            if (element.clientWidth) { const index = Math.round(element.scrollLeft / element.clientWidth); setActive(index); recordSlide(index); }
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
        <button key={icon} type="button" aria-label={label} aria-disabled={(icon === "save" && canBookmark) || (icon === "like" && canLike) ? undefined : true}
          aria-pressed={icon === "save" && canBookmark ? saved : icon === "like" && canLike ? reaction?.liked === true : undefined}
          aria-busy={(icon === "save" && saving) || (icon === "like" && liking) || undefined} disabled={(icon === "save" && saving) || (icon === "like" && liking)}
          data-demo-bookmark={icon === "save" && canBookmark && demo ? card.detailContentId : undefined}
          data-service-bookmark={icon === "save" && canBookmark && !demo ? card.detailContentId : undefined}
          onClick={() => {
            if (icon === "save" && canBookmark) { void toggleBookmark(); }
            else if (icon === "like" && canLike) { void toggleLike(); }
            else setActionNotice(`${label} 기능은 준비 중이에요. 아직 반영되지 않아요.`);
          }}>
          <span className={icon === "share" ? undefined : styles.actionCircle} data-bookmarked={icon === "save" && canBookmark ? saved : undefined} data-liked={icon === "like" && canLike ? reaction?.liked === true : undefined}><img src={`/pivot/home/chrome/${icon}.svg`} alt="" width={icon === "share" ? 28 : 24} height={icon === "share" ? 28 : 24} /></span>
        </button>)}
    </div>
    {actionNotice ? <p role="status" className={styles.actionNotice}>{actionNotice}</p> : null}
    <div className={styles.detail}>
      <button type="button" className={styles.detailButton} data-home-detail={card.detailContentId}
        aria-label={`${card.title} 자세히 보기`} aria-describedby={!onViewDetail ? `${id}-detail-help` : undefined}
        onClick={() => {
          if (onViewDetail) onViewDetail(target);
          else setDetailRequested(true);
        }}><span>자세히 보기<img src="/pivot/home/chrome/detail-arrow.svg" alt="" width={8} height={9} /></span></button>
      {!onViewDetail ? <p id={`${id}-detail-help`} className={detailRequested ? styles.detailHelp : styles.srOnly} role="status">
        {detailRequested ? "자세한 콘텐츠 화면을 준비하고 있어요." : "상세 화면은 준비 중이에요."}
      </p> : null}
    </div>
  </article>;
}

export function HomeFeed({ cards, demo, onViewDetail, bookmarks, notice, onNotice, label = "콘텐츠 피드", impressions = true }: {
  cards: readonly HomeCardModel[]; demo: boolean; onViewDetail?: HomeDetailHandler; bookmarks?: BookmarkControls;
  notice?: HomeNotice; onNotice?: (notice: HomeNotice) => void; label?: string; impressions?: boolean;
}) {
  return <section aria-label={label} className={styles.feed}>
    <h2 className={styles.srOnly}>{label}</h2>
    <ul>{cards.map((card) => <li key={card.id}><HomeCard card={card} demo={demo} onViewDetail={onViewDetail} bookmarks={bookmarks} notice={notice} onNotice={onNotice} impressions={impressions} /></li>)}</ul>
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

export function PivotHomeScreen({ state, demo, query, onQueryChange, onSearch, search, searchNotice, onSearchNoticeEnd, onRetry, onRestart, onViewDetail, onMy, onSaved, bookmarks }: {
  state: HomeState;
  demo: boolean;
  query: string;
  onQueryChange: (value: string) => void;
  onSearch?: (value: string) => void;
  search?: HomeSearchView;
  searchNotice?: SearchNotice | null;
  onSearchNoticeEnd?: () => void;
  onRetry: () => void;
  onRestart?: () => void;
  onViewDetail?: HomeDetailHandler;
  onMy?: () => void;
  onSaved?: () => void;
  bookmarks?: BookmarkControls;
}) {
  // Keep only the latest feedback, at its existing screen or card location.
  const [notice, setNotice] = useState<HomeNotice>({ message: "" });
  useEffect(() => {
    return dismissActionNotice(notice.message, () => {
      setNotice(current => current === notice ? { message: "" } : current);
    });
  }, [notice]);
  function pending(message: string) { setNotice({ message }); }
  const result = search?.result ?? null;
  return <main className={styles.screen} data-home-state={state.status} data-search-open={search?.open || undefined} aria-busy={state.status === "loading"}>
    <HomeHeader onNotice={pending} />
    {demo ? <aside className={styles.demoNotice} aria-label="시연용 피드 안내">
      <strong>시연용 예시 피드</strong>
      <p>실제 추천 결과가 아니에요. 선택한 답변과 관계없이 같은 예시를 보여드려요.</p>
    </aside> : null}
    <WorkSearchField value={query} onChange={onQueryChange} onSubmit={onSearch} notice={searchNotice} onNoticeEnd={onSearchNoticeEnd}
      search={search} active={!!search?.open} />
    {search?.open ? <button type="button" className={styles.searchDim} aria-label="검색 닫기" tabIndex={-1} onClick={search.onBack} /> : null}
    {result && search ? <section aria-labelledby="home-search-results">
      <div className={styles.resultsHeader}>
        <h2 id="home-search-results"><strong>‘{result.label}’</strong> 검색 결과</h2>
        <button type="button" onClick={search.onClose}>검색 결과 닫기</button>
      </div>
      {result.cards.length
        ? <HomeFeed cards={result.cards} demo={demo} onViewDetail={onViewDetail} bookmarks={bookmarks} notice={notice} onNotice={setNotice} label="검색 결과 글" impressions={false} />
        : <SearchEmpty label={result.label} />}
    </section>
      : state.status === "ready" ? <HomeFeed cards={state.home.cards} demo={demo} onViewDetail={onViewDetail} bookmarks={bookmarks} notice={notice} onNotice={setNotice} /> : <HomeStatus state={state} onRetry={onRetry} />}
    {demo && onRestart ? <footer className={styles.footer}><button type="button" onClick={onRestart} className={styles.button}>처음부터 시연</button></footer> : null}
    <HomeNavigation onMy={onMy} onSaved={onSaved} onNotice={pending} />
    {notice.message && !notice.postId ? <p className={styles.pendingNotice} role="status">{notice.message}</p> : null}
  </main>;
}
