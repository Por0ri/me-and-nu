"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { deleteDebate, getVoteTickets, listDebates, listMyDebates, listMyVotes } from "@/lib/debate/client";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateSummary, VoteTickets } from "@/lib/debate/types";
import { LiveCard, PastCard, VoteCard, useRoomActions, useVoteActions } from "./debate-cards";
import { DebateHomeHeader, DebateScreen, DebateState, DebateTabs, errorText, useLoad, useToast, type ShowToast } from "./debate-chrome";
import { ConfirmDialog, DebateGuide, VoteTicketBox, useNow } from "./debate-parts";
import styles from "./debate.module.css";

/* 내 활동 (2026-11-23 시안 3장): 내가 투표한 주제 · 내가 올린 주제 · 참여 중인 방. 하단바 없이 머리 부분에 뒤로가기. */

export type DebateActivityTab = "voted" | "mine" | "joined";
const TABS = [
  { id: "voted", label: "내가 투표한 주제" },
  { id: "mine", label: "내가 올린 주제" },
  { id: "joined", label: "참여 중인 방" },
] as const;

export function DebateActivity({ initialTab, created }: { initialTab: DebateActivityTab; created: boolean }) {
  const router = useRouter();
  const [tab, setTab] = useState<DebateActivityTab>(initialTab);
  const [guideOpen, setGuideOpen] = useState(false);
  const [toast, showToast] = useToast();
  useEffect(() => {
    if (!created) return;
    showToast(`주제를 올렸어요. ${DEBATE_RULES.votingDays}일 동안 투표를 받아요.`, "done");
    window.history.replaceState(null, "", "/pivot/debate/activity?tab=mine");
  }, [created, showToast]);
  const select = (next: DebateActivityTab) => {
    setTab(next);
    window.history.replaceState(null, "", `/pivot/debate/activity?tab=${next}`);
    window.scrollTo({ top: 0 });
  };
  const closeGuide = useCallback(() => setGuideOpen(false), []);
  return <DebateScreen label="내 활동">
    <DebateHomeHeader title="내 활동" onGuide={() => setGuideOpen(true)} back={() => router.push("/pivot/debate?tab=vote")} />
    <DebateTabs tabs={TABS} value={tab} onChange={select} label="내 활동 메뉴" spread />
    {tab === "voted" ? <VotedTab showToast={showToast} />
      : tab === "mine" ? <MineTab showToast={showToast} />
      : <JoinedTab showToast={showToast} />}
    {guideOpen ? <DebateGuide onClose={closeGuide} /> : null}
    {toast}
  </DebateScreen>;
}

/** 빈 화면 (시안): 큰 제목, 안내 상자 위로 빼꼼 나온 마스코트, 두 줄 안내와 규칙 점 목록, 큰 버튼.
 *  마스코트는 홈 '검색 결과 없음'과 같은 그림(위쪽만 잘라 둔 1x·2x·3x)을 크게 쓴다. */
function ActivityEmpty({ title, lead, rules, action }: {
  title: ReactNode; lead: ReactNode; rules: ReactNode[]; action: { label: string; href: string };
}) {
  return <section className={styles.emptyState} aria-label="안내">
    <h2>{title}</h2>
    <img className={styles.emptyMascot} src="/pivot/home/search/empty-mascot@3x.png"
      srcSet="/pivot/home/search/empty-mascot@1x.png 115w, /pivot/home/search/empty-mascot@2x.png 230w, /pivot/home/search/empty-mascot@3x.png 345w"
      sizes="168px" alt="" width={168} height={101} draggable={false} />
    <div className={styles.emptyBox}>
      <p className={styles.emptyLead}>{lead}</p>
      <ul className={styles.emptyRules}>{rules.map((rule, index) => <li key={index}>{rule}</li>)}</ul>
    </div>
    <Link className={styles.bigButton} href={action.href}>{action.label}</Link>
  </section>;
}

/* ───────────── 내가 투표한 주제 ───────────── */

type VotedData = { tickets: VoteTickets | null; debates: DebateSummary[] };

const loadVoted = async (): Promise<VotedData> => {
  const [tickets, debates] = await Promise.all([getVoteTickets(), listMyVotes()]);
  // 아직 토론방이 열리지 않은 주제만 (2026-11-23 사용자 결정). 서버(D-15)는 열린 방도 같이 주지만, 열린 방은 '참여 중인 방',
  // 내가 올린 주제는 '내가 올린 주제' 탭에서 본다.
  return { tickets, debates: debates.filter((debate) => debate.status === "voting" && !debate.isMine) };
};

