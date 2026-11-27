import { DEBATE_RULES } from "./rules";
import type { DebateSummary } from "./types";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function msUntil(iso: string | null, now: number): number {
  return iso ? new Date(iso).getTime() - now : 0;
}

/** 방이 끝날 때까지: "31시간", "45분", 지났으면 "곧 종료" */
export function roomRemaining(closesAt: string | null, now = Date.now()): string {
  const left = msUntil(closesAt, now);
  if (left <= 0) return "곧 종료";
  if (left < HOUR) return `${Math.max(1, Math.ceil(left / MINUTE))}분`;
  return `${Math.floor(left / HOUR)}시간`;
}

/** 투표 마감까지: "3일 남음", "14시간 남음", "20분 남음" */
export function votingRemaining(votingEndsAt: string, now = Date.now()): string {
  const left = msUntil(votingEndsAt, now);
  if (left <= 0) return "마감";
  if (left >= DAY) return `${Math.floor(left / DAY)}일 남음`;
  if (left >= HOUR) return `${Math.floor(left / HOUR)}시간 남음`;
  return `${Math.max(1, Math.ceil(left / MINUTE))}분 남음`;
}

/** 투표 마감까지 하루 안 남았으면 마감 임박 */
export function isClosingSoon(votingEndsAt: string, now = Date.now()): boolean {
  const left = msUntil(votingEndsAt, now);
  return left > 0 && left < DAY;
}

/** "2일 14시간", 하루 안쪽이면 "14시간 20분" */
export function longRemaining(iso: string | null, now = Date.now()): string {
  const left = msUntil(iso, now);
  if (left <= 0) return "곧";
  const days = Math.floor(left / DAY);
  const hours = Math.floor((left % DAY) / HOUR);
  const minutes = Math.floor((left % HOUR) / MINUTE);
  if (days > 0) return `${days}일 ${hours}시간`;
  if (hours > 0) return `${hours}시간 ${minutes}분`;
  return `${Math.max(1, minutes)}분`;
}

/** 다음 투표권 충전까지: "18분 후", "60분 후".
 *  서버는 다음 충전을 길어야 한 주기(60분) 뒤로 준다. 폰 시계가 서버보다 조금만 늦어도 남은 시간이 60분을 살짝 넘어서
 *  예전엔 시간 단위로 올려 '2시간 후'가 떴다 (2026-11-26 팀원 제보) → 한 주기로 자르고 60분까지는 분으로 보여 준다. */
export function refillIn(nextRefillAt: string | null, now = Date.now(), periodMinutes: number = DEBATE_RULES.ticketRefillMinutes): string | null {
  if (!nextRefillAt) return null;
  const left = Math.min(Math.max(0, msUntil(nextRefillAt, now)), periodMinutes * MINUTE);
  const minutes = Math.max(1, Math.ceil(left / MINUTE));
  if (minutes <= 60) return `${minutes}분 후`;
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  return rest ? `${hours}시간 ${rest}분 후` : `${hours}시간 후`;
}

/** "2026.11.19" */
export function dateText(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  return `${date.getFullYear()}.${String(date.getMonth() + 1).padStart(2, "0")}.${String(date.getDate()).padStart(2, "0")}`;
}

/** "오후 8:12" */
export function timeText(iso: string): string {
  const date = new Date(iso);
  const hours = date.getHours();
  const period = hours < 12 ? "오전" : "오후";
  const hour12 = hours % 12 === 0 ? 12 : hours % 12;
  return `${period} ${hour12}:${String(date.getMinutes()).padStart(2, "0")}`;
}

/** 우세한 쪽 문구: "나루토 우세", 같으면 "팽팽해요", 아무도 없으면 null */
export function leadingText(debate: Pick<DebateSummary, "sideA" | "sideB">): string | null {
  const { sideA, sideB } = debate;
  if (sideA.count + sideB.count === 0) return null;
  if (sideA.count === sideB.count) return "팽팽해요";
  return `${sideA.count > sideB.count ? sideA.label : sideB.label} 우세`;
}

/** 종료된 방의 결과 문구 */
export function resultText(debate: DebateSummary): string {
  const { result, sideA, sideB } = debate;
  if (result === "a") return `${sideA.label} 승 · ${sideA.percent ?? 0}%`;
  if (result === "b") return `${sideB.label} 승 · ${sideB.percent ?? 0}%`;
  if (result === "draw") return "무승부";
  return "참여자 없음";
}
