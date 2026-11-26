import { parseAnalyticsQuery } from "./admin-analytics";
import { decimalId } from "./admin-read-contract";
import type { ReportSummary } from "./admin-reports";
import type { AttentionKind, ReportPeriod } from "./admin-statistics-api";

export type DashboardQuery = { period: ReportPeriod; attention: AttentionKind; page: number };
export type SupportTarget = Pick<ReportSummary, "id" | "recordType" | "recordId">;
const kinds: readonly string[] = ["all", "report", "inquiry", "notice"];

export function parseDashboardQuery(params: Pick<URLSearchParams, "get">): DashboardQuery {
  const attention = params.get("attention"), page = params.get("page");
  return {
    period: parseAnalyticsQuery(params).period,
    attention: kinds.includes(attention ?? "") ? attention as AttentionKind : "all",
    page: page && /^[1-9]\d{0,5}$/.test(page) && Number(page) <= 100_000 ? Number(page) : 1,
  };
}

function dashboardSearch(query: DashboardQuery): string {
  const params = new URLSearchParams();
  if (query.period !== "7d") params.set("period", query.period);
  if (query.attention !== "all") params.set("attention", query.attention);
  if (query.page !== 1) params.set("page", String(query.page));
  return params.size ? `?${params}` : "";
}

export function dashboardHref(query: DashboardQuery): string {
  return `/admin/dashboard${dashboardSearch(query)}`;
}

// Keep bigint IDs as strings, and never treat a report ID as an inquiry ID.
export function parseSupportTarget(recordType: string, recordId: string): SupportTarget | null {
  if (!["report", "inquiry"].includes(recordType) || !decimalId(recordId)) return null;
  return { recordType: recordType as SupportTarget["recordType"], recordId, id: `${recordType === "report" ? "R" : "I"}-${recordId}` };
}

export function attentionDetailHref(item: { kind: "report" | "inquiry"; recordId: number }, query: DashboardQuery): string {
  return `/admin/reports/${item.kind}/${item.recordId}${dashboardSearch(query)}`;
}
