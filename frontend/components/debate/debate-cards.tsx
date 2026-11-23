"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { cancelVote, getVoteTickets, joinDebate, leaveDebate, voteDebate } from "@/lib/debate/client";
import { roomRemaining, votingRemaining } from "@/lib/debate/format";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateSummary, VoteTickets } from "@/lib/debate/types";
import { errorText, type ShowToast } from "./debate-chrome";
import { CheckIcon, ClockIcon, CloseIcon, UsersIcon } from "./debate-icons";
import { AuthorLine, ConfirmDialog, VoteMini, WorkChips } from "./debate-parts";
import styles from "./debate.module.css";

/*
 * 토론 카드 세 종류 (2026-11-23 시안). 토론방 탭·내 활동·검색 결과가 같이 쓴다.
 * 실시간(LiveCard) · 주제 투표(VoteCard) · 지난 토론방(PastCard).
 */

/** 카드 윗부분: 작품 칩, 제목, 설명 한 줄(+더보기), 양쪽 선택지.
 *  제목은 눌리지 않는다 (2026-11-23 사용자 결정): 따로 여는 화면 없이 카드의 버튼으로 참여·투표한다.
 *  참여 화면도 없다 (2026-11-24): 개설 알림의 상세에도 이 카드를 보여 준다 (debate-notice.tsx). */
export function TopicHead({ debate }: { debate: DebateSummary }) {
  return <>
    <WorkChips works={debate.works} />
    <h3 className={styles.topicTitle}>{debate.title}</h3>
    {debate.description ? <TopicDescription text={debate.description} /> : null}
    <TopicSides debate={debate} />
  </>;
}

/** 설명 (2026-11-23 사용자 결정): 카드가 세로로 길어지지 않게 한 줄만 보이고, 넘치면 끝에 '더보기'.
 *  누르면 그 자리에서 아래로 이어서 전부 보이고(줄바꿈 그대로), '접기'로 다시 한 줄이 된다. */
function TopicDescription({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const [overflow, setOverflow] = useState(false);
  const line = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const element = line.current;
    if (!element || expanded) return;
    // 화면 폭이 바뀌면 다시 잰다 (넘칠 때만 '더보기')
    const check = () => setOverflow(element.scrollWidth > element.clientWidth + 1);
    check();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(check);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [text, expanded]);
  if (expanded) {
    return <p className={styles.topicDescription} data-expanded="">
      {text} <button type="button" className={styles.moreButton} aria-expanded="true" onClick={() => setExpanded(false)}>접기</button>
    </p>;
  }
  return <p className={styles.topicDescription}>
    <span ref={line} className={styles.descriptionLine}>{text}</span>
    {overflow ? <button type="button" className={styles.moreButton} aria-expanded="false" onClick={() => setExpanded(true)}>더보기</button> : null}
  </p>;
}

/** 양쪽 선택지 (2026-11-23 사용자 결정): 설명 아래에 'A vs B'. 카드만 보고도 무엇을 두고 겨루는지 알 수 있게.
 *  카드가 길어지지 않게 상자 없이 설명과 같은 글씨로 쓴다 (2026-11-24 사용자 결정). */
function TopicSides({ debate }: { debate: DebateSummary }) {
  return <p className={styles.topicSides}>{debate.sideA.label} vs {debate.sideB.label}</p>;
}

/** 실시간 토론방 카드: 참여한 방은 '방 나가기'·'다시 입장하기', 아니면 '토론 참여하기' */
export function LiveCard({ debate, now, busy, onEnter, onLeave, mine = false }: {
  debate: DebateSummary; now: number; busy: boolean; onEnter: (debate: DebateSummary) => void; onLeave: (debate: DebateSummary) => void;
  /** 내가 올린 주제가 토론방으로 열렸을 때: 작성자 대신 오른쪽에 '토론 중' 표시 (2026-11-23 시안) */
  mine?: boolean;
}) {
  return <article className={styles.topicCard}>
    <TopicHead debate={debate} />
    <div className={styles.topicMeta}>
      {mine ? null : <AuthorLine debate={debate} />}
      <span className={styles.meta}><UsersIcon size={16} />{debate.memberCount}명 참여 중</span>
      {mine ? null : <span className={styles.metaDivider} aria-hidden="true" />}
      <span className={styles.meta}><ClockIcon size={16} />{mine ? `${roomRemaining(debate.closesAt, now)} 남음` : `남은 시간 ${roomRemaining(debate.closesAt, now)}`}</span>
      {mine ? <span className={styles.statusPill}>토론 중</span> : null}
    </div>
    {/* 다시 입장하기: '토론 참여하기'와 같은 진한 바탕에 흰 글씨 (2026-11-23 사용자 결정) */}
    {debate.joined
      ? <div className={styles.cardButtons}>
        <button type="button" className={styles.softFillButton} disabled={busy} onClick={() => onLeave(debate)}>방 나가기</button>
        <Link className={styles.fillButton} href={`/pivot/debate/${debate.debateId}`}>다시 입장하기</Link>
      </div>
      : <button type="button" className={styles.fillButton} disabled={busy} onClick={() => onEnter(debate)}>토론 참여하기</button>}
  </article>;
}

