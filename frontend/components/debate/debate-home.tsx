"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { cancelVote, getVoteTickets, joinDebate, leaveDebate, listDebates, searchDebates, voteDebate } from "@/lib/debate/client";
import { dateText, resultText, roomRemaining, votingRemaining } from "@/lib/debate/format";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateSearchResult, DebateSummary, DebateWorkOption, VoteTickets } from "@/lib/debate/types";
import { DebateHomeHeader, DebateScreen, DebateState, DebateTabs, errorText, useToast } from "./debate-chrome";
import { CalendarIcon, CheckIcon, ClockIcon, CloseIcon, SearchIcon, TrophyIcon, UsersIcon } from "./debate-icons";
import { AuthorLine, Badge, ConfirmDialog, DebateGuide, StatusBadge, VoteMini, VoteTicketBox, WorkChips, useNow } from "./debate-parts";
import styles from "./debate.module.css";

export type DebateHomeTab = "live" | "vote" | "past";
const TABS = [
  { id: "live", label: "실시간 토론방" },
  { id: "vote", label: "주제 투표" },
  { id: "past", label: "지난 토론방" },
] as const;

export function DebateHome({ initialTab }: { initialTab: DebateHomeTab }) {
  const [tab, setTab] = useState<DebateHomeTab>(initialTab);
  const [searchOpen, setSearchOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [toast, showToast] = useToast();
  const select = (next: DebateHomeTab) => {
    setTab(next);
    // 주소의 탭만 바꾸고 화면은 다시 불러오지 않는다.
    window.history.replaceState(null, "", next === "live" ? "/pivot/debate" : `/pivot/debate?tab=${next}`);
    window.scrollTo({ top: 0 });
  };
  const closeGuide = useCallback(() => setGuideOpen(false), []);
  return <DebateScreen nav label="토론방">
    <DebateHomeHeader onGuide={() => setGuideOpen(true)} />
    <DebateTabs tabs={TABS} value={tab} onChange={select} label="토론방 메뉴" />
    {/* 홈 검색창과 같은 방식: 누르면 검색 화면(작품 칩 + 토론 목록)이 열린다. */}
    <button type="button" className={styles.searchField} onClick={() => setSearchOpen(true)}>
      <img src="/pivot/home/chrome/search.svg" alt="" width={20} height={20} />
      <span>작품 이름으로 검색해보세요</span>
    </button>
    {tab === "live" ? <LiveTab onShowVotes={() => select("vote")} showToast={showToast} />
      : tab === "vote" ? <VoteTab showToast={showToast} />
      : <PastTab />}
    {searchOpen ? <SearchSheet onClose={() => setSearchOpen(false)} /> : null}
    {guideOpen ? <DebateGuide onClose={closeGuide} /> : null}
    {toast}
  </DebateScreen>;
}

type Loaded<T> = { loading: boolean; error: string | null; data: T };

function useLoad<T>(initial: T, loader: () => Promise<T>): [Loaded<T>, () => void, (update: (data: T) => T) => void] {
  const [state, setState] = useState<Loaded<T>>({ loading: true, error: null, data: initial });
  const load = useCallback(() => {
    let alive = true;
    setState((current) => ({ ...current, loading: true, error: null }));
    loader().then(
      (data) => { if (alive) setState({ loading: false, error: null, data }); },
      (error: unknown) => { if (alive) setState((current) => ({ ...current, loading: false, error: errorText(error) })); },
    );
    return () => { alive = false; };
  }, [loader]);
  useEffect(() => load(), [load]);
  const update = useCallback((change: (data: T) => T) => setState((current) => ({ ...current, data: change(current.data) })), []);
  return [state, () => { load(); }, update];
}

/** 카드 윗부분: 작품 칩, 제목(누르면 상세), 설명(있을 때만, 두 줄까지) */
function TopicHead({ debate }: { debate: DebateSummary }) {
  return <>
    <WorkChips works={debate.works} />
    <Link className={styles.cardLink} href={`/pivot/debate/${debate.debateId}`}><h3 className={styles.topicTitle}>{debate.title}</h3></Link>
    {debate.description ? <p className={styles.topicDescription}>{debate.description}</p> : null}
  </>;
}

/* ───────────── 실시간 토론방 탭 ───────────── */

const loadLive = () => listDebates({ status: "open" });

/** 지금 열려 있는 토론방. 곧 열릴 주제는 주제 투표 탭에서 본다. 규칙 설명은 머리 부분 ⓘ 안내 창으로 옮겼다.
 *  참여한 방은 '방 나가기'·'다시 입장하기', 아니면 '토론 참여하기' (2026-11-23 시안). */
function LiveTab({ onShowVotes, showToast }: { onShowVotes: () => void; showToast: (message: string) => void }) {
  const router = useRouter();
  const [state, retry, update] = useLoad<DebateSummary[]>([], loadLive);
  const [pending, setPending] = useState<number | null>(null);
  const [leaving, setLeaving] = useState<DebateSummary | null>(null);
  const now = useNow();
  const open = state.data;
  const enter = async (debate: DebateSummary) => {
    setPending(debate.debateId);
    try {
      await joinDebate(debate.debateId);
      router.push(`/pivot/debate/${debate.debateId}`);
    } catch (error) {
      showToast(errorText(error));
      setPending(null);
    }
  };
  const leave = async () => {
    if (!leaving) return;
    const target = leaving;
    setLeaving(null);
    setPending(target.debateId);
    try {
      const next = await leaveDebate(target.debateId);
      update((rows) => rows.map((row) => row.debateId === next.debateId ? next : row));
      showToast("토론방에서 나왔어요.");
    } catch (error) {
      showToast(errorText(error));
    } finally {
      setPending(null);
    }
  };
  const cancelLeave = useCallback(() => setLeaving(null), []);
  return <div className={styles.stack}>
    <DebateState loading={state.loading && open.length === 0} error={state.error} onRetry={retry} />
    {!state.loading && !state.error && open.length === 0 ? <div className={styles.state}>
      <p>지금 열려 있는 토론방이 없어요.<br />주제 투표에서 {DEBATE_RULES.voteThreshold}표를 모아 열어보세요.</p>
      <button type="button" className={styles.softButton} onClick={onShowVotes}>주제 투표 보러가기</button>
    </div> : null}
    {open.map((debate) => <article key={debate.debateId} className={styles.topicCard}>
      <TopicHead debate={debate} />
      <div className={styles.topicMeta}>
        <AuthorLine debate={debate} />
        <span className={styles.meta}><UsersIcon size={14} />{debate.memberCount}명 참여 중</span>
        <span className={styles.metaDivider} aria-hidden="true" />
        <span className={styles.meta}><ClockIcon size={14} />남은 시간 {roomRemaining(debate.closesAt, now)}</span>
      </div>
      {debate.joined
        ? <div className={styles.cardButtons}>
          <button type="button" className={styles.softFillButton} disabled={pending === debate.debateId} onClick={() => setLeaving(debate)}>방 나가기</button>
          <Link className={styles.fillButton} href={`/pivot/debate/${debate.debateId}`}>다시 입장하기</Link>
        </div>
        : <button type="button" className={styles.fillButton} disabled={pending === debate.debateId} onClick={() => void enter(debate)}>토론 참여하기</button>}
    </article>)}
    {leaving ? <ConfirmDialog title="토론방을 나갈까요?" body="나가면 지금까지의 채팅이 사라지고, 고른 진영도 취소돼요."
      confirmLabel="나가기" onConfirm={() => void leave()} onCancel={cancelLeave} /> : null}
  </div>;
}

/* ───────────── 주제 투표 탭 ───────────── */

type VoteData = { tickets: VoteTickets | null; debates: DebateSummary[] };

// 최신순 하나만 보여준다 (2026-11-23 시안에서 정렬을 뺐다. 서버의 sort=votes는 그대로 둔다).
const loadVotes = async (): Promise<VoteData> => {
  const [tickets, debates] = await Promise.all([getVoteTickets(), listDebates({ status: "voting" })]);
  return { tickets, debates };
};

function VoteTab({ showToast }: { showToast: (message: string) => void }) {
  const [state, retry, update] = useLoad<VoteData>({ tickets: null, debates: [] }, loadVotes);
  const [pending, setPending] = useState<number | null>(null);
  const now = useNow();
  const toggle = async (debate: DebateSummary) => {
    setPending(debate.debateId);
    try {
      if (debate.myVote) {
        const result = await cancelVote(debate.debateId);
        update((data) => ({ tickets: result.tickets, debates: data.debates.map((row) => row.debateId === debate.debateId ? result.debate : row) }));
        showToast("투표를 취소했어요. 투표권 1개를 돌려받았어요.");
      } else {
        const result = await voteDebate(debate.debateId);
        update((data) => ({
          tickets: result.tickets,
          debates: result.opened ? data.debates.filter((row) => row.debateId !== debate.debateId)
            : data.debates.map((row) => row.debateId === debate.debateId ? result.debate : row),
        }));
        showToast(result.opened ? `${DEBATE_RULES.voteThreshold}표 달성! 토론방이 열렸어요. 투표한 사람들에게 알림이 가요.` : "투표했어요.");
      }
    } catch (error) {
      showToast(errorText(error));
    } finally {
      setPending(null);
    }
  };
  const { tickets, debates } = state.data;
  return <div className={styles.stack}>
    {tickets ? <VoteTicketBox tickets={tickets} /> : null}
    <DebateState loading={state.loading && debates.length === 0} error={state.error} onRetry={retry} />
    {!state.loading && !state.error && debates.length === 0 ? <p className={styles.state}>투표 중인 주제가 없어요. 첫 주제를 올려보세요.</p> : null}
    {debates.map((debate) => <article key={debate.debateId} className={styles.topicCard}>
      <TopicHead debate={debate} />
      <div className={styles.topicMeta}>
        <AuthorLine debate={debate} />
        <VoteMini count={debate.voteCount} threshold={debate.voteThreshold} />
        <span className={styles.meta}><ClockIcon size={14} />{votingRemaining(debate.votingEndsAt, now)}</span>
      </div>
      {/* 투표한 주제는 진한 버튼으로 바뀌고, 다시 누르면 취소된다. */}
      <button type="button" className={debate.myVote ? styles.fillButton : styles.softFillButton} aria-pressed={debate.myVote}
        disabled={pending === debate.debateId} onClick={() => void toggle(debate)}>
        {debate.myVote ? <><CheckIcon size={15} />투표 완료</> : "주제 투표하기"}
      </button>
    </article>)}
  </div>;
}

/* ───────────── 지난 토론방 탭 (시안 전이라 다른 탭과 비슷하게) ───────────── */

// 지난 토론방: 48시간이 끝난 방만, 끝난 뒤 9일 동안 (기준 표를 못 받은 주제는 바로 삭제돼 여기 없다)
const loadPast = () => listDebates({ status: "ended" });

function PastTab() {
  // 검색은 위 공통 검색창으로 한다 (이 탭만의 검색창은 뺐다).
  const [state, retry] = useLoad<DebateSummary[]>([], loadPast);
  return <div className={styles.stack}>
    <p className={styles.fieldHelp}>방이 끝난 뒤 {DEBATE_RULES.resultRetentionDays}일 동안 어느 진영이 이겼는지 볼 수 있어요. 채팅은 남지 않아요.</p>
    <DebateState loading={state.loading && state.data.length === 0} error={state.error} onRetry={retry} />
    {!state.loading && !state.error && state.data.length === 0 ? <p className={styles.state}>조건에 맞는 지난 토론방이 없어요.</p> : null}
    {state.data.map((debate) => <PastItem key={debate.debateId} debate={debate} />)}
  </div>;
}

function PastItem({ debate }: { debate: DebateSummary }) {
  return <Link className={styles.topicCard} href={`/pivot/debate/${debate.debateId}`}>
    <div className={styles.pastTop}>
      <WorkChips works={debate.works} />
      <span className={styles.spacer} />
      <Badge tone="soft">방 종료</Badge>
    </div>
    <h3 className={styles.topicTitle}>{debate.title}</h3>
    {debate.description ? <p className={styles.topicDescription}>{debate.description}</p> : null}
    <span className={styles.result}><TrophyIcon size={16} />{resultText(debate)}</span>
    <div className={styles.topicMeta}>
      <AuthorLine debate={debate} />
      <span className={styles.meta}><UsersIcon size={14} />진영 투표 {debate.participantCount}명</span>
      <span className={styles.metaDivider} aria-hidden="true" />
      <span className={styles.meta}><CalendarIcon size={14} />{dateText(debate.closesAt)}</span>
    </div>
  </Link>;
}

/* ───────────── 검색 (입력창 하나: 작품 칩 + 토론 목록) ───────────── */

function SearchSheet({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState("");
  const [picked, setPicked] = useState<DebateWorkOption | null>(null);
  const [result, setResult] = useState<DebateSearchResult>({ works: [], debates: [] });
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      searchDebates(text).then((next) => { if (alive) setResult(next); }, () => { if (alive) setResult({ works: [], debates: [] }); });
    }, 250);
    return () => { alive = false; clearTimeout(timer); };
  }, [text]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const debates = picked ? result.debates.filter((debate) => debate.works.some((work) => work.entityId === picked.entityId)) : result.debates;
  return <div className={styles.searchSheet} role="dialog" aria-modal="true" aria-label="토론 검색">
    <div className={styles.searchPanel}>
      <form className={styles.searchHead} role="search" onSubmit={(event) => {
        event.preventDefault();
        // 엔터로 검색을 확정했을 때만 검색 기록을 남긴다 (자유 태그가 필요한지 판단용).
        if (text.trim()) void searchDebates(text, { record: true }).catch(() => undefined);
      }}>
        <label className={styles.searchBox}>
          <SearchIcon size={18} /><span className={styles.srOnly}>검색어</span>
          <input autoFocus value={text} placeholder="작품 이름으로 검색해보세요" enterKeyHint="search"
            onChange={(event) => { setText(event.target.value); setPicked(null); }} />
        </label>
        <button type="button" className={styles.iconButton} aria-label="검색 닫기" onClick={onClose}><CloseIcon size={22} /></button>
      </form>
      {!text.trim() ? <p className={styles.fieldHelp}>작품 이름을 치면 작품을 골라 걸러볼 수 있고, 캐릭터 이름은 제목과 선택지에서 찾아요.</p> : null}
      {result.works.length > 0 ? <>
        <p className={styles.resultLabel}>작품</p>
        <div className={styles.chips}>
          {result.works.map((work) => <button key={work.entityId} type="button" className={styles.workPick} aria-pressed={picked?.entityId === work.entityId}
            onClick={() => {
              const next = picked?.entityId === work.entityId ? null : work;
              setPicked(next);
              // 작품 칩을 고른 것도 검색을 확정한 것으로 보고 기록한다.
              if (next) void searchDebates(text, { record: true, pickedEntityId: next.entityId }).catch(() => undefined);
            }}>#{work.name}</button>)}
        </div>
      </> : null}
      {text.trim() ? <p className={styles.resultLabel}>토론 {debates.length}개</p> : null}
      {debates.map((debate) => <Link key={debate.debateId} className={styles.pastItem} href={`/pivot/debate/${debate.debateId}`} onClick={onClose}>
        <div className={styles.pastTop}><h3>{debate.title}</h3><StatusBadge status={debate.status} /></div>
        <p className={styles.description}>{debate.sideA.label} vs {debate.sideB.label}</p>
        <WorkChips works={debate.works} />
      </Link>)}
      {text.trim() && debates.length === 0 ? <p className={styles.state}>찾는 토론이 없어요. 직접 주제를 올려보세요.</p> : null}
    </div>
  </div>;
}
