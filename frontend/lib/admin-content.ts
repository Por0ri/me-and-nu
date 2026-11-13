import { apiRequest, ApiError } from "@/lib/api";

// Mirrors app/schemas/admin_content.py. These are stored values, not a review workflow.
export const judgmentStatuses = ["pending", "confirmed", "needs_review", "failed"] as const;
export const contentStatuses = ["active", "hidden", "pending_review", "deleted"] as const;
export type JudgmentStatus = typeof judgmentStatuses[number];
export type ContentStatus = typeof contentStatuses[number];
export type AdminContentQuery = { mode?: "all" | "review"; q: string; judgmentStatus: JudgmentStatus | ""; status: ContentStatus | ""; page: number; pageSize: number };
export type AdminContentItem = {
  contentId: number; title: string; judgmentStatus: JudgmentStatus; status: ContentStatus;
  createdAt: string; publishedAt: string | null;
};
export type AdminContentPage = { items: AdminContentItem[]; page: number; pageSize: number; total: number };
export type AdminContentDetail = AdminContentItem & {
  summary: string | null; body: string | null;
  sources: { url: string; title: string | null; publisher: string | null; role: string; citationOrder: number | null }[];
};
export type AdminFailure = { status: "login-required" | "forbidden" | "unavailable" | "not-found" | "error"; message: string };
export type AdminReadState<T> = { status: "loading" } | { status: "ready"; data: T } | AdminFailure;

export function parseAdminQuery(params: Pick<URLSearchParams, "get">): AdminContentQuery {
  const mode = params.get("mode") ?? "all";
  const q = (params.get("q") ?? "").trim();
  const judgmentStatus = params.get("judgmentStatus") ?? "";
  const status = params.get("status") ?? "";
  const number = (name: string, fallback: number, max: number) => {
    const value = params.get(name);
    if (value === null) return fallback;
    if (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) > max) throw new Error("조회 조건을 확인해 주세요.");
    return Number(value);
  };
  if (!["all", "review"].includes(mode) || q.length > 300 || (judgmentStatus && !judgmentStatuses.includes(judgmentStatus as JudgmentStatus)) ||
    (status && !contentStatuses.includes(status as ContentStatus))) throw new Error("조회 조건을 확인해 주세요.");
  return { mode: mode as AdminContentQuery["mode"], q, judgmentStatus: judgmentStatus as AdminContentQuery["judgmentStatus"], status: status as AdminContentQuery["status"],
    page: number("page", 1, 1_000_000), pageSize: number("pageSize", 20, 100) };
}

export function adminQueryString(query: AdminContentQuery): string {
  const params = new URLSearchParams();
  if (query.mode === "review") params.set("mode", "review");
  if (query.q) params.set("q", query.q);
  if (query.judgmentStatus) params.set("judgmentStatus", query.judgmentStatus);
  if (query.status) params.set("status", query.status);
  params.set("page", String(query.page)); params.set("pageSize", String(query.pageSize));
  return params.toString();
}

function invalid(): never { throw new ApiError("콘텐츠 응답을 확인하지 못했습니다.", 200, "INVALID_ADMIN_RESPONSE"); }
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function integer(value: unknown, min: number): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value >= min; }
function timestamp(value: unknown): value is string { return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value)); }
function nullableText(value: unknown): value is string | null { return value === null || typeof value === "string"; }
function isItem(value: unknown): value is AdminContentItem {
  return record(value) && integer(value.contentId, 1) && typeof value.title === "string" &&
    judgmentStatuses.includes(value.judgmentStatus as JudgmentStatus) && contentStatuses.includes(value.status as ContentStatus) &&
    timestamp(value.createdAt) && (value.publishedAt === null || timestamp(value.publishedAt));
}

export async function loadAdminContents(query: AdminContentQuery, signal?: AbortSignal): Promise<AdminContentPage> {
  const value = await apiRequest<unknown>(`/api/v1/admin/contents?${adminQueryString(query)}`, { signal });
  if (!record(value) || value.page !== query.page || value.pageSize !== query.pageSize || !integer(value.total, 0) ||
    !Array.isArray(value.items) || !value.items.every(isItem) ||
    (query.mode === "review" && value.items.some(item => item.judgmentStatus !== "needs_review")) || value.items.length > query.pageSize ||
    value.items.length > value.total || new Set(value.items.map(item => item.contentId)).size !== value.items.length) invalid();
  return value as AdminContentPage;
}

export async function loadAdminContent(contentId: number, signal?: AbortSignal): Promise<AdminContentDetail> {
  if (!integer(contentId, 1)) invalid();
  const value = await apiRequest<unknown>(`/api/v1/admin/contents/${contentId}`, { signal });
  if (!isItem(value) || value.contentId !== contentId) invalid();
  const detail = value as unknown as Record<string, unknown>;
  if (!nullableText(detail.summary) || !nullableText(detail.body) || !Array.isArray(detail.sources) ||
    !detail.sources.every(source => record(source) && typeof source.url === "string" && nullableText(source.title) &&
      nullableText(source.publisher) && typeof source.role === "string" &&
      (source.citationOrder === null || integer(source.citationOrder, 1)))) invalid();
  return value as AdminContentDetail;
}

export function adminFailure(error: unknown): AdminFailure {
  if (error instanceof ApiError) {
    if (error.status === 401) return { status: "login-required", message: "로그인이 필요합니다. 로그인 후 이 페이지를 다시 열어 주세요." };
    if (error.status === 403) return { status: "forbidden", message: "이 콘텐츠를 조회할 관리자 권한이 없습니다." };
    if (error.status === 404) return error.code === "CONTENT_NOT_FOUND"
      ? { status: "not-found", message: "콘텐츠를 찾을 수 없습니다." }
      : { status: "unavailable", message: "관리자 콘텐츠 조회를 준비 중입니다. 접근 권한 연결 후 이용할 수 있습니다." };
  }
  return { status: "error", message: "콘텐츠를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요." };
}

export function safeSourceUrl(value: string): string | undefined {
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined; }
  catch { return undefined; }
}

export function adminDate(value: string | null): string {
  return value ? new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(value)) : "—";
}
