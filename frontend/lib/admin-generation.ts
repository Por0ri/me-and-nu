import { ApiError, apiRequest } from "@/lib/api";

export type GenerationAvailability = "available" | "partial" | "stale" | "unavailable";
export type GenerationDaily = {
  attempts: number | null; maxAttempts: 40 | null; passes: number | null; successTarget: 20;
  remainingAttempts: number | null; remainingPasses: number | null;
  pendingRecords: number | null; duplicateRecords: number | null;
  knownEstimatedCostUsd: string | null; totalEstimatedCostUsd: string | null; reservedCostUsd: string | null;
  unknownCostJobs: number | null; inflightCostJobs: number | null; articleAttempts: number | null; ragAttempts: number | null;
};
export type GenerationHistory = {
  knownEstimatedCostUsd: string | null; baselineEstimatedCostUsd: string | null; totalEstimatedCostUsd: string | null;
  unknownCostJobs: number | null;
};
export const generationJobStates = ["queued", "running", "succeeded", "failed", "needsAttention", "cancelled", "held", "rejected", "skipped", "readyForStaging"] as const;
export type GenerationJobs = Record<typeof generationJobStates[number], number | null>;
export type GenerationSnapshot = {
  availability: GenerationAvailability; reason: string | null; observedAt: string;
  generatedAt: string | null; ageSeconds: number | null; staleAfterSeconds: 180;
  timezone: "Asia/Seoul"; policyDate: string | null;
  daily: GenerationDaily | null; history: GenerationHistory | null;
  jobs: { article: GenerationJobs; rag: GenerationJobs } | null;
  broker: { articleQueued: number | null; ragQueued: number | null; unacked: number | null; unackedIndex: number | null } | null;
  runtime: {
    controller: "active" | "activating" | "inactive" | "failed" | "unknown";
    timer: "active" | "inactive" | "failed" | "unknown";
    articleWorker: "running" | "stopped" | "missing" | "unknown";
    ragWorker: "running" | "stopped" | "missing" | "unknown";
  } | null;
};

