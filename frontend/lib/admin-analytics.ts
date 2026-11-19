import { reportPeriods, type ReportPeriod } from "./admin-reports";

// UI selections only. No aggregate response or evaluation contract is defined here.
export const analyticsAudiences = { all: "전체 사용자", existing: "기존 사용자", new: "신규 사용자" } as const;
export type AnalyticsAudience = keyof typeof analyticsAudiences;
export type AnalyticsQuery = { view: "overview" | "performance"; period: ReportPeriod; audience: AnalyticsAudience };

export function parseAnalyticsQuery(params: Pick<URLSearchParams, "get">): AnalyticsQuery {
  const period = params.get("period"), audience = params.get("audience");
  return {
    view: params.get("view") === "performance" ? "performance" : "overview",
    period: Object.hasOwn(reportPeriods, period ?? "") ? period as ReportPeriod : "7d",
    audience: Object.hasOwn(analyticsAudiences, audience ?? "") ? audience as AnalyticsAudience : "all",
  };
}

export function analyticsHref(query: AnalyticsQuery) {
  const params = new URLSearchParams();
  if (query.view === "performance") params.set("view", query.view);
  if (query.period !== "7d") params.set("period", query.period);
  if (query.audience !== "all") params.set("audience", query.audience);
  return `/admin/analytics${params.size ? `?${params}` : ""}`;
}
