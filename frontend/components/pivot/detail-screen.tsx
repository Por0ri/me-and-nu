"use client";

import { useEffect, useRef, useState } from "react";
import type { DetailArticle, DetailState } from "@/lib/pivot/detail";
import { demoBookmarkNotice } from "@/lib/pivot/saved";
import { bookmarkPending, bookmarkValue, type BookmarkControls } from "@/lib/pivot/bookmark-service";
import { HomeNavigation } from "./home-screen";
import { ContentCover, ContentMetadata } from "./content-carousel";
import homeStyles from "./home.module.css";
import styles from "./detail.module.css";

function DetailImage({ article }: { article: DetailArticle }) {
  const [failed, setFailed] = useState(false);
  return article.image?.src && !failed
    ? <img className={styles.cover} src={article.image.src} alt={article.image.alt} width={327} height={408}
        fetchPriority="high" onError={() => setFailed(true)} />
    : <div className={styles.coverPlaceholder} role="img" aria-label="대표 이미지 준비 중">대표 이미지 준비 중</div>;
}

export function PivotDetailScreen({ state, demo, onBack, onRetry, onMy, onSaved, onHome, fromSaved = false, bookmarks }: {
  state: DetailState; demo: boolean; onBack: () => void; onRetry: () => void; onMy?: () => void; onSaved?: () => void; onHome?: () => void;
  fromSaved?: boolean; bookmarks?: BookmarkControls;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [notice, setNotice] = useState("");
  const [feedback, setFeedback] = useState<"more" | "stop" | null>(null);
  const [aiExpanded, setAiExpanded] = useState(false);
  const target = state.status === "ready" ? { postId: state.article.postId, contentId: state.article.contentId,
    ...(state.article.topicId === undefined ? {} : { topicId: state.article.topicId }) } : null;
  const canBookmark = !!target && !!bookmarks && (demo ? bookmarks.kind === "demo" : bookmarks.kind === "service" && !!target.topicId);
  const initialSaved = state.status === "ready" && state.article.bookmarked === true;
  const saved = canBookmark && bookmarkValue(bookmarks!, target!, initialSaved);
  const saving = canBookmark && bookmarkPending(bookmarks, target!);
  async function toggleBookmark() {
    if (!canBookmark || !bookmarks || !target || saving) return;
    if (bookmarks.kind === "demo") { bookmarks.toggle(target); setNotice(demoBookmarkNotice(saved)); return; }
    setNotice(saved ? "저장을 취소하고 있어요." : "저장하고 있어요.");
    try {
      const nowSaved = await bookmarks.toggle(target, initialSaved);
      setNotice(nowSaved ? "저장했어요." : "저장을 취소했어요.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "저장하지 못했어요. 다시 시도해 주세요.");
    }
  }
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [state.status]);
  return <main className={styles.screen} data-detail-state={state.status} aria-busy={state.status === "loading"}>
    <header className={styles.header}>
      <button type="button" className={styles.iconButton} aria-label="뒤로가기" onClick={onBack}>
        <span className={styles.circle}><img src="/pivot/detail/back.svg" alt="" width={24} height={24} /></span>
      </button>
      <div className={styles.actions}>
        {([['share', '공유'], ['like', '좋아요'], ['save', '저장']] as const).map(([icon, label]) =>
          <button key={icon} type="button" className={styles.iconButton} aria-label={label} aria-disabled={icon === "save" && canBookmark ? undefined : true}
            aria-pressed={icon === "save" && canBookmark ? saved : undefined}
            aria-busy={icon === "save" && saving || undefined} disabled={icon === "save" && saving}
            data-demo-bookmark={icon === "save" && canBookmark && demo ? target!.contentId : undefined}
            data-service-bookmark={icon === "save" && canBookmark && !demo ? target!.contentId : undefined}
            onClick={() => {
              if (icon === "save" && canBookmark) { void toggleBookmark(); }
              else setNotice(`${label} 기능은 준비 중이에요. 아직 반영되지 않아요.`);
            }}>
            <span className={icon === "share" ? styles.plainIcon : styles.circle} data-bookmarked={icon === "save" && canBookmark ? saved : undefined}>
              <img src={`/pivot/home/chrome/${icon}.svg`} alt="" width={icon === "share" ? 28 : 24} height={icon === "share" ? 28 : 24} />
            </span>
          </button>)}
      </div>
    </header>
    <h1 ref={heading} tabIndex={-1} className={homeStyles.srOnly}>{state.status === "ready" ? [state.article.work, state.article.title].filter(Boolean).join(" · ") : "콘텐츠 상세"}</h1>
    {state.status === "ready" ? <article aria-label={state.article.title} data-detail-content={state.article.contentId} data-detail-post={state.article.postId}>
      {!demo && state.article.slides?.length ? <ContentCover key={state.article.contentId}
        slides={state.article.slides} title={state.article.title} /> : <DetailImage key={state.article.image?.src ?? "missing"} article={state.article} />}
      <div className={styles.body}>
        {state.article.sections.map((section, index) => <section key={index} className={styles.section}>
          {section.heading ? <h2>{section.heading}</h2> : null}
          {section.paragraphs.map((paragraph, paragraphIndex) => <p key={paragraphIndex}>{paragraph}</p>)}
        </section>)}
        {!demo && state.article.displayMode === "link_excerpt" && state.article.sourceUrl ? <p className={styles.sourceLink}>
          <a href={state.article.sourceUrl} target="_blank" rel="noopener noreferrer">원문에서 계속 읽기</a>
        </p> : null}
        {!demo ? <footer data-detail-publication="">
        <ContentMetadata productionType={state.article.productionType} notices={state.article.notices}
          publishedAt={state.article.publishedAt} publisher={state.article.publisher} />
        {state.article.sources?.length || state.article.sourceUrl ? <section className={styles.sources} aria-label="콘텐츠 출처">
          <h2>출처</h2>
          <ul>{(state.article.sources?.length ? state.article.sources : [{ url: state.article.sourceUrl!, title: "원문 출처", publisher: state.article.publisher }]).map((source, index) =>
            <li key={`${source.url}-${index}`}><a href={source.url} target="_blank" rel="noopener noreferrer">
              {source.title || source.publisher || new URL(source.url).hostname}
            </a>{source.publisher && source.title !== source.publisher ? <span>{source.publisher}</span> : null}</li>)}</ul>
        </section> : null}
        </footer> : null}
        {demo ? <section className={styles.feedback} aria-labelledby="detail-feedback-title">
          <h2 id="detail-feedback-title">이런 글, 더 보고 싶으세요?</h2>
          <div className={styles.feedbackButtons}>
            {([['stop', '그만 볼래요'], ['more', '더 볼래요']] as const).map(([value, label]) =>
              <button key={value} type="button" aria-pressed={feedback === value} onClick={() => setFeedback(feedback === value ? null : value)}>{label}</button>)}
          </div>
          <p className={styles.demoHelp}>Demo 선택 · 실제 추천·저장·서버 전송 없음</p>
          <p role="status" className={styles.feedbackStatus}>{feedback ? `이 화면에서만 ‘${feedback === "more" ? "더 볼래요" : "그만 볼래요"}’를 선택했어요.` : "아직 선택하지 않았어요."}</p>
        </section> : null}
      </div>
    </article> : <section className={styles.status} aria-label="상세 콘텐츠 상태">
      <p role={state.status === "error" ? "alert" : "status"}>{state.status === "loading" ? "글을 불러오고 있어요."
        : state.status === "not-found" ? "요청한 콘텐츠를 찾을 수 없어요."
        : state.status === "empty" ? "아직 준비된 본문이 없어요." : state.message}</p>
      {state.status === "error" ? <button type="button" onClick={onRetry}>다시 시도</button> : null}
      {state.status !== "loading" ? <button type="button" onClick={onBack}>{fromSaved ? "저장 목록으로 돌아가기" : "Home으로 돌아가기"}</button> : null}
    </section>}
    {/* Keep shared navigation consistent with Home and My Page. */}
    <div inert={aiExpanded}><HomeNavigation active={fromSaved ? "saved" : "home"} onHome={onHome ?? onBack} onMy={onMy} onSaved={onSaved} hideAI /></div>
    <div className={styles.aiDock}>
      {aiExpanded ? <div className={styles.aiPrompt}><button type="button" aria-disabled="true"
        onClick={() => setNotice("AI 질문 기능은 준비 중이에요. 질문을 전송하지 않아요.")}>이 콘텐츠에서 어떤 점이 궁금하세요?</button></div> : null}
      <button type="button" className={styles.aiButton} aria-label="AI 질문 안내" aria-expanded={aiExpanded}
        onClick={() => setAiExpanded((expanded) => !expanded)}><img src="/pivot/detail/ai.svg" alt="" /></button>
    </div>
    {notice ? <p role="status" className={styles.notice}>{notice}</p> : null}
  </main>;
}
