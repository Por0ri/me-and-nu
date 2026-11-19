"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { cancelVote, getVoteTickets, listDebates, searchDebates, voteDebate } from "@/lib/debate/client";
import { dateText, isClosingSoon, leadingText, resultText, roomRemaining, votingRemaining } from "@/lib/debate/format";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateSearchResult, DebateSummary, DebateWorkOption, VoteTickets } from "@/lib/debate/types";
import { DebateHeader, DebateScreen, DebateState, DebateTabs, errorText, useToast } from "./debate-chrome";
import {
  CalendarIcon, ChatIcon, CheckIcon, ClockIcon, CloseIcon, FlameIcon, InfinityIcon, InfoIcon,
  PlusIcon, SearchIcon, StarIcon, TrophyIcon, UsersIcon,
} from "./debate-icons";
import { Badge, InfoNote, StatusBadge, TicketCard, VoteProgress, WorkChips, useNow } from "./debate-parts";
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
  const [toast, showToast] = useToast();
  const select = (next: DebateHomeTab) => {
    setTab(next);
    // 주소의 탭만 바꾸고 화면은 다시 불러오지 않는다.
    window.history.replaceState(null, "", next === "live" ? "/pivot/debate" : `/pivot/debate?tab=${next}`);
    window.scrollTo({ top: 0 });
  };
  return <DebateScreen nav label="토론방">
    <DebateHeader title="토론방" onSearch={() => setSearchOpen(true)} />
    <DebateTabs tabs={TABS} value={tab} onChange={select} label="토론방 메뉴" />
    {tab === "live" ? <LiveTab onShowVotes={() => select("vote")} />
      : tab === "vote" ? <VoteTab showToast={showToast} />
      : <PastTab />}
    {searchOpen ? <SearchSheet onClose={() => setSearchOpen(false)} /> : null}
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

/* ───────────── 실시간 토론방 탭 ───────────── */

const loadLive = () => listDebates({ status: "open" });

/** 지금 열려 있는 토론방만 보여준다. 곧 열릴 주제는 주제 투표 탭의 '개설 임박순'으로 본다. */
function LiveTab({ onShowVotes }: { onShowVotes: () => void }) {
  const [state, retry] = useLoad<DebateSummary[]>([], loadLive);
  const now = useNow();
  const open = state.data;
  return <div className={styles.stack}>
    <ul className={styles.ruleGrid} aria-label="토론방 규칙">
      <li className={styles.ruleCard}><StarIcon size={18} /><p>10표 달성 시<br />개설</p></li>
      <li className={styles.ruleCard}><ClockIcon size={18} /><p>방 유지<br />{DEBATE_RULES.roomHours}시간</p></li>
      {/* 시안의 '정원 10명' 대신: 정원 없이 시작하고, 진영 선택은 채팅과 별개 (설계 9장) */}
      <li className={styles.ruleCard}><UsersIcon size={18} /><p>진영은<br />언제든 변경</p></li>
      <li className={styles.ruleCard}><InfinityIcon size={18} /><p>방 개수<br />제한 없음</p></li>
    </ul>
    <div className={styles.sectionBar}><ChatIcon size={20} /><h2>지금 열려 있는 토론방</h2></div>
    <DebateState loading={state.loading && open.length === 0} error={state.error} onRetry={retry} />
    {!state.loading && !state.error && open.length === 0 ? <div className={styles.state}>
      <p>지금 열려 있는 토론방이 없어요.<br />주제 투표에서 10표를 모아 열어보세요.</p>
      <button type="button" className={styles.softButton} onClick={onShowVotes}>주제 투표 보러가기</button>
    </div> : null}
    {open.map((debate) => <article key={debate.debateId} className={styles.card}>
      <Badge>실시간 토론방</Badge>
      <h3 className={styles.cardTitle}>{debate.title}</h3>
      <WorkChips works={debate.works} />
      <p className={styles.description}>{debate.sideA.label} vs {debate.sideB.label}{leadingText(debate) ? ` · ${leadingText(debate)}` : ""}</p>
      <div className={styles.metaRow}>
        <span className={styles.meta}><UsersIcon size={15} />진영 투표 {debate.participantCount}명</span>
        <span className={styles.metaDivider} aria-hidden="true" />
        <span className={styles.meta}><ChatIcon size={15} />남은 시간 {roomRemaining(debate.closesAt, now)}</span>
        <span className={styles.spacer} />
        <Link className={styles.softButton} href={`/pivot/debate/${debate.debateId}`}>입장하기</Link>
      </div>
    </article>)}
  </div>;
}

