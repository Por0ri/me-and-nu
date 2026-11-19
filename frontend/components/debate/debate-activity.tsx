"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { cancelVote, deleteDebate, getVoteTickets, listMyDebates, listMyVotes } from "@/lib/debate/client";
import { longRemaining, resultText, votingRemaining } from "@/lib/debate/format";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateSummary, VoteTickets } from "@/lib/debate/types";
import { DebateHeader, DebateScreen, DebateState, DebateTabs, errorText, useToast } from "./debate-chrome";
import { ChatPlusIcon, CheckIcon, ClockIcon, InfoIcon, ListIcon, PlusIcon, TrophyIcon, UsersIcon } from "./debate-icons";
import { Badge, InfoNote, StatusBadge, TicketCard, VoteProgress, useNow } from "./debate-parts";
import styles from "./debate.module.css";

export type DebateActivityTab = "voted" | "mine";
const TABS = [{ id: "voted", label: "내가 투표한 주제" }, { id: "mine", label: "내가 올린 주제" }] as const;

export function DebateActivity({ initialTab, created }: { initialTab: DebateActivityTab; created: boolean }) {
  const [tab, setTab] = useState<DebateActivityTab>(initialTab);
  const [toast, showToast] = useToast();
  useEffect(() => {
    if (!created) return;
    showToast(`주제를 올렸어요. ${DEBATE_RULES.votingDays}일 동안 투표를 받아요.`);
    window.history.replaceState(null, "", "/pivot/debate/activity?tab=mine");
  }, [created, showToast]);
  const select = (next: DebateActivityTab) => {
    setTab(next);
    window.history.replaceState(null, "", `/pivot/debate/activity?tab=${next}`);
  };
  return <DebateScreen label="내 활동">
    <DebateHeader title="내 활동" brand={false} back="/pivot/debate?tab=vote" />
    <DebateTabs tabs={TABS} value={tab} onChange={select} label="내 활동 메뉴" />
    {tab === "voted" ? <VotedTab showToast={showToast} /> : <MineTab showToast={showToast} />}
    {toast}
  </DebateScreen>;
}

/* ───────────── 내가 투표한 주제 ───────────── */

function VotedTab({ showToast }: { showToast: (message: string) => void }) {
  const [tickets, setTickets] = useState<VoteTickets | null>(null);
  const [debates, setDebates] = useState<DebateSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<number | null>(null);
  const now = useNow();
  const load = useCallback(() => {
    setError(null);
    Promise.all([getVoteTickets(), listMyVotes()]).then(([nextTickets, rows]) => { setTickets(nextTickets); setDebates(rows); },
      (reason: unknown) => setError(errorText(reason)));
  }, []);
  useEffect(() => { load(); }, [load]);
  const cancel = async (debate: DebateSummary) => {
    setPending(debate.debateId);
    try {
      const before = tickets?.balance ?? 0;
      const result = await cancelVote(debate.debateId);
      setTickets(result.tickets);
      setDebates((rows) => (rows ?? []).filter((row) => row.debateId !== debate.debateId));
      showToast(result.tickets.balance > before ? "투표를 취소했어요. 투표권 1개를 돌려받았어요." : "투표를 취소했어요. 투표권이 이미 가득이라 늘지 않았어요.");
    } catch (reason) {
      showToast(errorText(reason));
    } finally {
      setPending(null);
    }
  };
  return <div className={styles.stack}>
    {tickets ? <TicketCard tickets={tickets} variant="activity" /> : null}
    <DebateState loading={debates === null && !error} error={error} onRetry={load} />
    {debates?.length === 0 ? <div className={styles.state}>
      <p>아직 투표한 주제가 없어요.</p>
      <Link className={styles.softButton} href="/pivot/debate?tab=vote" style={{ marginTop: 12 }}>주제 투표 보러가기</Link>
    </div> : null}
    {(debates ?? []).map((debate) => <article key={debate.debateId} className={styles.card}>
      {debate.works[0] ? <Badge tone="gray">{debate.works[0].name}</Badge> : null}
      <h3 className={styles.cardTitleSmall}>{debate.title}</h3>
      <span className={styles.votedMark}><CheckIcon size={13} />내가 1표 보냄</span>
      <div className={styles.metaRow}>
        <UsersIcon size={18} />
        <div style={{ flex: 1 }}><VoteProgress count={debate.voteCount} threshold={debate.voteThreshold} large /></div>
      </div>
      <div className={styles.metaRow}>
        {debate.status === "voting"
          ? <span className={styles.meta}><ClockIcon size={14} />{votingRemaining(debate.votingEndsAt, now)}</span>
          : <StatusBadge status={debate.status} />}
        <span className={styles.spacer} />
        {debate.status === "voting"
          ? <button type="button" className={styles.softButton} disabled={pending === debate.debateId} onClick={() => void cancel(debate)}>투표 취소</button>
          : debate.status === "open" ? <Link className={styles.softButton} href={`/pivot/debate/${debate.debateId}`}>입장하기</Link> : null}
      </div>
    </article>)}
    {debates && debates.length > 0 ? <InfoNote>투표권이 이미 {DEBATE_RULES.ticketMax}개라면, 투표를 취소해도 추가 반환되지 않아요. 열린 토론방의 표는 취소할 수 없어요.</InfoNote> : null}
  </div>;
}

