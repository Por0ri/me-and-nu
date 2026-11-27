"use client";

import { useCallback, useState } from "react";
import { WorkSearchField } from "@/components/pivot/home-screen";
import homeStyles from "@/components/pivot/home.module.css";
import { getVoteTickets, listDebates } from "@/lib/debate/client";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateSummary, VoteTickets } from "@/lib/debate/types";
import { LiveCard, PastCard, VoteCard, useRoomActions, useVoteActions } from "./debate-cards";
import { DebateHomeHeader, DebateScreen, DebateState, DebateTabs, useLoad, useToast, type ShowToast } from "./debate-chrome";
import { ActivityFab, DebateGuide, VoteTicketBox, useNow } from "./debate-parts";
import { DebateSearchResults, useDebateSearch } from "./debate-search";
import styles from "./debate.module.css";

export type DebateHomeTab = "live" | "vote" | "past";
const TABS = [
  { id: "live", label: "실시간 토론방" },
  { id: "vote", label: "주제 투표" },
  { id: "past", label: "지난 토론방" },
] as const;

export function DebateHome({ initialTab }: { initialTab: DebateHomeTab }) {
  const [tab, setTab] = useState<DebateHomeTab>(initialTab);
  const [guideOpen, setGuideOpen] = useState(false);
  const [toast, showToast] = useToast();
  // 탭마다 검색이 따로다 (2026-11-23 사용자 결정): 한 탭에서 찾은 검색어·결과·최근 검색이 다른 탭으로 넘어가지 않는다.
  const searches = { live: useDebateSearch("live"), vote: useDebateSearch("vote"), past: useDebateSearch("past") };
  const search = searches[tab];
  const select = (next: DebateHomeTab) => {
    search.leave();
    setTab(next);
    // 주소의 탭만 바꾸고 화면은 다시 불러오지 않는다.
    window.history.replaceState(null, "", next === "live" ? "/pivot/debate" : `/pivot/debate?tab=${next}`);
    window.scrollTo({ top: 0 });
  };
  const closeGuide = useCallback(() => setGuideOpen(false), []);
  return <DebateScreen nav label="토론방" searchOpen={search.open}>
    <DebateHomeHeader onGuide={() => setGuideOpen(true)} />
    <DebateTabs tabs={TABS} value={tab} onChange={select} label="토론방 메뉴" />
    {/* 홈과 같은 검색창 (2026-11-23 시안): 누르면 뒤가 흐려지고 최근 검색·관련 작품이 뜬다 */}
    <div className={styles.searchArea} data-open={search.open || undefined}>
      <WorkSearchField value={search.query} onChange={search.setQuery} onSubmit={search.submit} search={search.view} active={search.open} />
    </div>
    {search.open ? <button type="button" className={homeStyles.searchDim} aria-label="검색 닫기" tabIndex={-1} onClick={search.view.onBack} /> : null}
    {search.found ? <DebateSearchResults search={search} tab={tab} notice={<PastNotice />} showToast={showToast} />
      : tab === "live" ? <LiveTab onShowVotes={() => select("vote")} showToast={showToast} />
      : tab === "vote" ? <VoteTab showToast={showToast} />
      : <PastTab />}
    {/* 내 활동 바로가기: 세 탭 모두 아래에 떠 있다 (2026-11-25 시안). 검색창을 열어 뒤가 흐려진 동안은 숨긴다 */}
    {search.open ? null : <ActivityFab />}
    {guideOpen ? <DebateGuide onClose={closeGuide} /> : null}
    {toast}
  </DebateScreen>;
}

/* ───────────── 실시간 토론방 탭 ───────────── */

const loadLive = () => listDebates({ status: "open" });

/** 지금 열려 있는 토론방. 곧 열릴 주제는 주제 투표 탭에서 본다. 규칙 설명은 머리 부분 ⓘ 안내 창으로 옮겼다.
 *  참여한 방은 '방 나가기'·'다시 입장하기', 아니면 '토론 참여하기' (2026-11-23 시안). */
