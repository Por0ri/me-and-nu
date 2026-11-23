"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { SearchEmpty, type HomeSearchView } from "@/components/pivot/home-screen";
import { usePivotSession } from "@/components/pivot/pivot-session";
import { getVoteTickets, listDebates, searchDebates, searchWorks } from "@/lib/debate/client";
import type { DebateListQuery, DebateStatus, DebateSummary, VoteTickets } from "@/lib/debate/types";
import type { HomeWorkSuggestion } from "@/lib/pivot/home";
import { createRecentSearchStore, type RecentSearch } from "@/lib/pivot/recent-search";
import { LiveCard, PastCard, VoteCard, useRoomActions, useVoteActions } from "./debate-cards";
import { DebateState, errorText, type ShowToast } from "./debate-chrome";
import { VoteTicketBox, useNow } from "./debate-parts";
import styles from "./debate.module.css";

/*
 * 토론방 탭 검색 (2026-11-23 시안). 홈 검색과 같은 부품·방식이다(home-screen.tsx WorkSearchField).
 * 검색창을 누르면 뒤가 흐려지고, 빈 칸이면 최근 검색, 입력 중이면 관련 작품 최대 3개가 뜬다.
 * 작품을 고르거나 Enter 를 누르면 그 탭의 토론만 결과로 보여 준다.
 * 탭마다 검색이 따로다 (2026-11-23 사용자 결정): 탭마다 이 훅을 하나씩 써서 검색어·결과·최근 검색이 다른 탭으로 넘어가지 않고,
 * 다른 탭에 갔다 돌아오면 그 탭에서 찾아 둔 결과가 그대로 있다.
 */

export type DebateSearchTab = "live" | "vote" | "past";

/** 탭마다 결과로 보여 줄 토론 상태 */
const TAB_STATUS: Record<DebateSearchTab, DebateStatus> = { live: "open", vote: "voting", past: "closed" };
const SUGGEST_DELAY_MS = 250;
const SUGGEST_MAX = 3;
/** 최근 검색은 이 브라우저에 회원별·탭별로, 홈 검색과도 따로 3개까지 (저장 이름: 앞부분 + 탭 + "." + 회원 번호) */
const RECENT_PREFIX = "pivot.debate-recent-searches.";

type Found = { entry: RecentSearch; debates: DebateSummary[] | null; error: string | null };

export type DebateSearch = {
  query: string;
  setQuery: (value: string) => void;
  /** 검색창을 눌러 패널(최근 검색·관련 작품)과 흐린 배경이 떠 있는지 */
  open: boolean;
  /** WorkSearchField 에 넘기는 값 */
  view: HomeSearchView;
  found: Found | null;
  submit: (value: string) => void;
  /** 결과 카드에서 투표·참여한 뒤 그 자리에서 고친다 */
  update: (change: (rows: DebateSummary[]) => DebateSummary[]) => void;
  /** 다른 탭으로 갈 때: 열린 검색 패널과 입력 중인 글자만 닫는다 (찾아 둔 결과는 이 탭에 남는다) */
  leave: () => void;
};

function listQuery(tab: DebateSearchTab, entityId: number): DebateListQuery {
  if (tab === "live") return { status: "open", entityId };
  if (tab === "vote") return { status: "voting", entityId };
  return { status: "ended", entityId };
}

