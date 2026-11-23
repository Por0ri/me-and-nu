import type { AdminFailure } from "./admin-content";
import { adminReadRequest, decimalId, integer, invalidAdminResponse, nullableText, privateSearchHeaders, record, timestamp } from "./admin-read-contract";

// Presentation models only. No administrator transport, permission, DB enum, or
// workflow is inferred from the consumer report/inquiry submission contracts.
export const reportStatuses = { waiting: "검토 대기", reviewing: "처리 중", completed: "완료" } as const;
export const reportPeriods = { "7d": "최근 7일", "30d": "최근 30일", "3m": "최근 3개월", "6m": "최근 6개월" } as const;
export type ReportStatus = keyof typeof reportStatuses;
export type ReportPeriod = keyof typeof reportPeriods;
export const reportKinds = { all: "전체 유형", content: "콘텐츠 신고", service: "서비스 문의", bug: "버그/오류 문의" } as const;
export type ReportQuery = { q: string; status: ReportStatus | ""; period: ReportPeriod; page: number; kind: keyof typeof reportKinds };
export const reportPageSize = 5;
export const initialReportQuery: ReportQuery = { q: "", status: "", period: "7d", page: 1, kind: "all" };
export type ReportContent = {
  contentId: string; title: string; subtitle: string | null; category: string | null; coverUrl: string | null;
  body: string | null; source: { title: string; url: string } | null; aiGenerated: boolean; productionType: "human" | "ai" | "hybrid";
};
export type ReportSummary = {
  id: string; number: string; kind: "content" | "service" | "bug";
  recordId: string; recordType: "report" | "inquiry"; title: string | null;
  reasonCode: string | null; reason: string | null; status: ReportStatus | null; rawStatus: string;
  receivedAt: string | null; updatedAt: string;
};
export type ReportRecord = ReportSummary & { message: string | null; target: string | null; content: ReportContent | null; answer: null; answerAvailability: "unavailable" };
export type ReportPage = { items: ReportSummary[]; total: number; page: number; pageSize: number; periodStart: string; periodEnd: string; statistics: Record<"all" | ReportStatus, number> };
export type ReportStatistics = Record<"all" | ReportStatus, { count: number; change: number | null }>;
export const reportUnavailable: AdminFailure = {
  status: "unavailable", message: "관리자 신고·문의 조회는 준비 중입니다. 조회 권한과 데이터 연결이 완료되면 이용할 수 있습니다.",
};

export function reportDate(value: string | null, withTime = false): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "—";
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } as const : {}) }).format(new Date(value)).replaceAll("-", ".");
}

export function reportKind(kind: ReportRecord["kind"], detail = false): string {
  return kind === "content" ? "콘텐츠 신고" : kind === "bug" ? "버그/오류 문의" : "서비스 문의";
}

export function reportSourceUrl(value: string): string | null {
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : null; } catch { return null; }
}

export const rawReportStatuses: Record<string, string> = { received: "접수", reviewing: "검토 중", resolved: "해결", rejected: "반려", answered: "답변 완료", closed: "종료" };
const statusGroups: Record<string, ReportStatus> = { received: "waiting", reviewing: "reviewing", resolved: "completed", rejected: "completed", answered: "completed", closed: "completed" };
function summary(value: unknown): value is Omit<ReportSummary, "number"> & Record<string, unknown> {
  return record(value) && decimalId(value.recordId) && ["report", "inquiry"].includes(String(value.recordType)) &&
    value.id === `${value.recordType === "report" ? "R" : "I"}-${value.recordId}` &&
    (value.recordType === "report" ? value.kind === "content" : ["service", "bug"].includes(String(value.kind))) &&
    nullableText(value.title) && nullableText(value.reasonCode) && nullableText(value.reason) &&
    typeof value.rawStatus === "string" && Object.hasOwn(statusGroups, value.rawStatus) && statusGroups[value.rawStatus] === value.status &&
    timestamp(value.receivedAt) && timestamp(value.updatedAt);
}
export function reportQueryString(query: ReportQuery): string {
  if (!Object.hasOwn(reportPeriods, query.period) || !Object.hasOwn(reportKinds, query.kind) ||
    (query.status !== "" && !Object.hasOwn(reportStatuses, query.status)) || !integer(query.page, 1) || query.page > 100_000) throw new Error("조회 조건을 확인해 주세요.");
  const params = new URLSearchParams({ period: query.period, kind: query.kind, page: String(query.page), pageSize: String(reportPageSize) });
  if (query.status) params.set("status", query.status);
  return params.toString();
}
export async function loadAdminReports(query: ReportQuery, signal?: AbortSignal): Promise<ReportPage> {
  const value = await adminReadRequest(`/api/v1/admin/reports?${reportQueryString(query)}`, { signal, headers: privateSearchHeaders(query.q) });
  if (!record(value) || value.page !== query.page || value.pageSize !== reportPageSize || !integer(value.total) ||
    !Array.isArray(value.items) || !value.items.every(summary) || value.items.length > reportPageSize || value.items.length > value.total ||
    new Set(value.items.map(item => item.id)).size !== value.items.length ||
    value.items.some(item => (query.status && item.status !== query.status) || (query.kind !== "all" && item.kind !== query.kind) || "message" in item || "content" in item) ||
    !timestamp(value.periodStart) || !timestamp(value.periodEnd) || Date.parse(value.periodStart) >= Date.parse(value.periodEnd) ||
    !record(value.statistics) || !["all", "waiting", "reviewing", "completed"].every(key => integer((value.statistics as Record<string, unknown>)[key]))) invalidAdminResponse();
  const counts = value.statistics as Record<"all" | ReportStatus, number>;
  if (counts.all !== counts.waiting + counts.reviewing + counts.completed || value.total !== counts[query.status || "all"]) invalidAdminResponse();
  return { ...value, items: value.items.map(item => ({ ...item, number: item.id })) } as ReportPage;
}
export async function loadAdminReport(item: Pick<ReportSummary, "id" | "recordType" | "recordId">, signal?: AbortSignal): Promise<ReportRecord> {
  if (!decimalId(item.recordId) || !["report", "inquiry"].includes(item.recordType) || item.id !== `${item.recordType === "report" ? "R" : "I"}-${item.recordId}`) invalidAdminResponse();
  const value = await adminReadRequest(`/api/v1/admin/${item.recordType === "report" ? "reports" : "inquiries"}/${item.recordId}`, { signal });
  if (!record(value) || !summary(value) || value.id !== item.id || value.recordType !== item.recordType || !nullableText(value.message) || !nullableText(value.target) ||
    value.answer !== null || value.answerAvailability !== "unavailable") invalidAdminResponse();
  const content = value.content;
  if (content !== null && (!record(content) || !decimalId(content.contentId) || typeof content.title !== "string" ||
    ![content.subtitle, content.category, content.coverUrl, content.body].every(nullableText) ||
    !["human", "ai", "hybrid"].includes(String(content.productionType)) || content.aiGenerated !== (content.productionType === "ai") ||
    !(content.source === null || (record(content.source) && typeof content.source.title === "string" && typeof content.source.url === "string")))) invalidAdminResponse();
  if (value.recordType === "inquiry" && value.content !== null) invalidAdminResponse();
  return { ...value, number: value.id } as ReportRecord;
}