/* ───────────── 주제 투표 탭 ───────────── */

type VoteData = { tickets: VoteTickets | null; debates: DebateSummary[] };
type VoteSort = "recent" | "votes";
const SORTS: { id: VoteSort; label: string }[] = [{ id: "recent", label: "최신순" }, { id: "votes", label: "개설 임박순" }];

function VoteTab({ showToast }: { showToast: (message: string) => void }) {
  // 개설 임박순: 표가 많은 주제부터 (예전 실시간 탭의 '곧 열릴 주제'를 여기서 본다)
  const [sort, setSort] = useState<VoteSort>("recent");
  const loader = useCallback(async (): Promise<VoteData> => {
    const [tickets, debates] = await Promise.all([getVoteTickets(), listDebates({ status: "voting", sort })]);
    return { tickets, debates };
  }, [sort]);
  const [state, retry, update] = useLoad<VoteData>({ tickets: null, debates: [] }, loader);
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
        showToast(result.opened ? "10표 달성! 토론방이 열렸어요. 투표한 사람들에게 알림이 가요." : "투표했어요.");
      }
    } catch (error) {
      showToast(errorText(error));
    } finally {
      setPending(null);
    }
  };
  const { tickets, debates } = state.data;
  return <div className={styles.stack}>
    {tickets ? <TicketCard tickets={tickets} variant="vote" /> : null}
    {/* 시안에는 없지만 주제 올리기·내 활동으로 가는 길이 필요해서 추가 */}
    <div className={styles.buttonRow}>
      <Link className={styles.softButton} style={{ height: 40 }} href="/pivot/debate/new"><PlusIcon size={16} />주제 올리기</Link>
      <Link className={styles.softButton} style={{ height: 40 }} href="/pivot/debate/activity">내 활동</Link>
    </div>
    <div className={styles.infoRow}>
      <span className={styles.infoChip}><CalendarIcon size={16} />후보당 {DEBATE_RULES.votingDays}일 투표</span>
      <span className={styles.infoChip}><FlameIcon size={15} />10표 미달 시 자동 소각</span>
    </div>
    <InfoNote>투표는 토론방 개설에 찬성하는 표예요. 투표권은 최대 {DEBATE_RULES.ticketMax}개이고 1시간마다 1개씩 차요. 10표가 모이는 순간 토론방이 열려요.</InfoNote>
    <div className={styles.filterRow} role="group" aria-label="주제 후보 정렬">
      {SORTS.map((item) => <button key={item.id} type="button" className={styles.filter} aria-pressed={sort === item.id}
        onClick={() => setSort(item.id)}>{item.label}</button>)}
    </div>
    <DebateState loading={state.loading && debates.length === 0} error={state.error} onRetry={retry} />
    {!state.loading && !state.error && debates.length === 0 ? <p className={styles.state}>투표 중인 주제가 없어요. 첫 주제를 올려보세요.</p> : null}
    {debates.map((debate) => <article key={debate.debateId} className={styles.voteCard}>
      <div>
        <Badge>주제 후보</Badge>
        <Link className={styles.cardLink} href={`/pivot/debate/${debate.debateId}`}><h3 className={styles.cardTitleSmall}>{debate.title}</h3></Link>
        <WorkChips works={debate.works} />
        <VoteProgress count={debate.voteCount} threshold={debate.voteThreshold} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
        <button type="button" className={styles.softButton} aria-pressed={debate.myVote} disabled={pending === debate.debateId}
          onClick={() => void toggle(debate)}>{debate.myVote ? <>투표함<CheckIcon size={15} /></> : "투표하기"}</button>
        {isClosingSoon(debate.votingEndsAt, now)
          ? <span className={styles.closingSoon}><InfoIcon size={12} />마감 임박</span>
          : <span className={styles.meta} style={{ color: "var(--debate-muted)", fontSize: 12 }}><ClockIcon size={14} />{votingRemaining(debate.votingEndsAt, now)}</span>}
      </div>
    </article>)}
  </div>;
}

