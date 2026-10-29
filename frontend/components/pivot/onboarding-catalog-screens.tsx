"use client";
import { useEffect, useRef, useState, type Dispatch } from "react";
import { ApiError } from "@/lib/api";
import { type OnboardingCatalog } from "@/lib/pivot/onboarding-catalog";
import { MAX_PREFERENCE_TAGS_PER_KIND, selectedPreferenceCount, canAdvance, selectionKey, workFormatLabel, type OnboardingAction, type OnboardingState, type OnboardingWork } from "@/lib/pivot/onboarding";
import { PivotFrame, PrimaryButton } from "./pivot-ui";
import styles from "./onboarding.module.css";

export function QuestionProgress({ step, onBack, busy }: { step: 1 | 2 | 3; onBack: () => void; busy: boolean }) {
  return <nav className={styles.navigation} aria-label="온보딩 화면 탐색">
    <button type="button" className={styles.back} aria-label="이전 단계" onClick={onBack} disabled={busy}>
      <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true"><path d="m15 5-7 7 7 7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
    </button>
    <div className={styles.progress} role="progressbar" aria-label="기본 질문 진행 상태" aria-valuemin={0} aria-valuemax={3} aria-valuenow={step}>
      {[1, 2, 3].map(index => <i key={index} className={index <= step ? styles.filled : ""} />)}
    </div><span aria-hidden="true">{step}/3</span>
  </nav>;
}
type Props = { state: OnboardingState; dispatch: Dispatch<OnboardingAction>; catalog: OnboardingCatalog; onBack: () => void; onNext: () => void; busy: boolean; chrome?: boolean; demo: boolean; message?: string };
const errorMessage = (error: unknown) => error instanceof ApiError && error.status === 401 ? "로그인이 만료됐어요. 다시 로그인해 주세요."
  : error instanceof ApiError && error.code === "CSRF_INVALID" ? "로그인 확인 정보가 바뀌었어요. 다시 시도해 주세요."
  : "목록을 불러오지 못했어요. 연결을 확인하고 다시 시도해 주세요.";