/** 투표권 카드(내 활동 버튼 없이) + 투표했고 아직 열리지 않은 남의 주제. '✓ 투표 완료'를 누르면 취소되고 그 자리에서 '주제 투표하기'로 바뀐다.
 *  여기서 투표해서 10표가 되면 그 자리에서 실시간 토론방 카드로 바뀌고, 다시 들어오면 빠진다. */
function VotedTab({ showToast }: { showToast: ShowToast }) {
  const [state, retry, update] = useLoad<VotedData>({ tickets: null, debates: [] }, loadVoted);
  const replace = (next: DebateSummary) => (rows: DebateSummary[]) => rows.map((row) => row.debateId === next.debateId ? next : row);
  const vote = useVoteActions(({ debate, tickets }) => update((data) => ({ tickets, debates: replace(debate)(data.debates) })), showToast);
  const room = useRoomActions((next) => update((data) => ({ ...data, debates: replace(next)(data.debates) })), showToast);
  const now = useNow();
  const { tickets, debates } = state.data;
  return <div className={styles.stack}>
    {tickets ? <VoteTicketBox tickets={tickets} activity={false} /> : null}
    <DebateState loading={state.loading && debates.length === 0} error={state.error} onRetry={retry} />
    {!state.loading && !state.error && debates.length === 0 ? <ActivityEmpty title="아직 투표한 주제가 없어요!"
      lead={<>마음에 드는 토론 주제에<br />투표하고 토론방을 열어보세요</>}
      rules={[
        <>투표권은 <em>{DEBATE_RULES.ticketRefillMinutes / 60}시간마다 1개</em> 충전돼요</>,
        <>투표권은 <em>최대 {DEBATE_RULES.ticketMax}개</em>까지 가질 수 있어요</>,
        <>주제가 <em>{DEBATE_RULES.voteThreshold}표를 달성</em>하면 채팅방이 열려요</>,
      ]}
      action={{ label: "주제 투표하러 가기", href: "/pivot/debate?tab=vote" }} /> : null}
    {debates.map((debate) => debate.status === "open"
      ? <LiveCard key={debate.debateId} debate={debate} now={now} busy={room.pending === debate.debateId} onEnter={room.enter} onLeave={room.askLeave} />
      : <VoteCard key={debate.debateId} debate={debate} now={now} busy={vote.pending !== null} onToggle={vote.toggle} />)}
    {room.dialog}
    {vote.dialog}
  </div>;
}

/* ───────────── 내가 올린 주제 ───────────── */

const loadMine = () => listMyDebates();

/** 올린 주제가 없으면 빈 화면. 투표 중이면 안내 띠 + 카드(작성자 대신 '투표 중') + '내 주제 삭제하기'(확인 창),
 *  토론방이 열렸으면 '선정됐어요' 띠 + 카드(작성자 대신 '토론 중', 삭제 없음) (2026-11-23 시안).
 *  자기 주제에도 투표할 수 있다 (회의 규칙). */