function LiveTab({ onShowVotes, showToast }: { onShowVotes: () => void; showToast: ShowToast }) {
  const [state, retry, update] = useLoad<DebateSummary[]>([], loadLive);
  const room = useRoomActions((next) => update((rows) => rows.map((row) => row.debateId === next.debateId ? next : row)), showToast);
  const now = useNow();
  const open = state.data;
  return <div className={styles.stack}>
    <DebateState loading={state.loading && open.length === 0} error={state.error} onRetry={retry} />
    {!state.loading && !state.error && open.length === 0 ? <div className={styles.state}>
      <p>지금 열려 있는 토론방이 없어요.<br />주제 투표에서 {DEBATE_RULES.voteThreshold}표를 모아 열어보세요.</p>
      <button type="button" className={styles.softButton} onClick={onShowVotes}>주제 투표 보러가기</button>
    </div> : null}
    {open.map((debate) => <LiveCard key={debate.debateId} debate={debate} now={now} busy={room.pending === debate.debateId}
      onEnter={room.enter} onLeave={room.askLeave} />)}
    {room.dialog}
  </div>;
}

/* ───────────── 주제 투표 탭 ───────────── */

type VoteData = { tickets: VoteTickets | null; debates: DebateSummary[] };

// 최신순 하나만 보여준다 (2026-11-23 시안에서 정렬을 뺐다. 서버의 sort=votes는 그대로 둔다).
const loadVotes = async (): Promise<VoteData> => {
  const [tickets, debates] = await Promise.all([getVoteTickets(), listDebates({ status: "voting" })]);
  return { tickets, debates };
};

function VoteTab({ showToast }: { showToast: ShowToast }) {
  const [state, retry, update] = useLoad<VoteData>({ tickets: null, debates: [] }, loadVotes);
  // 투표한 주제는 진한 '✓ 투표 완료'로 바뀌고 다시 누르면 취소된다. 10표가 되어 열린 주제는 목록에서 빠진다.
  const vote = useVoteActions(({ debate, tickets, opened }) => update((data) => ({
    tickets,
    debates: opened ? data.debates.filter((row) => row.debateId !== debate.debateId)
      : data.debates.map((row) => row.debateId === debate.debateId ? debate : row),
  })), showToast);
  const now = useNow();
  const { tickets, debates } = state.data;
  return <div className={styles.stack}>
    {tickets ? <VoteTicketBox tickets={tickets} /> : null}
    <DebateState loading={state.loading && debates.length === 0} error={state.error} onRetry={retry} />
    {!state.loading && !state.error && debates.length === 0 ? <p className={styles.state}>투표 중인 주제가 없어요. 첫 주제를 올려보세요.</p> : null}
    {debates.map((debate) => <VoteCard key={debate.debateId} debate={debate} now={now} busy={vote.pending !== null} onToggle={vote.toggle} />)}
    {vote.dialog}
  </div>;
}

/* ───────────── 지난 토론방 탭 (2026-11-23 시안) ───────────── */

// 지난 토론방: 48시간이 끝난 방만, 끝난 뒤 9일 동안 (기준 표를 못 받은 주제는 바로 삭제돼 여기 없다)
const loadPast = () => listDebates({ status: "ended" });

/** 지난 토론방 위 안내 (시안 문구, 보관 날 수는 규칙 값을 쓴다) */
export function PastNotice() {
  return <section className={styles.banner} aria-label="지난 토론방 안내">
    <h2>토론이 끝난 채팅방을 다시 볼 수 있어요</h2>
    <p>최근 {DEBATE_RULES.resultRetentionDays}일 동안의 채택된 채팅방 목록이에요<br />채팅 내용은 볼 수 없고 투표 결과만 확인할 수 있어요</p>
  </section>;
}

function PastTab() {
  const [state, retry] = useLoad<DebateSummary[]>([], loadPast);
  return <div className={styles.stack}>
    <PastNotice />
    <DebateState loading={state.loading && state.data.length === 0} error={state.error} onRetry={retry} />
    {!state.loading && !state.error && state.data.length === 0 ? <p className={styles.state}>최근 {DEBATE_RULES.resultRetentionDays}일 동안 끝난 토론방이 없어요.</p> : null}
    {state.data.map((debate) => <PastCard key={debate.debateId} debate={debate} />)}
  </div>;
}
