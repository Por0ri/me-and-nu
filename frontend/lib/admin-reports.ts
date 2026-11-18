import type { AdminFailure } from "./admin-content";

// Presentation models only. No administrator transport, permission, DB enum, or
// workflow is inferred from the consumer report/inquiry submission contracts.
export const reportStatuses = { waiting: "검토 대기", reviewing: "처리 중", completed: "완료" } as const;
export const reportPeriods = { "7d": "최근 7일", "30d": "최근 30일", "3m": "최근 3개월", "6m": "최근 6개월" } as const;
export type ReportStatus = keyof typeof reportStatuses;
export type ReportPeriod = keyof typeof reportPeriods;
export type ReportQuery = { q: string; status: ReportStatus | ""; period: ReportPeriod; page: number };
export const reportPageSize = 5;
export const initialReportQuery: ReportQuery = { q: "", status: "", period: "7d", page: 1 };
export type ReportContent = {
  title: string; subtitle: string | null; category: string | null; coverUrl: string | null;
  body: string; source: { title: string; url: string } | null; aiGenerated: boolean;
};
export type ReportRecord = {
  id: string; number: string; kind: "content" | "service" | "bug";
  reason: string | null; message: string; status: ReportStatus | null;
  receivedAt: string | null; target: string | null; content: ReportContent | null;
};
export type ReportPage = { items: ReportRecord[]; total: number };
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
  return kind === "content" ? "콘텐츠 신고" : kind === "bug" ? "버그/오류 신고" : detail ? "서비스 문의" : "서비스 신고";
}

export function reportSourceUrl(value: string): string | null {
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : null; } catch { return null; }
}
