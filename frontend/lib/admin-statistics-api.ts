import { apiRequest, ApiError } from "@/lib/api";
import type { ReportPeriod } from "./admin-reports";
import type { AnalyticsAudience, EvaluationDataset } from "./admin-analytics";

export type { ReportPeriod, AnalyticsAudience };
export type Availability = "available" | "partial" | "unavailable";
export type Metric = {
  availability: Availability; reason: string | null; value: number | null;
  unit: "count" | "percent" | "seconds"; definition: string;
  numerator: number | null; denominator: number | null;
};
export type AnalyticsMeta = {
  generatedAt: string; window: { period: ReportPeriod; startAt: string; endAt: string; timezone: "UTC" };
  eventDataSource: string; scope: "connected_database"; limitations: string[];
};
export type UsageMetrics = { activeUsers: Metric; signupFeedObservationRate: Metric; contentReadSegments: Metric };
export type Ranking = {
  availability: Availability; reason: string | null; definition: string;
  items: { rank: number; label: string; count: number; contentId: number | null }[];
};
export type Distribution = {
  availability: Availability; reason: string | null; definition: string; sampleSize: number | null;
  items: { key: string; label: string; count: number; percentage: number }[];
};
export type FunnelStep = { key: string; label: string; metric: Metric };
export type GenerationSummary = {
  windowStartAt: string; windowEndAt: string; availability: Availability; reason: string; definition: string;
  succeeded: number; inProgress: number; failed: number; cancelled: number;
};
export type AttentionKind = "all" | "report" | "inquiry" | "notice";
export type AttentionQuery = { kind: AttentionKind; page: number; pageSize: number };
export type AttentionResponse = {
  generatedAt: string; availability: Availability; reason: string | null;
  knownTotal: number; reports: number; inquiries: number; scheduledNotices: Metric; page: number; pageSize: number;
  items: { kind: "report" | "inquiry"; recordId: number; status: string; category: string; createdAt: string; contentId: number | null }[];
};
export type EvaluationConditions = {
  evaluationData: string; datasetSha256: string; inputConditions: string; aggregationUnit: "series" | "content";
  commonRules: string; targetPeriod: string; groundTruth: string; candidatePolicy: string; recordSource: string;
  cohortInterpretation: "input_scenarios" | "disjoint_users";
};
export type RecommendationScore = {
  audience: "existing" | "new"; sampleSize: number; ndcgAt10: number; hitRateAt10: number;
  ndcgDeltaVsBaseline: number; hitRateDeltaVsBaseline: number;
};
export type RecommendationModelScores = { modelId: string; label: string; version: string; scores: RecommendationScore[] };
export type EvaluationPipelineStatus = {
  schemaVersion: 1; dataset: "service"; state: "running" | "published" | "waiting" | "unchanged" | "failed";
  reason: string | null; dataSource: "production" | "local_demo" | "automated_test" | "imported" | null;
  observationDays: number; minSamples: number; collectedCases: number; matureCases: number; eligibleCases: number;
  cohortSampleSizes: { existing: number; new: number }; lastEvaluationId: string | null;
  lastAttemptAt: string; lastSuccessAt: string | null; lastPublishedAt: string | null;
};
export type RecommendationPerformance = {
  availability: "available" | "unavailable"; reason: string | null; audience: AnalyticsAudience; period: ReportPeriod;
  periodStart: string; periodEnd: string; timeBasis: "evaluation_completed_at"; source: "offline_evaluation" | "service_feedback";
  pipeline?: EvaluationPipelineStatus | null;
  dataSource?: "production" | "local_demo" | null;
  metricUnit: "ratio"; k: 10; groundTruthPerCase: 1;
  evaluationId: string | null; evaluatedAt: string | null; baselineModelId: string | null; currentModelId: string | null;
  conditions: EvaluationConditions | null;
  cohorts: { audience: "existing" | "new"; definition: string; caseSetSha256: string; sampleSize: number }[];
  models: RecommendationModelScores[];
};
export type AnalyticsOverviewResponse = {
  meta: AnalyticsMeta; usage: UsageMetrics; funnel: FunnelStep[]; preferenceDistribution: Distribution; deviceDistribution: Distribution;
  topContents: Ranking; popularSearches: Ranking; zeroResultSearches: Ranking; returningUserRate: Metric; averageSessionDuration: Metric;
};
export type DashboardResponse = {
  meta: AnalyticsMeta; usage: UsageMetrics; topContents: Ranking; preferenceDistribution: Distribution;
  generation: GenerationSummary; attention: AttentionResponse; todayNotices: Metric; recommendationPerformance: RecommendationPerformance;
  serviceRecommendationPerformance?: RecommendationPerformance | null;
};

