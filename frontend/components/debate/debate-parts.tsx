"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { refillIn } from "@/lib/debate/format";
import { DEBATE_RULES as RULES } from "@/lib/debate/rules";
import type { DebateStatus, DebateSummary, DebateWork, VoteTickets } from "@/lib/debate/types";
import { ClockIcon, CloseIcon, InfoIcon } from "./debate-icons";
import styles from "./debate.module.css";

/** 프로필 사진이 없는 사람(지금은 모두)의 얼굴: me;nu 마스코트. 프로필 사진 기능이 생기면 사진이 있는 사람만 바꾼다. */
const DEFAULT_FACE = "/pivot/chat/face.svg";

export function Badge({ children, tone = "solid" }: { children: ReactNode; tone?: "solid" | "soft" | "gray" | "danger" }) {
  return <span className={styles.badge} data-tone={tone === "solid" ? undefined : tone}>{children}</span>;
}

/** 관련 작품 칩. 여러 개면 모두 보여준다. */
export function WorkChips({ works }: { works: DebateWork[] }) {
  if (works.length === 0) return null;
  return <ul className={styles.chips} aria-label="관련 작품">
    {works.map((work) => <li key={work.entityId} className={styles.chip}>{work.name}</li>)}
  </ul>;
}

/** 카드에 보이는 닉네임 글자 수. 넘으면 앞 10자 뒤에 '…' (2026-11-25 디자인 요청) */
const NICKNAME_SHOWN = 10;

/** 카드의 작성자 줄: 얼굴과 닉네임. 화면 읽기 프로그램에는 닉네임 전체를 읽어 준다. */
export function AuthorLine({ debate }: { debate: Pick<DebateSummary, "authorNickname"> }) {
  const letters = Array.from(debate.authorNickname);
  const shown = letters.length > NICKNAME_SHOWN ? `${letters.slice(0, NICKNAME_SHOWN).join("")}…` : debate.authorNickname;
  return <span className={styles.author}>
    <img src={DEFAULT_FACE} alt="" width={24} height={24} />
    <b title={shown === debate.authorNickname ? undefined : debate.authorNickname}>
      <span className={styles.srOnly}>올린 사람 {debate.authorNickname}</span><span aria-hidden="true">{shown}</span>
    </b>
  </span>;
}

function ProgressBar({ value, label }: { value: number; label: string }) {
  return <span className={styles.progress} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}>
    <span className={styles.progressFill} style={{ width: `${value}%`, display: "block" }} />
  </span>;
}

/** 주제 카드의 작은 표 수: "6/10표"와 막대 */
export function VoteMini({ count, threshold }: { count: number; threshold: number }) {
  return <span className={styles.voteMini}>
    <b>{count}/{threshold}표</b>
    <ProgressBar value={Math.min(100, Math.round((count / threshold) * 100))} label="개설까지" />
  </span>;
}

export function InfoNote({ children }: { children: ReactNode }) {
  return <div className={styles.note}><InfoIcon size={17} /><div>{children}</div></div>;
}

export function StatusBadge({ status }: { status: DebateStatus }) {
  if (status === "voting") return <Badge tone="soft">투표 중</Badge>;
  if (status === "open") return <Badge>토론방 열림</Badge>;
  if (status === "closed") return <Badge tone="soft">방 종료</Badge>;
  return <Badge tone="gray">자동 소멸</Badge>;
}

/** 일정 간격(기본 30초)마다 다시 그려서 남은 시간 문구가 흘러가게 한다. */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function refillRule(): string {
  const minutes = RULES.ticketRefillMinutes;
  return `${minutes % 60 === 0 ? `${minutes / 60}시간` : `${minutes}분`}마다 1개 충전`;
}

/** 투표권 카드 (2026-11-23 시안): 남은 투표권, 충전 규칙·막대, 다음 충전.
 *  주제 투표 탭에서는 오른쪽에 내 활동 버튼, 내 활동 화면(activity=false)에서는 버튼 없이 막대가 카드 폭을 채운다. */
