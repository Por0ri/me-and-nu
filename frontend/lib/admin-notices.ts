import type { AdminFailure } from "./admin-content";
import { adminReadRequest, decimalId, integer, invalidAdminResponse, privateSearchHeaders, record, timestamp } from "./admin-read-contract";

export const noticeStatuses = { draft: "임시 저장", published: "게시 중", scheduled: "예약" } as const;
export type NoticeStatus = keyof typeof noticeStatuses;
export type NoticeQuery = { q: string; status: NoticeStatus | ""; page: number };
export type NoticeScope = { actorUserId: string; adminAccountId: string };
export type NoticeAction = "save_draft" | "publish" | "schedule" | "cancel_schedule";
export type NoticeCapability = { allowedActions: (NoticeAction | "create")[]; disabledReason: null | "permission_denied" | "terminal_state"; scope: NoticeScope | null };
export type NoticeSummary = { id: string; title: string; status: NoticeStatus; scheduledAt: string | null; publishedAt: string | null; createdAt: string; updatedAt: string; readCount: number };
export type NoticeRecord = NoticeSummary & { body: string | null; actions?: NoticeCapability };
export type NoticePage = { items: NoticeSummary[]; total: number; statistics?: Record<"all" | NoticeStatus, number>; capabilities?: NoticeCapability };
export type NoticeStatistics = Record<"all" | NoticeStatus, { count: number; change: number | null }>;
export type NoticeDraft = { title: string; body: string; scheduled: boolean; date: string; time: string };
export const noticePageSize = 7;
export const initialNoticeQuery: NoticeQuery = { q: "", status: "", page: 1 };
export const emptyNoticeDraft: NoticeDraft = { title: "", body: "", scheduled: false, date: "", time: "" };
export const noticeUnavailable: AdminFailure = { status: "unavailable", message: "관리자 알림·공지 조회는 준비 중입니다. 조회 권한과 데이터 연결이 완료되면 이용할 수 있습니다." };
export const noticeVersion = (value: unknown): value is string => timestamp(value) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value);
export const validNoticeScope = (value: unknown): value is NoticeScope => record(value) && decimalId(value.actorUserId) && decimalId(value.adminAccountId);
export function noticeActionsFor(status: NoticeStatus): NoticeAction[] {
  return status === "draft" ? ["save_draft", "publish", "schedule"] : status === "scheduled" ? ["cancel_schedule"] : [];
}
function capability(value: unknown, allowed: readonly string[]): value is NoticeCapability {
  if (!record(value) || !Array.isArray(value.allowedActions) || new Set(value.allowedActions).size !== value.allowedActions.length ||
    value.allowedActions.some(action => !allowed.includes(String(action))) || ![null, "permission_denied", "terminal_state"].includes(value.disabledReason as null) ||
    !(value.scope === null || validNoticeScope(value.scope))) return false;
  return value.disabledReason === null ? value.allowedActions.length > 0 && validNoticeScope(value.scope)
    : value.allowedActions.length === 0 && (value.disabledReason !== "terminal_state" || allowed.length === 0);
}
function summary(value: unknown): value is NoticeSummary & Record<string, unknown> {
  if (!record(value) || !decimalId(value.id) || typeof value.title !== "string" || !Object.hasOwn(noticeStatuses, String(value.status)) ||
    !noticeVersion(value.createdAt) || !noticeVersion(value.updatedAt) || !integer(value.readCount) ||
    !(value.scheduledAt === null || noticeVersion(value.scheduledAt)) || !(value.publishedAt === null || noticeVersion(value.publishedAt))) return false;
  return value.status === "published" ? noticeVersion(value.publishedAt) : value.publishedAt === null && (value.status !== "scheduled" || noticeVersion(value.scheduledAt));
}
export function noticeQueryString(query: NoticeQuery) {
  if (!integer(query.page, 1) || (query.status && !Object.hasOwn(noticeStatuses, query.status))) throw new Error("조회 조건을 확인해 주세요.");
  return new URLSearchParams({ status: query.status || "all", page: String(query.page), pageSize: String(noticePageSize) }).toString();
}
export async function loadAdminNotices(query: NoticeQuery, signal?: AbortSignal): Promise<NoticePage> {
  const data = await adminReadRequest(`/api/v1/admin/notices?${noticeQueryString(query)}`, { signal, headers: privateSearchHeaders(query.q) });
  if (!record(data) || data.page !== query.page || data.pageSize !== noticePageSize || !Array.isArray(data.items) || !data.items.every(summary) || !integer(data.total) || data.items.length > noticePageSize ||
    new Set(data.items.map(item => item.id)).size !== data.items.length || !record(data.statistics) ||
    !["all", "draft", "scheduled", "published"].every(key => integer((data.statistics as Record<string, unknown>)[key])) ||
    !capability(data.capabilities, ["create"])) return invalidAdminResponse();
  return data as NoticePage;
}
export async function loadAdminNotice(id: string, signal?: AbortSignal): Promise<NoticeRecord> {
  if (!decimalId(id)) throw new Error("공지 번호를 확인해 주세요.");
  const data = await adminReadRequest(`/api/v1/admin/notices/${id}`, { signal });
  if (!summary(data) || data.id !== id || !record(data) || typeof data.body !== "string" || !capability(data.actions, noticeActionsFor(data.status))) return invalidAdminResponse();
  return data as NoticeRecord;
}

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