type RecordValue = Record<string, unknown>;
const periods = { "7d": 7, "30d": 30, "3m": 90, "6m": 180 } as const;
const audiences = ["all", "existing", "new"];
const availability = ["available", "partial", "unavailable"];
const performanceReasons = ["not_configured", "artifact_missing", "artifact_invalid", "artifact_unreadable", "outside_period", "audience_not_evaluated"];
const record = (v: unknown): v is RecordValue => !!v && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === "string" && v.length > 0;
const nullableText = (v: unknown) => v === null || text(v);
const integer = (v: unknown, min = 0): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= min;
const bounded = (v: unknown, min: number, max: number): v is number => typeof v === "number" && Number.isFinite(v) && v >= min && v <= max;
const timestamp = (v: unknown): v is string => typeof v === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v));
const digest = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
const distinct = (values: unknown[]) => new Set(values).size === values.length;
function invalid(): never { throw new ApiError("통계 응답을 확인하지 못했습니다. 다시 조회해 주세요.", 200, "INVALID_ADMIN_RESPONSE"); }
function validPeriod(v: unknown): v is ReportPeriod { return typeof v === "string" && Object.hasOwn(periods, v); }
function queryCheck(condition: boolean): void {
  if (!condition) throw new ApiError("조회 조건을 확인해 주세요.", 422, "VALIDATION_ERROR");
}
function state(v: RecordValue): boolean {
  return availability.includes(v.availability as string) && nullableText(v.reason) &&
    (v.availability === "available" ? v.reason === null : text(v.reason));
}
function isMetric(v: unknown): v is Metric {
  if (!record(v) || !state(v) || !text(v.definition) || !["count", "percent", "seconds"].includes(v.unit as string) ||
      !(v.numerator === null || integer(v.numerator)) || !(v.denominator === null || integer(v.denominator))) return false;
  if (v.availability === "unavailable") return v.value === null;
  if (v.unit === "count" ? !integer(v.value) : !bounded(v.value, 0, v.unit === "percent" ? 100 : Number.MAX_VALUE)) return false;
  return v.denominator === null || (integer(v.denominator, 1) && integer(v.numerator) && v.numerator <= v.denominator);
}
function interval(start: unknown, end: unknown, period: ReportPeriod): boolean {
  return timestamp(start) && timestamp(end) && Date.parse(end) - Date.parse(start) === periods[period] * 86_400_000;
}
function isMeta(v: unknown, period: ReportPeriod): v is AnalyticsMeta {
  return record(v) && timestamp(v.generatedAt) && record(v.window) && v.window.period === period && v.window.timezone === "UTC" &&
    interval(v.window.startAt, v.window.endAt, period) && v.scope === "connected_database" &&
    ["production", "local_demo", "automated_test", "imported"].includes(v.eventDataSource as string) &&
    Array.isArray(v.limitations) && v.limitations.every(text);
}
function isUsage(v: unknown): v is UsageMetrics {
  return record(v) && isMetric(v.activeUsers) && v.activeUsers.unit === "count" &&
    isMetric(v.signupFeedObservationRate) && v.signupFeedObservationRate.unit === "percent" &&
    isMetric(v.contentReadSegments) && v.contentReadSegments.unit === "count";
}
function isRanking(v: unknown): v is Ranking {
  return record(v) && state(v) && text(v.definition) && Array.isArray(v.items) && v.items.length <= 5 &&
    (v.availability !== "unavailable" || v.items.length === 0) && v.items.every((item, index) =>
      record(item) && item.rank === index + 1 && text(item.label) && integer(item.count) && (item.contentId === null || integer(item.contentId, 1)));
}
function isDistribution(v: unknown): v is Distribution {
  if (!record(v) || !state(v) || !text(v.definition) || !(v.sampleSize === null || integer(v.sampleSize)) || !Array.isArray(v.items)) return false;
  if (v.availability === "unavailable") return v.items.length === 0 && (v.sampleSize === null || v.sampleSize === 0);
  if (!integer(v.sampleSize, 1) || !v.items.length || v.items.length > 100) return false;
  const sampleSize = v.sampleSize;
  return v.items.every(item => record(item) && text(item.key) && text(item.label) && integer(item.count) &&
    bounded(item.percentage, 0, 100) && Math.abs(item.percentage - item.count / sampleSize * 100) <= .0001) &&
    distinct(v.items.map(item => item.key)) && v.items.reduce((sum, item) => sum + item.count, 0) === sampleSize;
}
function isGeneration(v: unknown): v is GenerationSummary {
  return record(v) && state(v) && text(v.definition) && timestamp(v.windowStartAt) && timestamp(v.windowEndAt) &&
    Date.parse(v.windowEndAt) - Date.parse(v.windowStartAt) === 86_400_000 &&
    [v.succeeded, v.inProgress, v.failed, v.cancelled].every(n => integer(n));
}
function isAttention(v: unknown, query: AttentionQuery): v is AttentionResponse {
  if (!record(v) || !state(v) || !timestamp(v.generatedAt) || !integer(v.knownTotal) || !integer(v.reports) || !integer(v.inquiries) ||
      !isMetric(v.scheduledNotices) || v.scheduledNotices.unit !== "count" || v.page !== query.page || v.pageSize !== query.pageSize ||
      !Array.isArray(v.items) || v.items.length > query.pageSize || v.items.length > v.knownTotal ||
      (v.availability === "unavailable" && v.items.length !== 0)) return false;
  const knownTotal = query.kind === "all" ? v.reports + v.inquiries : query.kind === "report" ? v.reports : query.kind === "inquiry" ? v.inquiries : 0;
  return v.knownTotal === knownTotal && (query.kind !== "notice" || (v.availability === "unavailable" && v.items.length === 0)) &&
    v.items.every(item => record(item) && ["report", "inquiry"].includes(item.kind as string) &&
      (query.kind === "all" || query.kind === item.kind) && integer(item.recordId, 1) && text(item.status) && text(item.category) && timestamp(item.createdAt) &&
      (item.contentId === null || integer(item.contentId, 1))) && distinct(v.items.map(item => `${item.kind}:${item.recordId}`));
}
function isConditions(v: unknown): v is EvaluationConditions {
  return record(v) && ["evaluationData", "inputConditions", "commonRules", "targetPeriod", "groundTruth", "candidatePolicy", "recordSource"].every(key => text(v[key])) &&
    digest(v.datasetSha256) && ["series", "content"].includes(v.aggregationUnit as string) && ["input_scenarios", "disjoint_users"].includes(v.cohortInterpretation as string);
}
function isPipeline(v: unknown): v is EvaluationPipelineStatus {
  return record(v) && v.schemaVersion === 1 && v.dataset === "service" &&
    ["running", "published", "waiting", "unchanged", "failed"].includes(v.state as string) &&
    (v.reason === null || (typeof v.reason === "string" && /^[a-z][a-z0-9_]{0,79}$/.test(v.reason))) &&
    (v.dataSource === null || ["production", "local_demo", "automated_test", "imported"].includes(v.dataSource as string)) &&
    integer(v.observationDays, 1) && v.observationDays <= 30 && integer(v.minSamples, 1) && v.minSamples <= 100000 &&
    [v.collectedCases, v.matureCases, v.eligibleCases].every(n => integer(n)) &&
    record(v.cohortSampleSizes) && integer(v.cohortSampleSizes.existing) && integer(v.cohortSampleSizes.new) &&
    nullableText(v.lastEvaluationId) && timestamp(v.lastAttemptAt) &&
    (v.lastSuccessAt === null || timestamp(v.lastSuccessAt)) && (v.lastPublishedAt === null || timestamp(v.lastPublishedAt));
}
function isPerformance(v: unknown, period: ReportPeriod, audience: AnalyticsAudience, dataset: EvaluationDataset = "offline"): v is RecommendationPerformance {
  if (!record(v) || v.period !== period || v.audience !== audience || !interval(v.periodStart, v.periodEnd, period) ||
      v.timeBasis !== "evaluation_completed_at" || v.source !== (dataset === "service" ? "service_feedback" : "offline_evaluation") || v.metricUnit !== "ratio" || v.k !== 10 || v.groundTruthPerCase !== 1 ||
      (v.pipeline != null && (dataset !== "service" || !isPipeline(v.pipeline))) ||
      !Array.isArray(v.cohorts) || !Array.isArray(v.models)) return false;
  if (v.availability === "unavailable") return performanceReasons.includes(v.reason as string) &&
    [v.evaluationId, v.evaluatedAt, v.baselineModelId, v.currentModelId, v.conditions].every(item => item === null) && !v.models.length && !v.cohorts.length;
  if (v.availability !== "available" || v.reason !== null || !text(v.evaluationId) || !timestamp(v.evaluatedAt) ||
      Date.parse(v.evaluatedAt) < Date.parse(v.periodStart as string) || Date.parse(v.evaluatedAt) >= Date.parse(v.periodEnd as string) ||
      (dataset === "service" ? !["production", "local_demo"].includes(v.dataSource as string) : v.dataSource != null) ||
      !isConditions(v.conditions) || v.conditions.aggregationUnit !== (dataset === "service" ? "content" : "series") ||
      (dataset === "service" && v.conditions.cohortInterpretation !== "disjoint_users") || !text(v.baselineModelId) || !text(v.currentModelId) ||
      !v.cohorts.length || v.cohorts.length > 2 || v.models.length < 2 || v.models.length > 16) return false;
  if (!v.cohorts.every(c => record(c) && ["existing", "new"].includes(c.audience as string) &&
      (audience === "all" || audience === c.audience) && text(c.definition) && digest(c.caseSetSha256) && integer(c.sampleSize, 1)) ||
      !distinct(v.cohorts.map(c => c.audience))) return false;
  const cohorts = v.cohorts as RecommendationPerformance["cohorts"];
  if (!v.models.every(m => record(m) && text(m.modelId) && text(m.label) && text(m.version) && Array.isArray(m.scores) && m.scores.length === cohorts.length &&
      distinct(m.scores.map(s => record(s) ? s.audience : null)) && m.scores.every(s => record(s) &&
        cohorts.some(c => c.audience === s.audience && c.sampleSize === s.sampleSize) &&
        bounded(s.ndcgAt10, 0, 1) && bounded(s.hitRateAt10, 0, 1) && s.ndcgAt10 <= s.hitRateAt10 + 1e-12 &&
        bounded(s.ndcgDeltaVsBaseline, -1, 1) && bounded(s.hitRateDeltaVsBaseline, -1, 1)))) return false;
  const models = v.models as RecommendationModelScores[];
  const baseline = models.find(m => m.modelId === v.baselineModelId);
  if (!baseline || !models.some(m => m.modelId === v.currentModelId) || !distinct(models.map(m => m.modelId))) return false;
  return models.every(m => m.scores.every(s => {
    const b = baseline.scores.find(base => base.audience === s.audience)!;
    return Math.abs(s.ndcgDeltaVsBaseline - (s.ndcgAt10 - b.ndcgAt10)) < 1e-9 &&
      Math.abs(s.hitRateDeltaVsBaseline - (s.hitRateAt10 - b.hitRateAt10)) < 1e-9;
  }));
}

