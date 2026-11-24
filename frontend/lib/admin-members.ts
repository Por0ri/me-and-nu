import type { AdminFailure } from "./admin-content";
import { adminReadRequest, decimalId, integer, invalidAdminResponse, nullableText, nullableTimestamp, privateSearchHeaders, record, textArray, timestamp } from "./admin-read-contract";

// Stored account states. A dormant state is not collected by the current service.
export const memberStatuses = { active: "활성", warned: "경고", suspended: "일시 정지", banned: "이용 금지", withdrawn: "탈퇴" } as const;
export const memberProviders = { local: "이메일", kakao: "카카오", naver: "네이버", apple: "Apple" } as const;
export type MemberStatus = keyof typeof memberStatuses;
export type MemberQuery = { q: string; status: MemberStatus | ""; provider: keyof typeof memberProviders | ""; sort: "recent" | "oldest"; page: number };
export const memberPageSize = 5;
export type MemberSummary = {
  id: string; nickname: string; maskedEmail: string | null; provider: keyof typeof memberProviders | null;
  domain: string | null; joinedAt: string | null; lastLoginAt: string | null; status: MemberStatus | null;
};
export type NicknameChange = { changedAt: string; before: string; after: string };
export type MemberDetail = MemberSummary & {
  history: NicknameChange[] | null;
  onboarding: { works: string[]; genres: string[]; themes: string[]; contentTypes: string[] } | null;
  notes: { id: string; text: string; author: string; createdAt: string }[] | null;
  savedCount: number | null; likedCount: number | null;
};
export type MemberPage = { items: MemberSummary[]; total: number; page: number; pageSize: number };
export const memberUnavailable: AdminFailure = { status: "unavailable", message: "관리자 회원 조회는 준비 중입니다. 조회 권한과 데이터 연결이 완료되면 이용할 수 있습니다." };

export function parseMemberQuery(params: Pick<URLSearchParams, "get">, search = ""): MemberQuery {
  const status = params.get("status") ?? "", provider = params.get("provider") ?? "", sort = params.get("sort") ?? "recent", page = params.get("page") ?? "1";
  if ((status !== "" && !Object.hasOwn(memberStatuses, status)) || (provider !== "" && !Object.hasOwn(memberProviders, provider)) ||
      !["recent", "oldest"].includes(sort) || !/^[1-9]\d*$/.test(page) || Number(page) > 1_000_000 || search.length > 300) throw new Error("조회 조건을 확인해 주세요.");
  return { q: search.trim(), status: status as MemberQuery["status"], provider: provider as MemberQuery["provider"], sort: sort as MemberQuery["sort"], page: Number(page) };
}

export function memberQueryString(query: MemberQuery): string {
  // Search may contain PII. Keep it in memory, never in URLs, storage or logs.
  const params = new URLSearchParams();
  if (query.status) params.set("status", query.status);
  if (query.provider) params.set("provider", query.provider);
  if (query.sort !== "recent") params.set("sort", query.sort);
  params.set("page", String(query.page));
  return params.toString();
}

export function memberDate(value: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "—";
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)).replaceAll("-", ".");
}

export function privateEmail(value: string | null): string {
  // Accept already-masked presentation text only; fail closed for raw addresses.
  return value && /^[^@\s]{1,40}\*{3,}@[^@\s]+\.[^@\s]+$/.test(value) ? value : "—";
}

function memberSummary(value: unknown): value is MemberSummary & Record<string, unknown> {
  if (!record(value) || !decimalId(value.id) || typeof value.nickname !== "string" ||
    !nullableText(value.maskedEmail) || (value.maskedEmail !== null && privateEmail(value.maskedEmail) === "—") ||
    (value.provider !== null && (typeof value.provider !== "string" || !Object.hasOwn(memberProviders, value.provider))) ||
    !nullableText(value.domain) || !nullableTimestamp(value.joinedAt) || !nullableTimestamp(value.lastLoginAt) ||
    typeof value.status !== "string" || !Object.hasOwn(memberStatuses, value.status)) return false;
  return value.status !== "withdrawn" || (value.nickname === "탈퇴 회원" &&
    [value.maskedEmail, value.provider, value.domain, value.joinedAt, value.lastLoginAt].every(item => item === null));
}

export async function loadAdminMembers(query: MemberQuery, signal?: AbortSignal): Promise<MemberPage> {
  parseMemberQuery(new URLSearchParams(memberQueryString(query)), query.q);
  const params = new URLSearchParams(memberQueryString(query)); params.set("pageSize", String(memberPageSize));
  const value = await adminReadRequest(`/api/v1/admin/members?${params}`, { signal, headers: privateSearchHeaders(query.q) });
  if (!record(value) || value.page !== query.page || value.pageSize !== memberPageSize || !integer(value.total) ||
    !Array.isArray(value.items) || !value.items.every(memberSummary) || value.items.length > memberPageSize || value.items.length > value.total ||
    new Set(value.items.map(item => item.id)).size !== value.items.length ||
    value.items.some(item => (query.status && item.status !== query.status) || (query.provider && item.status !== "withdrawn" && item.provider !== query.provider))) invalidAdminResponse();
  return value as MemberPage;
}

export async function loadAdminMember(memberId: string, signal?: AbortSignal): Promise<MemberDetail> {
  if (!decimalId(memberId)) invalidAdminResponse();
  const value = await adminReadRequest(`/api/v1/admin/members/${memberId}`, { signal });
  if (!record(value) || !memberSummary(value) || value.id !== memberId ||
    !(value.history === null || (Array.isArray(value.history) && value.history.every(item => record(item) && timestamp(item.changedAt) && typeof item.before === "string" && typeof item.after === "string"))) ||
    !(value.notes === null || (Array.isArray(value.notes) && value.notes.every(item => record(item) && typeof item.id === "string" && typeof item.text === "string" && typeof item.author === "string" && timestamp(item.createdAt)))) ||
    !(value.onboarding === null || (record(value.onboarding) && ["works", "genres", "themes", "contentTypes"].every(key => textArray((value.onboarding as Record<string, unknown>)[key])))) ||
    !(value.savedCount === null || integer(value.savedCount)) || !(value.likedCount === null || integer(value.likedCount)) ||
    (value.status === "withdrawn" && [value.history, value.notes, value.onboarding, value.savedCount, value.likedCount].some(item => item !== null))) invalidAdminResponse();
  return value as MemberDetail;
}
