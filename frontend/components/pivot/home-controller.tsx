"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createHomeRequestController, type HomeCardModel, type HomeDataSource, type HomeDetailHandler, type HomeState, type HomeWorkSuggestion } from "@/lib/pivot/home";
import type { BookmarkControls } from "@/lib/pivot/bookmark-service";
import type { RecentSearch } from "@/lib/pivot/recent-search";
import { PivotHomeScreen, type HomeSearchView, type SearchNotice } from "./home-screen";
import { scrollHomeTo } from "./home-scroll";
import { FeedLoadingScreen } from "./feed-loading-screen";
import styles from "./home.module.css";

const loading: HomeState = { status: "loading" };
// Same pause as onboarding Q1 before asking for works while the user types.
const SUGGEST_DELAY_MS = 250;

export function HomeController({ source, onRestart, onViewDetail, onReady, restoreQuery, onMy, onSaved, onNotifications, bookmarks, skipEntryLoading = false }: {
  source: HomeDataSource; onRestart?: () => void; onViewDetail?: HomeDetailHandler; onReady?: () => void; restoreQuery?: string;
  onMy?: () => void;
  onSaved?: () => void; onNotifications?: () => void; bookmarks?: BookmarkControls;
  skipEntryLoading?: boolean;
}) {
  const [request, setRequest] = useState<{ source: HomeDataSource; state: HomeState }>({ source, state: loading });
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  // Search: focusing the box opens a panel over the dimmed screen (recent searches, or works matching what is
  // typed). Picking a work or pressing Enter shows only the found cards instead of the feed, until closed.
  const [open, setOpen] = useState(false);
  const [found, setFound] = useState<{ source: HomeDataSource; label: string; cards: HomeCardModel[] } | null>(null);
  const [suggested, setSuggested] = useState<{ source: HomeDataSource; query: string; works: HomeWorkSuggestion[] } | null>(null);
  const [recent, setRecent] = useState<{ source: HomeDataSource; entries: RecentSearch[] } | null>(null);
  const [searchNotice, setSearchNotice] = useState<SearchNotice | null>(null);
  const searching = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = createHomeRequestController(source, (state) => setRequest({ source, state }));
    void controller.load();
    return () => controller.cancel();
  }, [source, attempt]);
  useEffect(() => () => searching.current?.abort(), []);
  // Recent searches live in this browser only, so read them after mounting.
  useEffect(() => { setRecent({ source, entries: source.recent?.list() ?? [] }); }, [source]);
  // Never flash the previous source's data before the new effect starts.
  const state = request.source === source ? request.state : loading;
  const [imageReady, setImageReady] = useState<HomeState | null>(null);
  const firstImageReady = useCallback(() => setImageReady(state), [state]);
  const needsImage = state.status === "ready" && !!state.home.cards[0]?.slides.length;
  const preparing = source.kind === "service" && !skipEntryLoading &&
    (state.status === "loading" || (needsImage && imageReady !== state));
  useEffect(() => { if (restoreQuery !== undefined) setQuery(restoreQuery); }, [restoreQuery]);
  useEffect(() => { if (!preparing && (state.status === "ready" || state.status === "empty")) onReady?.(); }, [state.status, preparing, onReady]);

  const typed = query.trim();
  useEffect(() => {
    if (!open || !typed || !source.suggest) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      source.suggest!(typed, { signal: controller.signal })
        .then((works) => { if (!controller.signal.aborted) setSuggested({ source, query: typed, works }); })
        .catch(() => { /* suggestions only help typing; Enter still searches */ });
    }, SUGGEST_DELAY_MS);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [open, typed, source]);

  async function run(entry: RecentSearch) {
    const label = entry.label.trim();
    if (!label || !source.search) return;
    searching.current?.abort();
    const controller = new AbortController();
    searching.current = controller;
    if (source.recent) setRecent({ source, entries: source.recent.add({ ...entry, label }) });
    setOpen(false);
    setSearchNotice(null);
    try {
      const { cards } = await source.search(label, { signal: controller.signal, workId: entry.workId });
      if (controller.signal.aborted) return;
      setFound({ source, label, cards });
      // Found or not, the result has its own title row with "검색 결과 닫기", so the box starts empty again.
      setQuery("");
      scrollHomeTo(0);
    } catch {
      if (!controller.signal.aborted) setSearchNotice({ message: "검색하지 못했어요.\n잠시 후 다시 시도해 주세요.", id: Date.now() });
    }
  }
  function closeSearch() {
    searching.current?.abort();
    setOpen(false); setQuery(""); setFound(null); setSearchNotice(null);
  }
  const result = found?.source === source ? found : null;
  // Back from the panel over a shown result returns to that result; anywhere else it leaves search for the feed.
  function back() {
    if (open && result) { setOpen(false); setQuery(""); return; }
    closeSearch();
  }
  const store = source.recent;
  const search: HomeSearchView | undefined = source.search ? {
    open,
    result,
    suggestions: suggested?.source === source && suggested.query === typed ? suggested.works : [],
    recent: recent?.source === source ? recent.entries : [],
    onOpen: () => setOpen(true),
    onBack: back,
    onClear: () => { setQuery(""); setOpen(true); },
    onClose: closeSearch,
    onPick: (entry) => void run(entry),
    onRemoveRecent: (entry) => { if (store) setRecent({ source, entries: store.remove(entry) }); },
    onClearRecent: () => { if (store) setRecent({ source, entries: store.clear() }); },
  } : undefined;
  return <>
    <div inert={preparing} aria-hidden={preparing || undefined}>
      <PivotHomeScreen state={state} demo={source.kind === "demo"} query={query} onQueryChange={setQuery} preparing={preparing}
        onFirstImageReady={source.kind === "service" && !skipEntryLoading ? firstImageReady : undefined}
        onSearch={source.search ? (value) => void run({ label: value }) : undefined} search={search}
        searchNotice={searchNotice} onSearchNoticeEnd={() => setSearchNotice(null)}
        onRetry={() => setAttempt((value) => value + 1)} onRestart={onRestart} onViewDetail={onViewDetail} onMy={onMy} onSaved={onSaved} onNotifications={onNotifications} bookmarks={bookmarks} />
    </div>
    {preparing ? <div className={styles.entryLoading} data-home-entry-loading=""><FeedLoadingScreen /></div> : null}
  </>;
}
