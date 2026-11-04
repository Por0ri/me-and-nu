"use client";
import { useEffect, useRef, useState, type Dispatch, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { type OnboardingCatalog } from "@/lib/pivot/onboarding-catalog";
import { MAX_PREFERENCE_TAGS_PER_KIND, selectedPreferenceCount, canAdvance, selectionKey, type OnboardingAction, type OnboardingState, type OnboardingWork } from "@/lib/pivot/onboarding";
import { PivotFrame, PrimaryButton } from "./pivot-ui";
import styles from "./onboarding.module.css";

export function QuestionProgress({ step, onBack, busy }: { step: 1 | 2 | 3; onBack: () => void; busy: boolean }) {
  return <nav className={styles.navigation} aria-label="온보딩 화면 탐색">
    <button type="button" className={styles.back} aria-label="이전 단계" onClick={onBack} disabled={busy}>
      <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true"><path d="m15 5-7 7 7 7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
    </button>
    <div className={styles.progress} role="progressbar" aria-label="기본 질문 진행 상태" aria-valuemin={0} aria-valuemax={3} aria-valuenow={step}>
      <i style={{ width: `${(step / 3) * 100}%` }} />
    </div><span aria-hidden="true">{step}/3</span>
  </nav>;
}
/** Another flow's words for a question (My Page taste edit): its heading, description and a box above them. */
export type QuestionCopy = { heading?: string; description?: ReactNode; hint?: ReactNode };
type Props = { state: OnboardingState; dispatch: Dispatch<OnboardingAction>; catalog: OnboardingCatalog; onBack: () => void; onNext: () => void; busy: boolean; chrome?: boolean; demo: boolean; message?: string; copy?: QuestionCopy };
const errorMessage = (error: unknown) => error instanceof ApiError && error.status === 401 ? "로그인이 만료됐어요. 다시 로그인해 주세요."
  : error instanceof ApiError && error.code === "CSRF_INVALID" ? "로그인 확인 정보가 바뀌었어요. 다시 시도해 주세요."
  : "목록을 불러오지 못했어요. 연결을 확인하고 다시 시도해 주세요.";

export function WorksQuestion({ state, dispatch, catalog, onBack, onNext, busy, chrome = false, demo, message, copy }: Props) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<OnboardingWork[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [topicError, setTopicError] = useState("");
  const [topicReady, setTopicReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [topicAttempt, setTopicAttempt] = useState(0);
  const searchInput = useRef<HTMLInputElement>(null);
  const request = useRef(0);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    let live = true; setTopicReady(false); setTopicError("");
    catalog.resolveTopic().then(topicId => { if (live) { dispatch({ type: "set-topic", topicId }); setTopicReady(true); } })
      .catch(reason => { if (live) setTopicError(errorMessage(reason)); });
    return () => { live = false; };
  }, [catalog, dispatch, topicAttempt]);
  useEffect(() => {
    const token = ++request.current;
    controller.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    setError(""); setItems([]); setCursor(null);
    if (!topicReady || state.topicId === null || (!query.trim() && !demo)) { setLoading(false); return () => abort.abort(); }
    setLoading(true);
    const timer = setTimeout(() => {
      catalog.search(state.topicId!, query, undefined, abort.signal).then(page => {
        if (request.current !== token || abort.signal.aborted) return;
        setItems(page.items); setCursor(page.nextCursor); setLoading(false);
      }).catch(reason => { if (request.current === token && !abort.signal.aborted) { setError(errorMessage(reason)); setLoading(false); } });
    }, demo ? 0 : 250);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [catalog, state.topicId, topicReady, query, attempt, demo]);
  async function more() {
    if (!cursor || loading || !state.topicId) return;
    const token = ++request.current;
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    setLoading(true); setError("");
    try {
      const page = await catalog.search(state.topicId, query, cursor, abort.signal);
      if (request.current !== token || abort.signal.aborted) return;
      setItems(previous => [...previous, ...page.items.filter(work => !previous.some(old => old.entityId === work.entityId))]);
      setCursor(page.nextCursor);
    } catch (reason) { if (request.current === token && !abort.signal.aborted) setError(errorMessage(reason)); }
    finally { if (request.current === token && !abort.signal.aborted) setLoading(false); }
  }
  useEffect(() => () => { ++request.current; controller.current?.abort(); }, []);
  function toggle(work: OnboardingWork) { dispatch({ type: "toggle-work", work }); }
  function search(value: string) { ++request.current; controller.current?.abort(); setQuery(value); }
  return <PivotFrame decorated={false} chrome={chrome} className={styles.frame}>
    <QuestionProgress step={1} onBack={onBack} busy={busy} />
    {copy?.hint}
    <section className={styles.questionBody} aria-labelledby="question-heading">
      <h1 id="question-heading" tabIndex={-1}><span>Q1.</span>{copy?.heading ?? "요즘 가장 관심 있는 작품은 무엇인가요?"}</h1>
      <p className={styles.questionDescription}>{copy?.description ?? "좋아하는 작품을 1개 이상 5개 이하 선택해 주세요"}</p>
      {message && <p className={styles.catalogError} role="alert">{message}</p>}
      {demo && <p className={styles.catalogNotice}>미리보기용 가상 작품이에요.</p>}
      <label className={styles.srOnly} htmlFor="onboarding-work-search">작품 검색</label>
      <div className={styles.searchBox}>
        <svg className={styles.searchIcon} viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="m16 16 4 4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
        <input ref={searchInput} id="onboarding-work-search" className={styles.workSearch} type="search" value={query} maxLength={200} placeholder={demo ? "별빛, 구름으로 검색해보세요" : "작품명을 검색해보세요"}
          onChange={event => search(event.target.value)} disabled={busy || !topicReady} autoComplete="off" />
        {query && <button type="button" className={styles.searchClear} aria-label="검색어 지우기" onClick={() => { search(""); searchInput.current?.focus(); }} disabled={busy}>
          <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><circle cx="10" cy="10" r="9" fill="currentColor" /><path d="m7 7 6 6m0-6-6 6" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" /></svg>
        </button>}
      </div>
      <span className={styles.srOnly} aria-live="polite">{`선택한 작품 ${state.favoriteWorks.length}/5`}</span>
      <ul className={styles.selectedWorks} aria-label="선택한 작품">
        {state.favoriteWorks.map(work => <li key={work.entityId}><span>{work.name}</span><button type="button" aria-label={`${work.name} 선택 해제`} onClick={() => toggle(work)} disabled={busy}>
          <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="m2.5 2.5 7 7m0-7-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
        </button></li>)}
      </ul>
      {state.favoriteWorks.length === 5 && <p className={styles.catalogNotice}>5개를 모두 골랐어요.</p>}
      {topicError ? <div role="alert" className={styles.catalogError}>{topicError}<button type="button" onClick={() => setTopicAttempt(value => value + 1)}>다시 시도</button></div>
        : !topicReady ? <p className={styles.catalogNotice} role="status">작품 검색을 준비하고 있어요…</p>
        : <>
          {error && <div role="alert" className={styles.catalogError}>{error}<button type="button" onClick={() => { if (cursor && items.length) void more(); else setAttempt(value => value + 1); }}>다시 시도</button></div>}
          {items.length > 0 && <p className={styles.resultsLabel} aria-hidden="true">검색 결과</p>}
          <ul className={styles.workResults} aria-label="작품 검색 결과" aria-busy={loading}>
            {items.map(work => {
              const selected = state.favoriteWorks.some(item => item.entityId === work.entityId);
              return <li key={work.entityId}><button type="button" className={`${styles.workChoice} ${selected ? styles.selected : ""}`} aria-pressed={selected}
                disabled={busy || !work.selectable || (!selected && state.favoriteWorks.length >= 5)} onClick={() => toggle(work)}>
                <span className={styles.workName}>{work.name}{!work.selectable && <small>장르·소재 준비 중</small>}</span>
                <span className={`${styles.workCheck} ${selected ? styles.workChecked : ""}`} aria-hidden="true">
                  {selected && <svg viewBox="0 0 12 12" width="10" height="10"><path d="m2.5 6.2 2.3 2.3 4.7-4.9" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>}
                </span>
              </button></li>;
            })}
          </ul>
          {loading && <p className={styles.catalogNotice} role="status">작품을 찾고 있어요…</p>}
          {!loading && !error && (query.trim() || demo) && items.length === 0 && <p className={styles.catalogNotice} role="status">검색 결과가 없어요. 다른 제목이나 별칭으로 찾아보세요.</p>}
          {cursor && !error && <button className={styles.moreWorks} type="button" disabled={busy || loading} onClick={() => void more()}>작품 더 보기</button>}
        </>}
    </section>
    {/* The design shows just "다음"; the spoken name stays the same so existing checks still find it. */}
    <div className={styles.actions}><PrimaryButton aria-label="선택한 작품으로 다음" disabled={busy || !topicReady || !canAdvance(state)} onClick={onNext}>다음</PrimaryButton></div>
  </PivotFrame>;
}

/** Spreads chips over `lines` lines of about the same width and keeps the catalogue order within each line, so the
 * 16 genres fit in three lines. Widths are estimated in px from the labels (14px text, 8px padding, 6px gap). */
export function balancedRows<T extends { label: string }>(tags: T[], lines: number): T[][] {
  type Item = { tag: T; at: number; width: number };
  const estimate = (label: string) => [...label].reduce((sum, char) => sum + (/[가-힣ㄱ-ㅣ]/.test(char) ? 11.5 : 8.4), 24);
  const rows = Array.from({ length: lines }, () => ({ width: 0, items: [] as Item[] }));
  // Widest first onto the narrowest line ...
  tags.map((tag, at) => ({ tag, at, width: estimate(tag.label) })).sort((a, b) => b.width - a.width || a.at - b.at)
    .forEach(item => { const row = rows.reduce((best, next) => next.width < best.width ? next : best); row.width += item.width; row.items.push(item); });
  // ... then move or swap chips out of the widest line while that narrows it (equal widths can pile up on one line).
  for (let step = 0; step < 100; step++) {
    const widest = rows.reduce((best, next) => next.width > best.width ? next : best);
    let trade: { row: typeof widest; out: Item; back?: Item; change: number; after: number } | null = null;
    for (const row of rows) {
      if (row === widest) continue;
      for (const out of widest.items) for (const back of [undefined, ...row.items]) {
        const change = out.width - (back?.width ?? 0);
        const after = Math.max(widest.width - change, row.width + change);
        if (change > 0 && after < widest.width - 0.01 && (!trade || after < trade.after)) trade = { row, out, back, change, after };
      }
    }
    if (!trade) break;
    widest.items.splice(widest.items.indexOf(trade.out), 1); trade.row.items.push(trade.out);
    if (trade.back) { trade.row.items.splice(trade.row.items.indexOf(trade.back), 1); widest.items.push(trade.back); }
    widest.width -= trade.change; trade.row.width += trade.change;
  }
  return rows.filter(row => row.items.length).map(row => row.items.sort((a, b) => a.at - b.at).map(item => item.tag));
}

/** The chips of one kind the chosen works carry, most shared first (catalogue order on ties), for the box
 * "선택한 작품에서 이런 취향을 발견했어요!" above Q2. */
export function discoveredTags<T extends { kind: string; sourceEntityIds: number[] }>(options: T[], kind: string, limit = 3): T[] {
  return options.filter(tag => tag.kind === kind && tag.sourceEntityIds.length > 0)
    .sort((a, b) => b.sourceEntityIds.length - a.sourceEntityIds.length).slice(0, limit);
}

export function TagsQuestion({ state, dispatch, catalog, onBack, onNext, busy, chrome = false, message, copy }: Props) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const ids = state.favoriteWorks.map(work => work.entityId);
  const key = selectionKey(ids);
  useEffect(() => {
    const abort = new AbortController();
    const requestedIds = key.split(",").filter(Boolean).map(Number);
    dispatch({ type: "invalidate-options" }); setLoading(true); setError("");
    if (!state.topicId || !requestedIds.length) { setError("작품을 먼저 선택해 주세요."); setLoading(false); return () => abort.abort(); }
    catalog.options(state.topicId, requestedIds, abort.signal).then(result => {
      if (abort.signal.aborted) return;
      const options = [...result.genres, ...result.themes];
      dispatch({ type: "set-options", topicId: state.topicId!, entityIds: result.entityIds, options }); setLoading(false);
    }).catch(reason => { if (!abort.signal.aborted) { setError(reason instanceof ApiError && reason.status === 422 ? "선택한 작품 정보가 바뀌었어요. 이전 단계에서 작품을 다시 확인해 주세요." : errorMessage(reason)); setLoading(false); } });
    return () => abort.abort();
    // Selection changes within Q2 must not reload candidates or erase choices.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, dispatch, state.topicId, key, attempt]);
  // Onboarding only: My Page taste edit brings its own hint box in this place.
  const found = !copy && !loading && !error
    ? (["genre", "theme"] as const).map(kind => [kind, discoveredTags(state.preferenceOptions, kind)] as const).filter(([, tags]) => tags.length > 0) : [];
  return <PivotFrame decorated={false} chrome={chrome} className={styles.frame}>
    <QuestionProgress step={2} onBack={onBack} busy={busy} />
    {copy?.hint}
    {found.length > 0 && <section className={styles.discovered} aria-labelledby="discovered-heading">
      <h2 id="discovered-heading">선택한 작품에서 이런 취향을 발견했어요!</h2>
      <dl>{found.map(([kind, tags]) => <div key={kind}><dt>{kind === "genre" ? "장르" : "소재"}</dt><dd><ul>{tags.map(tag => <li key={tag.tagId}>{tag.label}</li>)}</ul></dd></div>)}</dl>
    </section>}
    <section className={styles.questionBody} aria-labelledby="question-heading">
      <h1 id="question-heading" tabIndex={-1}><span>Q2.</span>{copy?.heading ?? "어떤 장르와 소재를 좋아하나요?"}</h1>
      <p className={styles.questionDescription}>{copy?.description ?? <>장르·소재 각각 최소 1개, 최대 {MAX_PREFERENCE_TAGS_PER_KIND}개 선택해 주세요<br />위 분석결과를 참고하여 선택해도 좋고,<br />추가로 선택해도 좋아요</>}</p>
      {message && <p className={styles.catalogError} role="alert">{message}</p>}
      {loading ? <p className={styles.catalogNotice} role="status">장르·소재를 불러오고 있어요…</p> : error ? <div className={styles.catalogError} role="alert">{error}<button type="button" onClick={() => setAttempt(value => value + 1)}>다시 시도</button></div> : <>
        {(["genre", "theme"] as const).map(kind => {
          const tags = state.preferenceOptions.filter(tag => tag.kind === kind);
          // Genres spread over three lines of about the same width (design); themes wrap in catalogue order.
          const rows = kind === "genre" ? balancedRows(tags, 3) : [tags];
          return <fieldset key={kind} className={styles.tagGroup}>
            <legend>{kind === "genre" ? "장르" : "소재"}</legend>
            <div className={styles.tagRows}>{rows.map((row, line) => <div key={line} className={styles.tagChips}>{row.map(tag => <label key={tag.tagId} className={`${styles.tagChip} ${state.preferenceTagIds.includes(tag.tagId) ? styles.selected : ""}`}>
              <input type="checkbox" checked={state.preferenceTagIds.includes(tag.tagId)} onChange={() => dispatch({ type: "toggle-tag", tagId: tag.tagId })} disabled={busy || (!state.preferenceTagIds.includes(tag.tagId) && selectedPreferenceCount(state, kind) >= MAX_PREFERENCE_TAGS_PER_KIND)} />
              <span>{tag.label}</span>
            </label>)}</div>)}</div>
            {!tags.length && <p className={styles.catalogNotice}>{kind === "genre" ? "장르" : "소재"} 목록을 준비 중이에요. 지금은 다음 단계로 진행할 수 없어요.</p>}
          </fieldset>;
        })}
      </>}
    </section>
    {/* The design shows just "다음"; the spoken name stays the same so existing checks still find it. */}
    <div className={styles.actions}><PrimaryButton aria-label="장르·소재 선택 완료" disabled={busy || loading || !!error || !canAdvance(state)} onClick={onNext}>다음</PrimaryButton></div>
  </PivotFrame>;
}