export async function loadAdminDashboard(period: ReportPeriod, signal?: AbortSignal): Promise<DashboardResponse> {
  queryCheck(validPeriod(period));
  const v = await apiRequest<unknown>(`/api/v1/admin/dashboard?${new URLSearchParams({ period })}`, { signal }, 200);
  if (!record(v) || !isMeta(v.meta, period) || !isUsage(v.usage) || !isRanking(v.topContents) || !isDistribution(v.preferenceDistribution) ||
      !isGeneration(v.generation) || !isAttention(v.attention, { kind: "all", page: 1, pageSize: 5 }) || !isMetric(v.todayNotices) || v.todayNotices.unit !== "count" ||
      !isPerformance(v.recommendationPerformance, period, "all") ||
      (v.serviceRecommendationPerformance != null && !isPerformance(v.serviceRecommendationPerformance, period, "all", "service"))) invalid();
  return v as DashboardResponse;
}

export async function loadAdminOverview(period: ReportPeriod, signal?: AbortSignal): Promise<AnalyticsOverviewResponse> {
  queryCheck(validPeriod(period));
  const v = await apiRequest<unknown>(`/api/v1/admin/analytics/overview?${new URLSearchParams({ period })}`, { signal }, 200);
  if (!record(v) || !isMeta(v.meta, period) || !isUsage(v.usage) || !Array.isArray(v.funnel) ||
      !v.funnel.every(s => record(s) && text(s.key) && text(s.label) && isMetric(s.metric) && s.metric.unit === "count") || !distinct(v.funnel.map(s => s.key)) ||
      !isDistribution(v.preferenceDistribution) || !isDistribution(v.deviceDistribution) || !isRanking(v.topContents) ||
      !isRanking(v.popularSearches) || !isRanking(v.zeroResultSearches) || !isMetric(v.returningUserRate) || v.returningUserRate.unit !== "percent" ||
      !isMetric(v.averageSessionDuration) || v.averageSessionDuration.unit !== "seconds") invalid();
  return v as AnalyticsOverviewResponse;
}