export function useDebateSearch(tab: DebateSearchTab): DebateSearch {
  const { state } = usePivotSession();
  const session = state.session;
  const userId = session?.authenticated === true && typeof session.user?.id === "number" ? session.user.id : null;
  const owner = useRef(userId);
  owner.current = userId;
  const store = useMemo(() => createRecentSearchStore(() => owner.current, undefined, `${RECENT_PREFIX}${tab}.`), [tab]);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [recent, setRecent] = useState<RecentSearch[]>([]);
  const [suggested, setSuggested] = useState<{ query: string; works: HomeWorkSuggestion[] } | null>(null);
  const [found, setFound] = useState<Found | null>(null);
  // 검색을 확정했을 때(작품 고르기·Enter) 한 번만 검색 기록을 남긴다 (자유 태그가 필요한지 판단용). 결과를 다시 불러올 때는 남기지 않는다.
  const record = useRef(false);

  // 최근 검색은 브라우저 저장소에 있어서 화면이 뜬 뒤에 읽는다.
  useEffect(() => { setRecent(store.list()); }, [store, userId]);

  const typed = query.trim();
  useEffect(() => {
    if (!open || !typed) return;
    let alive = true;
    const timer = setTimeout(() => {
      searchWorks(typed).then(
        (works) => { if (alive) setSuggested({ query: typed, works: works.slice(0, SUGGEST_MAX).map((work) => ({ id: work.entityId, name: work.name })) }); },
        () => { /* 추천은 입력을 돕기만 한다. Enter 검색은 그대로 된다. */ },
      );
    }, SUGGEST_DELAY_MS);
    return () => { alive = false; clearTimeout(timer); };
  }, [open, typed]);

  const entry = found?.entry ?? null;
  useEffect(() => {
    if (!entry) return;
    let alive = true;
    const shouldRecord = record.current;
    record.current = false;
    setFound((current) => current && current.entry === entry ? { ...current, debates: null, error: null } : current);
    let load: Promise<DebateSummary[]>;
    if (entry.workId !== undefined) {
      load = listDebates(listQuery(tab, entry.workId));
      if (shouldRecord) void searchDebates(entry.label, { record: true, pickedEntityId: entry.workId }).catch(() => undefined);
    } else {
      load = searchDebates(entry.label, { record: shouldRecord }).then((result) => result.debates.filter((debate) => debate.status === TAB_STATUS[tab]));
    }
    load.then(
      (debates) => { if (alive) setFound((current) => current && current.entry === entry ? { ...current, debates, error: null } : current); },
      (error: unknown) => { if (alive) setFound((current) => current && current.entry === entry ? { ...current, debates: [], error: errorText(error) } : current); },
    );
    return () => { alive = false; };
  }, [entry, tab]);

  const run = useCallback((next: RecentSearch) => {
    const label = next.label.trim();
    if (!label) return;
    const clean: RecentSearch = next.workId === undefined ? { label } : { label, workId: next.workId };
    setRecent(store.add(clean));
    record.current = true;
    setOpen(false);
    setQuery("");
    setFound({ entry: clean, debates: null, error: null });
    window.scrollTo({ top: 0 });
  }, [store]);
  const close = useCallback(() => { setOpen(false); setQuery(""); setFound(null); }, []);
  const update = useCallback((change: (rows: DebateSummary[]) => DebateSummary[]) => {
    setFound((current) => current?.debates ? { ...current, debates: change(current.debates) } : current);
  }, []);

  const view: HomeSearchView = {
    open,
    result: null,
    suggestions: suggested?.query === typed ? suggested.works : [],
    recent,
    onOpen: () => setOpen(true),
    // 결과 위에 패널을 열었다가 뒤로가기면 결과로 돌아가고, 그 밖에는 검색을 닫는다 (홈과 같음).
    onBack: () => { if (open && found) { setOpen(false); setQuery(""); return; } close(); },
    onClear: () => { setQuery(""); setOpen(true); },
    onClose: close,
    onPick: run,
    onRemoveRecent: (item) => setRecent(store.remove(item)),
    onClearRecent: () => setRecent(store.clear()),
  };
  const leave = useCallback(() => { setOpen(false); setQuery(""); }, []);
  return { query, setQuery, open, view, found, submit: (value) => run({ label: value }), update, leave };
}

/** 검색 결과 (시안): "‘나루토’ 검색 결과 · 검색 결과 닫기". 주제 투표 탭은 투표권 카드, 지난 토론방 탭은 안내 띠(notice)를
 *  카드 위에 둔다. 결과가 없으면 홈과 같은 '검색 결과 없음' 화면. */
export function DebateSearchResults({ search, tab, notice, showToast }: {
  search: DebateSearch; tab: DebateSearchTab; notice?: ReactNode; showToast: ShowToast;
}) {
  const found = search.found;
  const now = useNow();
  const [tickets, setTickets] = useState<VoteTickets | null>(null);
  const replace = (next: DebateSummary) => (rows: DebateSummary[]) => rows.map((row) => row.debateId === next.debateId ? next : row);
  const room = useRoomActions((next) => search.update(replace(next)), showToast);
  const vote = useVoteActions(({ debate, tickets: nextTickets, opened }) => {
    setTickets(nextTickets);
    search.update(opened ? (rows) => rows.filter((row) => row.debateId !== debate.debateId) : replace(debate));
  }, showToast);
  useEffect(() => {
    if (tab !== "vote") return;
    let alive = true;
    getVoteTickets().then((next) => { if (alive) setTickets(next); }, () => undefined);
    return () => { alive = false; };
  }, [tab]);
  if (!found) return null;
  const debates = found.debates;
  return <section className={styles.results} aria-labelledby="debate-search-results">
    <div className={styles.resultsHead}>
      <h2 id="debate-search-results"><strong>‘{found.entry.label}’</strong> 검색 결과</h2>
      <button type="button" onClick={search.view.onClose}>검색 결과 닫기</button>
    </div>
    {debates && debates.length === 0 && !found.error ? <SearchEmpty label={found.entry.label} /> : <div className={styles.stack}>
      {tab === "vote" && tickets ? <VoteTicketBox tickets={tickets} /> : null}
      {tab === "past" ? notice : null}
      <DebateState loading={debates === null} error={found.error} />
      {(debates ?? []).map((debate) => tab === "live"
        ? <LiveCard key={debate.debateId} debate={debate} now={now} busy={room.pending === debate.debateId} onEnter={room.enter} onLeave={room.askLeave} />
        : tab === "vote"
          ? <VoteCard key={debate.debateId} debate={debate} now={now} busy={vote.pending !== null} onToggle={vote.toggle} />
          : <PastCard key={debate.debateId} debate={debate} />)}
    </div>}
    {room.dialog}
    {vote.dialog}
  </section>;
}