/** 주제 투표 카드: 표 수·막대·남은 날. 투표하면 진한 '✓ 투표 완료'로 바뀌고, 다시 누르면 취소된다.
 *  내가 올린 주제(mine)는 작성자 대신 오른쪽에 '투표 중' 표시를 둔다. */
export function VoteCard({ debate, now, busy, onToggle, mine = false }: {
  debate: DebateSummary; now: number; busy: boolean; onToggle: (debate: DebateSummary) => void; mine?: boolean;
}) {
  return <article className={styles.topicCard}>
    <TopicHead debate={debate} />
    <div className={styles.topicMeta} data-mine={mine || undefined}>
      {mine ? null : <AuthorLine debate={debate} />}
      <VoteMini count={debate.voteCount} threshold={debate.voteThreshold} />
      <span className={styles.meta}><ClockIcon size={16} />{votingRemaining(debate.votingEndsAt, now)}</span>
      {mine ? <span className={styles.statusPill}>투표 중</span> : null}
    </div>
    <button type="button" className={debate.myVote ? styles.fillButton : styles.softFillButton} aria-pressed={debate.myVote}
      disabled={busy} onClick={() => onToggle(debate)}>
      {debate.myVote ? <><CheckIcon size={15} />투표 완료</> : "주제 투표하기"}
    </button>
  </article>;
}

/** 지난 토론방 카드: 진영을 고른 사람 수(총 N명 투표)와 '투표 결과 확인하기' */
export function PastCard({ debate }: { debate: DebateSummary }) {
  // 결과는 다른 화면으로 가지 않고 창으로 본다 (2026-11-23 시안). 제목을 눌러도 같은 창.
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  return <article className={styles.topicCard}>
    <WorkChips works={debate.works} />
    <button type="button" className={styles.titleButton} onClick={() => setOpen(true)}><h3 className={styles.topicTitle}>{debate.title}</h3></button>
    {debate.description ? <TopicDescription text={debate.description} /> : null}
    <TopicSides debate={debate} />
    <div className={styles.topicMeta}>
      <AuthorLine debate={debate} />
      <span className={styles.meta}>총 {debate.participantCount.toLocaleString("ko-KR")}명 투표</span>
    </div>
    <button type="button" className={styles.fillButton} aria-haspopup="dialog" onClick={() => setOpen(true)}>투표 결과 확인하기</button>
    {open ? <ResultDialog debate={debate} onClose={close} /> : null}
  </article>;
}

/** 진영 비율 막대 (채팅방·지난 토론방 결과 공용, 2026-11-23 시안): 위에 두 진영 이름과 %, 아래 A 쪽만큼 채운 막대.
 *  mine 이면 그 진영 글씨를 보라로 (내가 고른 쪽). */
export function SideResult({ debate, mine = null }: { debate: DebateSummary; mine?: "a" | "b" | null }) {
  const percentA = debate.sideA.percent ?? 0;
  const percentB = debate.sideB.percent ?? 0;
  return <div className={styles.pollResult}>
    <p className={styles.pollLabels}>
      <span data-mine={mine === "a" || undefined}>{debate.sideA.label}<b>{percentA}%</b></span>
      <span data-mine={mine === "b" || undefined}>{debate.sideB.label}<b>{percentB}%</b></span>
    </p>
    <span className={styles.pollBar} role="progressbar" aria-label={`${debate.sideA.label} 비율`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentA}>
      <span style={{ width: `${percentA}%` }} />
    </span>
  </div>;
}

