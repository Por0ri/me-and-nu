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

/** 다음 투표권 충전까지: "18분 후", "1시간 후" */
export function refillIn(nextRefillAt: string | null, now = Date.now()): string | null {
  if (!nextRefillAt) return null;
  const left = Math.max(0, msUntil(nextRefillAt, now));
  if (left >= HOUR) return `${Math.ceil(left / HOUR)}시간 후`;
  return `${Math.max(1, Math.ceil(left / MINUTE))}분 후`;
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