export function WorksQuestion({ state, dispatch, catalog, onBack, onNext, busy, chrome = false, demo, message }: Props) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<OnboardingWork[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [topicError, setTopicError] = useState("");
  const [topicReady, setTopicReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [topicAttempt, setTopicAttempt] = useState(0);
  const [cleared, setCleared] = useState(false);
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
  function toggle(work: OnboardingWork) { if (state.preferenceTagIds.length) setCleared(true); dispatch({ type: "toggle-work", work }); }
  return <PivotFrame decorated={false} chrome={chrome} className={styles.frame}>
    <QuestionProgress step={1} onBack={onBack} busy={busy} />
    <section className={styles.questionBody} aria-labelledby="question-heading">
      <h1 id="question-heading" tabIndex={-1}><span>Q1.</span>좋아하는 작품을 알려주세요</h1>
      <p className={styles.questionDescription}>최소 1개, 최대 5개를 선택해주세요.</p>
      {message && <p className={styles.catalogError} role="alert">{message}</p>}
      {demo && <p className={styles.catalogNotice}>미리보기용 가상 작품이에요.</p>}
      <label className={styles.searchLabel} htmlFor="onboarding-work-search">작품 검색</label>
      <input id="onboarding-work-search" className={styles.workSearch} type="search" value={query} maxLength={200} placeholder={demo ? "별빛, 구름으로 검색해보세요" : "작품명이나 다른 제목으로 검색"}
        onChange={event => { ++request.current; controller.current?.abort(); setQuery(event.target.value); }} disabled={busy || !topicReady} autoComplete="off" />
      <div className={styles.selectionHeading}><strong>선택한 작품</strong><span aria-live="polite">{state.favoriteWorks.length}/5</span></div>
      <ul className={styles.selectedWorks} aria-label="선택한 작품">
        {state.favoriteWorks.map(work => <li key={work.entityId}><span>{work.name}</span><button type="button" aria-label={`${work.name} 선택 해제`} onClick={() => toggle(work)} disabled={busy}>×</button></li>)}
      </ul>
      {cleared && <p className={styles.catalogNotice} role="status">작품이 바뀌어 장르·소재 선택을 초기화했어요. 다음 단계에서 다시 골라주세요.</p>}
      {state.favoriteWorks.length === 5 && <p className={styles.catalogNotice}>5개를 모두 골랐어요. 다른 작품을 고르려면 선택한 작품을 해제해주세요.</p>}
      {topicError ? <div role="alert" className={styles.catalogError}>{topicError}<button type="button" onClick={() => setTopicAttempt(value => value + 1)}>다시 시도</button></div>
        : !topicReady ? <p className={styles.catalogNotice} role="status">작품 검색을 준비하고 있어요…</p>
        : <>
          {!query.trim() && !demo && <p className={styles.catalogNotice}>작품명을 입력하면 검색 결과가 나와요.</p>}
          {error && <div role="alert" className={styles.catalogError}>{error}<button type="button" onClick={() => { if (cursor && items.length) void more(); else setAttempt(value => value + 1); }}>다시 시도</button></div>}
          <ul className={styles.workResults} aria-label="작품 검색 결과" aria-busy={loading}>
            {items.map(work => {
              const selected = state.favoriteWorks.some(item => item.entityId === work.entityId);
              return <li key={work.entityId}><button type="button" className={`${styles.workChoice} ${selected ? styles.selected : ""}`} aria-pressed={selected}
                disabled={busy || !work.selectable || (!selected && state.favoriteWorks.length >= 5)} onClick={() => toggle(work)}>
                {work.imageUrl ? <img src={work.imageUrl} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span className={styles.workPlaceholder} aria-hidden="true">애니</span>}
                <span><strong>{work.name}</strong><small>{[work.releaseYear, workFormatLabel(work.mediaFormat)].filter(Boolean).join(" · ")}</small>{!work.selectable && <small>장르·소재 준비 중</small>}</span><span className={styles.workCheck} aria-hidden="true">{selected ? "✓" : "+"}</span>
              </button></li>;
            })}
          </ul>
          {loading && <p className={styles.catalogNotice} role="status">작품을 찾고 있어요…</p>}
          {!loading && !error && (query.trim() || demo) && items.length === 0 && <p className={styles.catalogNotice} role="status">검색 결과가 없어요. 다른 제목이나 별칭으로 찾아보세요.</p>}
          {cursor && !error && <button className={styles.moreWorks} type="button" disabled={busy || loading} onClick={() => void more()}>작품 더 보기</button>}
        </>}
    </section>
    <div className={styles.actions}><PrimaryButton disabled={busy || !topicReady || !canAdvance(state)} onClick={onNext}>선택한 작품으로 다음</PrimaryButton></div>
  </PivotFrame>;
}

export function TagsQuestion({ state, dispatch, catalog, onBack, onNext, busy, chrome = false, message }: Props) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [pruned, setPruned] = useState(false);
  const ids = state.favoriteWorks.map(work => work.entityId);
  const key = selectionKey(ids);
  useEffect(() => {
    const abort = new AbortController();
    const requestedIds = key.split(",").filter(Boolean).map(Number);
    dispatch({ type: "invalidate-options" }); setLoading(true); setError(""); setPruned(false);
    if (!state.topicId || !requestedIds.length) { setError("작품을 먼저 선택해 주세요."); setLoading(false); return () => abort.abort(); }
    catalog.options(state.topicId, requestedIds, abort.signal).then(result => {
      if (abort.signal.aborted) return;
      const options = [...result.genres, ...result.themes];
      setPruned(state.preferenceTagIds.some(id => !options.some(tag => tag.tagId === id)));
      dispatch({ type: "set-options", topicId: state.topicId!, entityIds: result.entityIds, options }); setLoading(false);
    }).catch(reason => { if (!abort.signal.aborted) { setError(reason instanceof ApiError && reason.status === 422 ? "선택한 작품 정보가 바뀌었어요. 이전 단계에서 작품을 다시 확인해 주세요." : errorMessage(reason)); setLoading(false); } });
    return () => abort.abort();
    // Selection changes within Q2 must not reload candidates or erase choices.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, dispatch, state.topicId, key, attempt]);
  return <PivotFrame decorated={false} chrome={chrome} className={styles.frame}>
    <QuestionProgress step={2} onBack={onBack} busy={busy} />
    <section className={styles.questionBody} aria-labelledby="question-heading">
      <h1 id="question-heading" tabIndex={-1}><span>Q2.</span>어떤 장르와 소재가 좋으세요?</h1>
      <p className={styles.questionDescription}>애니메이션의 장르와 소재예요.<br />장르와 소재를 각각 1개 이상, 최대 {MAX_PREFERENCE_TAGS_PER_KIND}개씩 골라주세요.</p>
      {message && <p className={styles.catalogError} role="alert">{message}</p>}
      <p className={styles.catalogNotice}>{state.favoriteWorks.map(work => work.name).join(" · ")}</p>
      {loading ? <p className={styles.catalogNotice} role="status">장르·소재를 불러오고 있어요…</p> : error ? <div className={styles.catalogError} role="alert">{error}<button type="button" onClick={() => setAttempt(value => value + 1)}>다시 시도</button></div> : <>
        {pruned && <p className={styles.catalogNotice} role="status">현재 후보에서 빠진 선택을 해제했어요. 다시 확인해주세요.</p>}
        {(["genre", "theme"] as const).map(kind => <fieldset key={kind} className={styles.tagGroup}>
          <legend>{kind === "genre" ? "장르" : "소재"} · 1~{MAX_PREFERENCE_TAGS_PER_KIND}개 선택</legend>
          <div className={styles.tagChips}>{state.preferenceOptions.filter(tag => tag.kind === kind).map(tag => <label key={tag.tagId} className={`${styles.tagChip} ${state.preferenceTagIds.includes(tag.tagId) ? styles.selected : ""}`}>
            <input type="checkbox" checked={state.preferenceTagIds.includes(tag.tagId)} onChange={() => dispatch({ type: "toggle-tag", tagId: tag.tagId })} disabled={busy || (!state.preferenceTagIds.includes(tag.tagId) && selectedPreferenceCount(state, kind) >= MAX_PREFERENCE_TAGS_PER_KIND)} />
            <span>{tag.label}</span>
          </label>)}</div>
          {!state.preferenceOptions.some(tag => tag.kind === kind) && <p className={styles.catalogNotice}>{kind === "genre" ? "장르" : "소재"} 목록을 준비 중이에요. 지금은 다음 단계로 진행할 수 없어요.</p>}
        </fieldset>)}
        <p className={styles.catalogNotice} role="status">장르 {selectedPreferenceCount(state, "genre")}/{MAX_PREFERENCE_TAGS_PER_KIND}개 · 소재 {selectedPreferenceCount(state, "theme")}/{MAX_PREFERENCE_TAGS_PER_KIND}개 선택했어요. 각각 1개 이상 선택해주세요.</p>
      </>}
    </section>
    <div className={styles.actions}><PrimaryButton disabled={busy || loading || !!error || !canAdvance(state)} onClick={onNext}>장르·소재 선택 완료</PrimaryButton></div>
  </PivotFrame>;
}