function MineTab({ showToast }: { showToast: ShowToast }) {
  const [state, retry, update] = useLoad<DebateSummary[]>([], loadMine);
  const replace = (next: DebateSummary) => (rows: DebateSummary[]) => rows.map((row) => row.debateId === next.debateId ? next : row);
  const vote = useVoteActions(({ debate }) => update(replace(debate)), showToast);
  const room = useRoomActions((next) => update(replace(next)), showToast);
  // 삭제 확인 창에 띄운 주제와, 지우는 중인지
  const [deleting, setDeleting] = useState<DebateSummary | null>(null);
  const [removing, setRemoving] = useState(false);
  const keep = useCallback(() => setDeleting(null), []);
  const now = useNow();
  const debates = state.data;
  if (state.loading && debates.length === 0 || state.error) {
    return <div className={styles.stack}><DebateState loading={state.loading} error={state.error} onRetry={retry} /></div>;
  }
  if (debates.length === 0) {
    return <div className={styles.stack}>
      <ActivityEmpty title="아직 올린 주제가 없어요!"
        lead={<>좋아하는 애니의<br />토론 주제를 올리고 투표를 받아보세요</>}
        rules={[
          <>한 번에 <em>1개의 주제만</em> 등록할 수 있어요</>,
          <>주제는 <em>{DEBATE_RULES.votingDays}일 동안</em> 투표를 받아요</>,
          <><em>{DEBATE_RULES.voteThreshold}표를 달성</em>하면 채팅방이 열려요</>,
          <>{DEBATE_RULES.votingDays}일 내 {DEBATE_RULES.voteThreshold}표를 달성하지 못할 경우 주제는 <em>자동 삭제</em>돼요</>,
        ]}
        action={{ label: "주제 올리기", href: "/pivot/debate/new" }} />
    </div>;
  }
  // 투표 중(10표 전)인 주제는 표가 있어도 지울 수 있고(투표한 사람은 투표권을 돌려받는다), 토론방이 열리면 지울 수 없다 (2026-11-23).
  const active = debates.find((debate) => debate.status === "voting") ?? null;
  const opened = debates.find((debate) => debate.status === "open") ?? null;
  const remove = async () => {
    if (!deleting) return;
    const target = deleting;
    setDeleting(null);
    setRemoving(true);
    try {
      await deleteDebate(target.debateId);
      // 새 목록이 오기 전에 지운 카드를 먼저 빼서, 그사이 같은 카드의 삭제 버튼이 다시 눌리지 않게 한다.
      update((rows) => rows.filter((row) => row.debateId !== target.debateId));
      showToast("주제를 삭제했어요.", "done");
      retry();
    } catch (reason) {
      showToast(errorText(reason));
    } finally {
      setRemoving(false);
    }
  };
  return <div className={styles.stack}>
    {active
      ? <section className={styles.banner} aria-label="주제 등록 안내">
        <h2>현재 등록한 주제가 있어요</h2>
        <p>기존 주제가 종료되기 전까지는 새 주제를 올릴 수 없어요</p>
      </section>
      : opened
        ? <section className={styles.banner} aria-label="주제 등록 안내">
          <h2>내가 올린 주제가 선정됐어요</h2>
          <p>선정되어 열린 채팅방은 {DEBATE_RULES.roomHours}시간 동안만 유지돼요</p>
        </section>
        : <section className={styles.banner} aria-label="주제 등록 안내">
          <h2>새 주제를 올릴 수 있어요</h2>
          <p>올린 주제의 토론이 끝났어요. 다음 주제도 올려보세요</p>
        </section>}
    {debates.map((debate) => debate.status === "voting"
      ? <VoteCard key={debate.debateId} debate={debate} now={now} busy={vote.pending !== null} onToggle={vote.toggle} mine />
      : debate.status === "open"
        ? <LiveCard key={debate.debateId} debate={debate} now={now} busy={room.pending === debate.debateId} onEnter={room.enter} onLeave={room.askLeave} mine />
        : <PastCard key={debate.debateId} debate={debate} />)}
    {active
      ? <button type="button" className={styles.dangerText} disabled={removing} onClick={() => setDeleting(active)}>내 주제 삭제하기</button>
      : opened ? null : <Link className={styles.bigButton} href="/pivot/debate/new">주제 올리기</Link>}
    {deleting ? <ConfirmDialog title="내 주제를 삭제할까요?" body={"삭제하면 현재 등록한 주제와\n진행 중인 투표가 함께 삭제돼요."}
      confirmLabel="삭제하기" onConfirm={() => void remove()} onCancel={keep} /> : null}
    {room.dialog}
    {vote.dialog}
  </div>;
}

/* ───────────── 참여 중인 방 ───────────── */

// 참여 중인 방만 따로 주는 API 는 없어서, 열린 방 목록에서 참여한 방만 고른다.
const loadJoined = async () => (await listDebates({ status: "open" })).filter((debate) => debate.joined);

/** 입장한(참여한) 열린 방. 방을 나가면 목록에서 빠진다. */
function JoinedTab({ showToast }: { showToast: ShowToast }) {
  const [state, retry, update] = useLoad<DebateSummary[]>([], loadJoined);
  const room = useRoomActions((next) => update((rows) => next.joined
    ? rows.map((row) => row.debateId === next.debateId ? next : row)
    : rows.filter((row) => row.debateId !== next.debateId)), showToast);
  const now = useNow();
  const rooms = state.data;
  return <div className={styles.stack}>
    <DebateState loading={state.loading && rooms.length === 0} error={state.error} onRetry={retry} />
    {!state.loading && !state.error && rooms.length === 0 ? <ActivityEmpty title={<>참여한 토론방을<br />여기서 확인할 수 있어요!</>}
      lead={<>토론방에 입장해서 다른 사용자들과<br />의견을 주고받아 보세요</>}
      rules={[
        <><em>입장한 채팅방</em>은 나가기 전까지 이곳에 표시돼요</>,
        <>채팅방은 개설 후 <em>{DEBATE_RULES.roomHours}시간 동안</em> 이용할 수 있어요</>,
        <>종료된 채팅방은 <em>지난 채팅방</em>에서 확인할 수 있어요</>,
      ]}
      action={{ label: "참여할 토론방 둘러보기", href: "/pivot/debate" }} /> : null}
    {rooms.map((debate) => <LiveCard key={debate.debateId} debate={debate} now={now} busy={room.pending === debate.debateId}
      onEnter={room.enter} onLeave={room.askLeave} />)}
    {room.dialog}
  </div>;
}