/* ───────────── 지난 토론방 탭 ───────────── */

type PastFilter = "all" | "closed" | "expired";
const FILTERS: { id: PastFilter; label: string }[] = [{ id: "all", label: "전체" }, { id: "closed", label: "방 종료" }, { id: "expired", label: "자동 소각" }];

function PastTab() {
  const [filter, setFilter] = useState<PastFilter>("all");
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQuery(text), 250);
    return () => clearTimeout(timer);
  }, [text]);
  const loader = useCallback(() => listDebates({ status: "ended", result: filter, q: query }), [filter, query]);
  const [state, retry] = useLoad<DebateSummary[]>([], loader);
  return <div className={styles.stack}>
    <InfoNote>지난 방은 결과만 볼 수 있어요. 채팅은 방이 끝나면 사라지고, 소각된 주제는 다시 투표할 수 없어요.</InfoNote>
    <div className={styles.filterRow} role="group" aria-label="지난 토론방 거르기">
      {FILTERS.map((item) => <button key={item.id} type="button" className={styles.filter} aria-pressed={filter === item.id}
        onClick={() => setFilter(item.id)}>{item.label}</button>)}
    </div>
    <label className={styles.searchBox}>
      <SearchIcon size={18} /><span className={styles.srOnly}>지난 토론방 검색</span>
      <input value={text} onChange={(event) => setText(event.target.value)} placeholder="제목이나 작품으로 검색하기" />
    </label>
    <DebateState loading={state.loading && state.data.length === 0} error={state.error} onRetry={retry} />
    {!state.loading && !state.error && state.data.length === 0 ? <p className={styles.state}>조건에 맞는 지난 토론방이 없어요.</p> : null}
    {state.data.map((debate) => <PastItem key={debate.debateId} debate={debate} />)}
  </div>;
}

function PastItem({ debate }: { debate: DebateSummary }) {
  const closed = debate.status === "closed";
  return <Link className={styles.pastItem} href={`/pivot/debate/${debate.debateId}`}>
    <div className={styles.pastTop}>
      <h3>{debate.title}</h3>
      {closed ? <Badge tone="soft">방 종료</Badge> : <Badge tone="gray">자동 소각</Badge>}
    </div>
    {closed ? <span className={styles.result}><TrophyIcon size={16} />{resultText(debate)}</span> : null}
    <div className={styles.metaRow}>
      {closed
        ? <span className={styles.meta}><CalendarIcon size={14} />{dateText(debate.closesAt)}</span>
        : <span className={styles.meta}><FlameIcon size={14} />{debate.voteCount}/{debate.voteThreshold}표</span>}
      <span className={styles.metaDivider} aria-hidden="true" />
      {closed
        ? <span className={styles.meta}><UsersIcon size={14} />진영 투표 {debate.participantCount}명</span>
        : <span className={styles.meta}><CalendarIcon size={14} />{dateText(debate.votingEndsAt)}</span>}
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
      <div className={styles.searchHead}>
        <label className={styles.searchBox}>
          <SearchIcon size={18} /><span className={styles.srOnly}>검색어</span>
          <input autoFocus value={text} placeholder="작품이나 캐릭터 이름으로 찾기"
            onChange={(event) => { setText(event.target.value); setPicked(null); }} />
        </label>
        <button type="button" className={styles.iconButton} aria-label="검색 닫기" onClick={onClose}><CloseIcon size={22} /></button>
      </div>
      {!text.trim() ? <p className={styles.fieldHelp}>작품 이름을 치면 작품을 골라 걸러볼 수 있고, 캐릭터 이름은 제목과 선택지에서 찾아요.</p> : null}
      {result.works.length > 0 ? <>
        <p className={styles.resultLabel}>작품</p>
        <div className={styles.chips}>
          {result.works.map((work) => <button key={work.entityId} type="button" className={styles.workPick} aria-pressed={picked?.entityId === work.entityId}
            onClick={() => setPicked(picked?.entityId === work.entityId ? null : work)}>#{work.name}</button>)}
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
