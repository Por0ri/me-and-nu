"use client";

import { useEffect, useState, type ReactNode } from "react";
import { refillIn } from "@/lib/debate/format";
import type { DebateStatus, DebateWork, VoteTickets } from "@/lib/debate/types";
import { ClockIcon, InfoIcon, TicketIcon } from "./debate-icons";
import styles from "./debate.module.css";

export function Badge({ children, tone = "solid" }: { children: ReactNode; tone?: "solid" | "soft" | "gray" | "danger" }) {
  return <span className={styles.badge} data-tone={tone === "solid" ? undefined : tone}>{children}</span>;
}

export function WorkChips({ works }: { works: DebateWork[] }) {
  if (works.length === 0) return null;
  return <ul className={styles.chips} aria-label="관련 작품">
    {works.map((work) => <li key={work.entityId} className={styles.chip}>#{work.name}</li>)}
  </ul>;
}

export function VoteProgress({ count, threshold, large = false }: { count: number; threshold: number; large?: boolean }) {
  const percent = Math.min(100, Math.round((count / threshold) * 100));
  return <div className={styles.voteLine}>
    <span className={styles.voteCount} data-large={large || undefined}>{count}/{threshold}표</span>
    <span className={styles.progress} role="progressbar" aria-label="개설까지" aria-valuemin={0} aria-valuemax={threshold} aria-valuenow={count}>
      <span className={styles.progressFill} style={{ width: `${percent}%`, display: "block" }} />
    </span>
  </div>;
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

/** 투표권 카드. vote: 주제 투표 탭, activity: 내 활동 */
export function TicketCard({ tickets, variant }: { tickets: VoteTickets; variant: "vote" | "activity" }) {
  const now = useNow(15_000);
  const next = refillIn(tickets.nextRefillAt, now);
  const segments = Array.from({ length: tickets.max }, (_, index) => index < tickets.balance);
  return <section className={styles.ticketCard} aria-label="내 투표권">
    <div className={styles.ticketTop}>
      <span className={styles.ticketIcon}><TicketIcon size={26} /></span>
      <div className={styles.ticketText}>
        <strong>{variant === "vote" ? "내 투표권" : "보유 투표권"}<em>{tickets.balance}/{tickets.max}</em></strong>
        {variant === "vote"
          ? <span>{next ? `${next} 1개 충전` : "투표권이 가득 찼어요"}</span>
          : <span>1시간마다 1개 충전 · 최대 {tickets.max}개</span>}
        {variant === "activity" && next ? <span className={styles.refillChip}><ClockIcon size={13} />다음 투표권 +1 · {next}</span> : null}
      </div>
    </div>
    {variant === "vote"
      ? <div className={styles.segments} style={{ ["--segments" as string]: tickets.max }} aria-hidden="true">
        {segments.map((on, index) => <span key={index} className={styles.segment} data-on={on || undefined} />)}
      </div>
      : <p className={styles.ticketFoot}><InfoIcon size={15} />투표 취소 시 1개 즉시 반환 · 최대 {tickets.max}개 초과 반환 불가</p>}
  </section>;
}