export function VoteTicketBox({ tickets, activity = true }: { tickets: VoteTickets; activity?: boolean }) {
  const now = useNow(15_000);
  const next = refillIn(tickets.nextRefillAt, now);
  return <section className={styles.ticketBox} aria-label="내 투표권">
    <div className={styles.ticketMain}>
      <strong>내 투표권<em>{tickets.balance}/{tickets.max}</em></strong>
      <span className={styles.ticketRule}>{refillRule()} · 최대 {tickets.max}개</span>
      <ProgressBar value={Math.round((tickets.balance / tickets.max) * 100)} label="남은 투표권" />
      <span className={styles.refillLine}><ClockIcon size={16} />{next ? `다음 투표권 +1 · ${next}` : "투표권이 가득 찼어요"}</span>
    </div>
    {activity ? <Link className={styles.activityButton} href="/pivot/debate/activity">내 활동</Link> : null}
  </section>;
}

/** 되돌리기 어려운 일(방 나가기 등) 전에 한 번 더 묻는 창 (2026-11-23 시안: 방 나가기 창). body 의 줄바꿈(\n)은 그대로 보인다. */
export function ConfirmDialog({ title, body, confirmLabel, cancelLabel = "취소", onConfirm, onCancel }: {
  title: string; body: string; confirmLabel: string; cancelLabel?: string; onConfirm: () => void; onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return <div className={styles.modalBackdrop} onClick={onCancel}>
    <section className={styles.confirm} role="alertdialog" aria-modal="true" aria-labelledby="debate-confirm-title"
      aria-describedby="debate-confirm-body" onClick={(event) => event.stopPropagation()}>
      <h2 id="debate-confirm-title">{title}</h2>
      <p id="debate-confirm-body">{body}</p>
      <div className={styles.confirmButtons}>
        <button type="button" className={styles.confirmCancel} onClick={onCancel} autoFocus>{cancelLabel}</button>
        <button type="button" className={styles.confirmOk} onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </section>
  </div>;
}

/** 토론방 이용 안내 창 (머리 부분 ⓘ, 2026-11-23 시안). 숫자는 규칙 값(rules.ts)을 따른다. */
export function DebateGuide({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const items: ReactNode[] = [
    <>토론 주제는 업로드 후 <em>{RULES.votingDays}일 안에 {RULES.voteThreshold}표</em>를 받으면 토론방이 열려요.</>,
    <>{RULES.votingDays}일 안에 {RULES.voteThreshold}표를 받지 못한 주제는 <em>자동으로 삭제</em>돼요.</>,
    <>토론방은 개설 후 <em>{RULES.roomHours}시간</em> 동안 유지돼요.</>,
    <>투표권은 <em>{refillRule()}</em>되며, <em>최대 {RULES.ticketMax}개</em>까지 보유할 수 있어요.</>,
    <>투표를 취소하면 투표권 1개가 <em>바로 반환</em>돼요. 단, 보유 투표권이 이미 {RULES.ticketMax}개면 추가 반환되지 않아요.</>,
  ];
  return <div className={styles.modalBackdrop} onClick={onClose}>
    <section className={styles.guide} role="dialog" aria-modal="true" aria-labelledby="debate-guide-title" onClick={(event) => event.stopPropagation()}>
      <header className={styles.guideHead}>
        <h2 id="debate-guide-title"><InfoIcon size={22} />토론방 이용 안내</h2>
        <button type="button" className={styles.iconButton} aria-label="닫기" onClick={onClose} autoFocus><CloseIcon size={22} /></button>
      </header>
      <ol className={styles.guideList}>
        {items.map((item, index) => <li key={index}><span className={styles.guideNumber} aria-hidden="true">{index + 1}</span><p>{item}</p></li>)}
      </ol>
    </section>
  </div>;
}
