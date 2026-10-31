"use client";

import { useEffect, useRef, useState } from "react";
import { createHomeRequestController, type HomeCardModel, type HomeDataSource, type HomeDetailHandler, type HomeState } from "@/lib/pivot/home";
import type { BookmarkControls } from "@/lib/pivot/bookmark-service";
import { PivotHomeScreen, type SearchNotice } from "./home-screen";

const loading: HomeState = { status: "loading" };

export function HomeController({ source, onRestart, onViewDetail, onReady, restoreQuery, onMy, onSaved, bookmarks }: {
  source: HomeDataSource; onRestart?: () => void; onViewDetail?: HomeDetailHandler; onReady?: () => void; restoreQuery?: string;
  onMy?: () => void;
  onSaved?: () => void; bookmarks?: BookmarkControls;
}) {
  const [request, setRequest] = useState<{ source: HomeDataSource; state: HomeState }>({ source, state: loading });
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  // Enter in the work search puts that work's cards above the feed until the box is cleared.
  const [found, setFound] = useState<{ source: HomeDataSource; cards: HomeCardModel[] } | null>(null);
  const [searchNotice, setSearchNotice] = useState<SearchNotice | null>(null);
  const searching = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = createHomeRequestController(source, (state) => setRequest({ source, state }));
    void controller.load();
    return () => controller.cancel();
  }, [source, attempt]);
  useEffect(() => () => searching.current?.abort(), []);
  // Never flash the previous source's data before the new effect starts.
  const state = request.source === source ? request.state : loading;
  useEffect(() => { if (restoreQuery !== undefined) setQuery(restoreQuery); }, [restoreQuery]);
  useEffect(() => { if (state.status === "ready") onReady?.(); }, [state.status, onReady]);

  async function search(value: string) {
    const q = value.trim();
    searching.current?.abort();
    if (!q || !source.search) { setFound(null); return; }
    const controller = new AbortController();
    searching.current = controller;
    try {
      const { works, cards } = await source.search(q, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setFound({ source, cards });
      // A matched work without cards is named officially; an unknown name keeps what was typed.
      setSearchNotice(cards.length ? null : {
        message: (works.length ? `‘${works[0]}’ 작품 글은 아직 없어요.` : `‘${q}’에 맞는 작품을 찾지 못했어요.`) + "\n추천 피드를 이어서 보여드릴게요.",
        id: Date.now(),
      });
      if (cards.length) window.scrollTo({ top: 0, behavior: "instant" });
    } catch {
      if (!controller.signal.aborted) setSearchNotice({ message: "검색하지 못했어요.\n잠시 후 다시 시도해 주세요.", id: Date.now() });
    }
  }
  function changeQuery(value: string) {
    setQuery(value);
    if (!value.trim()) { searching.current?.abort(); setFound(null); setSearchNotice(null); }
  }
  const results = found?.source === source ? found.cards : [];
  const shown: HomeState = state.status === "ready" && results.length
    ? { ...state, home: { ...state.home, cards: [...results, ...state.home.cards.filter((card) => !results.some((hit) => hit.id === card.id))] } }
    : state;
  return <PivotHomeScreen state={shown} demo={source.kind === "demo"} query={query} onQueryChange={changeQuery}
    onSearch={source.search ? (value) => void search(value) : undefined} searchNotice={searchNotice} onSearchNoticeEnd={() => setSearchNotice(null)}
    onRetry={() => setAttempt((value) => value + 1)} onRestart={onRestart} onViewDetail={onViewDetail} onMy={onMy} onSaved={onSaved} bookmarks={bookmarks} />;
}