export async function loadAdminAttention(query: AttentionQuery, signal?: AbortSignal): Promise<AttentionResponse> {
  queryCheck(["all", "report", "inquiry", "notice"].includes(query.kind) && integer(query.page, 1) && query.page <= 100_000 && integer(query.pageSize, 1) && query.pageSize <= 100);
  const params = new URLSearchParams({ kind: query.kind, page: String(query.page), pageSize: String(query.pageSize) });
  const v = await apiRequest<unknown>(`/api/v1/admin/dashboard/attention?${params}`, { signal }, 200);
  if (!isAttention(v, query)) invalid();
  return v;
}

export async function loadRecommendationPerformance(query: { period: ReportPeriod; audience: AnalyticsAudience; dataset?: EvaluationDataset }, signal?: AbortSignal): Promise<RecommendationPerformance> {
  queryCheck(validPeriod(query.period) && audiences.includes(query.audience) && ["offline", "service"].includes(query.dataset ?? "offline"));
  const params = new URLSearchParams({ period: query.period, audience: query.audience });
  if (query.dataset) params.set("dataset", query.dataset);
  const v = await apiRequest<unknown>(`/api/v1/admin/analytics/recommendation-performance?${params}`, { signal }, 200);
  if (!isPerformance(v, query.period, query.audience, query.dataset)) invalid();
  return v;
}
