import type { AdminFailure } from "./admin-content";

// Presentation models only; these are not administrator API/DB contracts.
export const noticeStatuses = { published: "게시 중", scheduled: "예약" } as const;
export type NoticeStatus = keyof typeof noticeStatuses;
export type NoticeQuery = { q: string; status: NoticeStatus | ""; page: number };
export type NoticeRecord = { id: string; title: string; body: string | null; status: NoticeStatus | null; publishedAt: string | null; views: number | null };
export type NoticePage = { items: NoticeRecord[]; total: number };
export type NoticeStatistics = Record<"all" | NoticeStatus, { count: number; change: number | null }>;
export type NoticeDraft = { title: string; body: string; scheduled: boolean; date: string; time: string };
export const noticePageSize = 7;
export const initialNoticeQuery: NoticeQuery = { q: "", status: "", page: 1 };
export const emptyNoticeDraft: NoticeDraft = { title: "", body: "", scheduled: false, date: "", time: "" };
export const noticeUnavailable: AdminFailure = { status: "unavailable", message: "관리자 알림·공지 조회는 준비 중입니다. 조회 권한과 데이터 연결이 완료되면 이용할 수 있습니다." };

export function noticeDate(value: string | null, withTime = false): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "—";
  const date = new Date(value);
  const day = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(date).replaceAll("-", ".");
  if (!withTime) return day;
  const weekday = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", weekday: "short" }).format(date);
  const time = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
  return `${day} (${weekday}) ${time}`;
}

export function noticeScheduleDate(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "날짜 선택";
  const date = new Date(`${value}T00:00:00+09:00`);
  if (!Number.isFinite(date.getTime())) return "날짜 선택";
  return `${value.replaceAll("-", ".")} (${new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", weekday: "short" }).format(date)})`;
}