/** 지난 토론방 결과 창 (시안): 작품 칩·닫기, 제목, 설명 전체, 작성자, 진영 비율 막대, 총 N명 투표. 채팅은 남지 않는다. */
function ResultDialog({ debate, onClose }: { debate: DebateSummary; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const titleId = `debate-result-${debate.debateId}`;
  return <div className={styles.modalBackdrop} onClick={onClose}>
    <section className={styles.resultDialog} role="dialog" aria-modal="true" aria-labelledby={titleId} onClick={(event) => event.stopPropagation()}>
      <div className={styles.roomTop}>
        <WorkChips works={debate.works} />
        <button type="button" className={styles.dialogClose} aria-label="닫기" onClick={onClose} autoFocus><CloseIcon size={22} /></button>
      </div>
      <h2 id={titleId} className={styles.topicTitle}>{debate.title}</h2>
      {debate.description ? <p className={styles.roomDescription}>{debate.description}</p> : null}
      <div className={styles.topicMeta}><AuthorLine debate={debate} /></div>
      <div className={styles.poll}>
        <SideResult debate={debate} />
        <p className={styles.pollFoot}><span>총 {debate.participantCount.toLocaleString("ko-KR")}명 투표</span></p>
      </div>
    </section>
  </div>;
}

/** 토론방 참여·나가기 (실시간 토론방·참여 중인 방·검색 결과 공용). 나가기 전에 한 번 더 묻는다. */
export function useRoomActions(onChange: (next: DebateSummary) => void, showToast: ShowToast): {
  pending: number | null; enter: (debate: DebateSummary) => void; askLeave: (debate: DebateSummary) => void; dialog: ReactNode;
} {
  const router = useRouter();
  const [pending, setPending] = useState<number | null>(null);
  const [leaving, setLeaving] = useState<DebateSummary | null>(null);
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
      onChange(await leaveDebate(target.debateId));
      showToast("토론방에서 나왔어요.", "done");
    } catch (error) {
      showToast(errorText(error));
    } finally {
      setPending(null);
    }
  };
  const cancelLeave = useCallback(() => setLeaving(null), []);
  // 방 나가기 창 문구 (2026-11-23 시안)
  const dialog = leaving ? <ConfirmDialog title="방에서 나가시겠어요?" body={"방을 나가면 참여 중인 방 목록에서 제거돼요.\n이 방의 대화 내역도 함께 삭제돼요."}
    confirmLabel="나가기" onConfirm={() => void leave()} onCancel={cancelLeave} /> : null;
  return { pending, enter: (debate) => void enter(debate), askLeave: setLeaving, dialog };
}

export type VoteChange = { debate: DebateSummary; tickets: VoteTickets; opened: boolean };

/** 주제 투표·취소 (주제 투표 탭·내 활동·검색 결과 공용).
 *  투표권이 이미 가득(최대 3개)일 때 취소하면 돌려받지 못하므로 먼저 확인 창을 띄운다 (2026-11-23 시안).
 *  요청은 한 번에 하나만 보낸다: 여러 카드를 빠르게 누르면 응답 순서가 바뀌어 투표권 숫자가 옛 값으로 덮일 수 있어서,
 *  하나가 끝나기 전에는 다른 투표·취소를 받지 않는다. 화면은 pending 이 있는 동안 투표 버튼을 모두 잠그고, 돌려준 dialog 를 그려야 한다. */
export function useVoteActions(onChange: (change: VoteChange) => void, showToast: ShowToast): {
  pending: number | null; toggle: (debate: DebateSummary) => void; dialog: ReactNode;
} {
  const [pending, setPending] = useState<number | null>(null);
  const [confirming, setConfirming] = useState<{ debate: DebateSummary; before: number } | null>(null);
  // 버튼 잠금은 다시 그린 뒤에야 걸리므로, 같은 순간에 두 번 눌린 것은 이 표시로 막는다.
  const working = useRef(false);
  const run = async (debateId: number, task: () => Promise<void>) => {
    if (working.current) return;
    working.current = true;
    setPending(debateId);
    try {
      await task();
    } catch (error) {
      showToast(errorText(error));
    } finally {
      working.current = false;
      setPending(null);
    }
  };
  const cancel = async (debate: DebateSummary, before: number | null) => {
    const result = await cancelVote(debate.debateId);
    onChange({ ...result, opened: false });
    const refunded = before === null || result.tickets.balance > before;
    showToast(refunded ? "투표를 취소했어요.  투표권 1개가 반환되었어요" : "투표를 취소했어요.", "done");
  };
  const toggle = (debate: DebateSummary) => run(debate.debateId, async () => {
    if (debate.myVote) {
      // 투표권은 시간이 지나면 충전되므로 화면의 숫자 대신 지금 값을 다시 읽어 본다. 못 읽으면 묻지 않고 취소한다.
      const tickets = await getVoteTickets().catch(() => null);
      if (tickets && tickets.balance >= tickets.max) { setConfirming({ debate, before: tickets.balance }); return; }
      await cancel(debate, tickets?.balance ?? null);
      return;
    }
    const result = await voteDebate(debate.debateId);
    onChange(result);
    showToast(result.opened ? `${DEBATE_RULES.voteThreshold}표 달성! 토론방이 열렸어요. 투표한 사람들에게 알림이 가요.` : "투표했어요.", "done");
  });
  const keepVote = useCallback(() => setConfirming(null), []);
  const dialog = confirming ? <ConfirmDialog title="투표를 취소할까요?"
    body={`현재 투표권을 최대 ${DEBATE_RULES.ticketMax}개 보유하고 있어,\n취소해도 투표권은 추가로 반환되지 않아요.`}
    cancelLabel="투표 유지하기" confirmLabel="투표 취소" onCancel={keepVote}
    onConfirm={() => { const target = confirming; setConfirming(null); void run(target.debate.debateId, () => cancel(target.debate, target.before)); }} /> : null;
  return { pending, toggle: (debate) => void toggle(debate), dialog };
}