type ObjectValue = Record<string, unknown>;
const record = (value: unknown): value is ObjectValue => !!value && typeof value === "object" && !Array.isArray(value);
const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= 1_000_000_000;
const maybeCount = (value: unknown) => value === null || count(value);
const money = (value: unknown) => value === null || (typeof value === "string" && /^(0|[1-9]\d{0,11})(?:\.\d{1,12})?$/.test(value));
const timestamp = (value: unknown): value is string => typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const maybeTimestamp = (value: unknown) => value === null || timestamp(value);
const oneOf = (value: unknown, values: string[]) => typeof value === "string" && values.includes(value);
function validDay(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}
function daily(value: unknown): boolean {
  if (value === null) return true;
  if (!record(value) || (value.maxAttempts !== 40 && value.maxAttempts !== null) || value.successTarget !== 20) return false;
  if (!["attempts", "passes", "remainingAttempts", "remainingPasses", "pendingRecords", "duplicateRecords", "unknownCostJobs", "inflightCostJobs", "articleAttempts", "ragAttempts"].every(key => maybeCount(value[key])) ||
      !["knownEstimatedCostUsd", "totalEstimatedCostUsd", "reservedCostUsd"].every(key => money(value[key]))) return false;
  return (value.maxAttempts === null || value.attempts === null ? value.remainingAttempts === null : Number(value.attempts) <= value.maxAttempts && value.remainingAttempts === value.maxAttempts - Number(value.attempts)) &&
    (value.passes === null ? value.remainingPasses === null : Number(value.passes) <= 20 && value.remainingPasses === 20 - Number(value.passes)) &&
    (value.attempts === null || value.passes === null || Number(value.passes) <= Number(value.attempts)) &&
    ([value.attempts, value.articleAttempts, value.ragAttempts].includes(null) || Number(value.articleAttempts) + Number(value.ragAttempts) === value.attempts) &&
    validTotal(value.knownEstimatedCostUsd, value.totalEstimatedCostUsd, value.unknownCostJobs, value.inflightCostJobs);
}
function validTotal(known: unknown, total: unknown, unknown: unknown, inflight: unknown = 0): boolean {
  const normalize = (value: string) => { const [integer, fraction = ""] = value.split("."); return `${integer}.${fraction.replace(/0+$/, "")}`; };
  return total === null || (typeof known === "string" && typeof total === "string" && unknown === 0 && inflight === 0 && normalize(total) === normalize(known));
}
function history(value: unknown): boolean {
  return value === null || (record(value) && maybeCount(value.unknownCostJobs) &&
    ["knownEstimatedCostUsd", "baselineEstimatedCostUsd", "totalEstimatedCostUsd"].every(key => money(value[key])) &&
    validTotal(value.knownEstimatedCostUsd, value.totalEstimatedCostUsd, value.unknownCostJobs));
}
function jobs(value: unknown): boolean {
  return value === null || (record(value) && [value.article, value.rag].every(row => record(row) && generationJobStates.every(key => maybeCount(row[key]))));
}
function broker(value: unknown): boolean {
  return value === null || (record(value) && ["articleQueued", "ragQueued", "unacked", "unackedIndex"].every(key => maybeCount(value[key])));
}
function runtime(value: unknown): boolean {
  return value === null || (record(value) && oneOf(value.controller, ["active", "activating", "inactive", "failed", "unknown"]) &&
    oneOf(value.timer, ["active", "inactive", "failed", "unknown"]) &&
    [value.articleWorker, value.ragWorker].every(worker => oneOf(worker, ["running", "stopped", "missing", "unknown"])));
}
export function parseGenerationSnapshot(value: unknown): GenerationSnapshot {
  if (!record(value) || !oneOf(value.availability, ["available", "partial", "stale", "unavailable"]) ||
      !(value.reason === null || oneOf(value.reason, ["not_configured", "snapshot_missing", "snapshot_unreadable", "snapshot_invalid", "snapshot_future", "snapshot_stale", "policy_day_mismatch", "source_partial", "source_unavailable"])) ||
      !timestamp(value.observedAt) || !maybeTimestamp(value.generatedAt) || !maybeCount(value.ageSeconds) || value.staleAfterSeconds !== 180 ||
      value.timezone !== "Asia/Seoul" || !(value.policyDate === null || validDay(value.policyDate)) ||
      !daily(value.daily) || !history(value.history) || !jobs(value.jobs) || !broker(value.broker) || !runtime(value.runtime) ||
      ((value.reason === "policy_day_mismatch" || value.policyDate === null) && (value.daily !== null || value.jobs !== null)) ||
      (value.availability === "unavailable" && [value.daily, value.history, value.jobs, value.broker, value.runtime].some(section => section !== null))) {
    throw new ApiError("자동 생성 현황을 확인하지 못했습니다. 다시 조회해 주세요.", 200, "INVALID_ADMIN_RESPONSE");
  }
  return value as GenerationSnapshot;
}
export async function loadAdminGeneration(signal?: AbortSignal): Promise<GenerationSnapshot> {
  return parseGenerationSnapshot(await apiRequest<unknown>("/api/v1/admin/generation", { signal }, 200));
}

export function generationCount(value: number | null | undefined): string { return value == null ? "확인 불가" : value.toLocaleString("ko-KR"); }
export function generationUsd(value: string | null | undefined): string {
  if (value == null) return "확인 불가";
  const [integer, fraction = ""] = value.split(".");
  return `$${Number(integer).toLocaleString("en-US")}.${fraction.replace(/0+$/, "").padEnd(4, "0")}`;
}
export function generationAge(seconds: number | null): string {
  if (seconds === null) return "수집 시각 확인 불가";
  if (seconds < 60) return `${seconds}초 전 수집`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전 수집`;
  return `${Math.floor(seconds / 3600)}시간 ${Math.floor(seconds % 3600 / 60)}분 전 수집`;
}