/* ───────────── 내가 올린 주제 ───────────── */

function MineTab({ showToast }: { showToast: (message: string) => void }) {
  const [debates, setDebates] = useState<DebateSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const now = useNow();
  const load = useCallback(() => {
    setError(null);
    listMyDebates().then(setDebates, (reason: unknown) => setError(errorText(reason)));
  }, []);
  useEffect(() => { load(); }, [load]);
  if (debates === null) return <div className={styles.stack}><DebateState loading={!error} error={error} onRetry={load} /></div>;

  const rules = <ul className={styles.bullets}>
    <li>주제는 {DEBATE_RULES.votingDays}일 동안 투표를 받아요</li>
    <li>{DEBATE_RULES.voteThreshold}표를 달성하면 토론방이 바로 열리고 {DEBATE_RULES.roomHours}시간 유지돼요</li>
    <li>{DEBATE_RULES.votingDays}일 안에 못 채우면 주제는 자동 소각돼요</li>
    <li>정원 없이 누구나 채팅할 수 있고, 진영은 언제든 고르고 바꿀 수 있어요</li>
  </ul>;

  if (debates.length === 0) {
    return <div className={styles.stack}>
      <InfoNote><strong>한 번에 1개의 주제만 등록할 수 있어요</strong>올린 주제가 열리거나 소각되면 새 주제를 등록할 수 있어요.</InfoNote>
      <div className={styles.empty}>
        <span className={styles.emptyIcon}><ChatPlusIcon size={52} /></span>
        <h2>아직 올린 주제가 없어요</h2>
        <p>좋아하는 애니의 논쟁거리를 올리고 투표를 받아보세요.</p>
      </div>
      <Link className={styles.softButton} style={{ height: 52, fontSize: 16 }} href="/pivot/debate/new"><PlusIcon size={20} />주제 올리기</Link>
      {rules}
    </div>;
  }

  const active = debates.find((debate) => debate.status === "voting") ?? null;
  const remove = async (debate: DebateSummary) => {
    if (debate.voteCount > 0) {
      showToast("표를 받은 주제는 지울 수 없어요. 다른 사람의 투표권이 걸려 있어요.");
      return;
    }
    if (!window.confirm("이 주제를 지울까요? 지운 주제는 되돌릴 수 없어요.")) return;
    setDeleting(true);
    try {
      await deleteDebate(debate.debateId);
      showToast("주제를 지웠어요.");
      load();
    } catch (reason) {
      showToast(errorText(reason));
    } finally {
      setDeleting(false);
    }
  };
  return <div className={styles.stack}>
    {active
      ? <InfoNote><strong>현재 투표 중인 주제가 있어요</strong>이 주제가 열리거나 소각되기 전까지는 새 주제를 올릴 수 없어요.</InfoNote>
      : <InfoNote><strong>새 주제를 올릴 수 있어요</strong>올린 주제가 열리거나 소각됐어요.</InfoNote>}
    {debates.map((debate) => <article key={debate.debateId} className={styles.card}>
      <div className={styles.metaRow} style={{ color: "inherit" }}>
        {debate.works[0] ? <Badge tone="gray">{debate.works[0].name}</Badge> : null}
        <span className={styles.spacer} />
        <StatusBadge status={debate.status} />
      </div>
      <h3 className={styles.cardTitleSmall}>{debate.title}</h3>
      <p className={styles.description}>{debate.sideA.label} vs {debate.sideB.label}</p>
      {debate.status === "voting" || debate.status === "expired" ? <VoteProgress count={debate.voteCount} threshold={debate.voteThreshold} large /> : null}
      {debate.status === "voting" ? <span className={styles.meta} style={{ color: "var(--debate-muted)", fontSize: 12.5 }}><ClockIcon size={14} />{votingRemaining(debate.votingEndsAt, now)}</span> : null}
      {debate.status === "open" ? <Link className={styles.softButton} href={`/pivot/debate/${debate.debateId}`}>토론방 입장하기</Link> : null}
      {debate.status === "closed" ? <span className={styles.result}><TrophyIcon size={16} />{resultText(debate)}</span> : null}
    </article>)}
    {active ? <>
      <h2 className={styles.sectionHeading}>현재 상태</h2>
      <ul className={styles.statusList}>
        <li className={styles.statusRow}><UsersIcon size={18} />개설까지<b>{Math.max(0, active.voteThreshold - active.voteCount)}표 남음</b></li>
        <li className={styles.statusRow}><ClockIcon size={18} />자동 소각까지<b>{longRemaining(active.votingEndsAt, now)}</b></li>
        <li className={styles.statusRow}><ListIcon size={18} />받은 표<b>{active.voteCount}표</b></li>
      </ul>
    </> : null}
    {rules}
    {active
      ? <button type="button" className={styles.primaryButton} disabled>새 주제 올리기</button>
      : <Link className={styles.primaryButton} href="/pivot/debate/new" style={{ display: "flex" }}>새 주제 올리기</Link>}
    <p className={styles.centerNote}><InfoIcon size={14} />1인당 투표 중인 주제 1개만 등록 가능</p>
    {active ? <button type="button" className={styles.dangerText} disabled={deleting} onClick={() => void remove(active)}>내 주제 삭제</button> : null}
  </div>;
}
