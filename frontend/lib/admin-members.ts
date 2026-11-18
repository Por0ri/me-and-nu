import type { AdminFailure } from "./admin-content";

// Presentation models only: these labels are NOT a backend contract or DB enums.
// No member endpoint/permission is inferred from /users/me, auth, or content access.
export const memberStatuses = { active: "활성", dormant: "휴면", withdrawn: "탈퇴", restricted: "제한" } as const;
export const memberProviders = { kakao: "카카오", naver: "네이버" } as const;
export type MemberStatus = keyof typeof memberStatuses;
export type MemberQuery = { q: string; status: MemberStatus | ""; provider: keyof typeof memberProviders | ""; sort: "recent" | "oldest"; page: number };
export const memberPageSize = 5;
export type MemberSummary = {
  id: string; nickname: string; maskedEmail: string | null; provider: keyof typeof memberProviders | null;
  domain: string | null; joinedAt: string | null; lastSeenAt: string | null; status: MemberStatus | null;
};
export type NicknameChange = { changedAt: string; before: string; after: string };
export type MemberDetail = MemberSummary & {
  history: NicknameChange[] | null;
  onboarding: { works: string[]; genres: string[]; themes: string[]; contentTypes: string[] } | null;
  notes: { id: string; text: string; author: string; createdAt: string }[] | null;
  savedCount: number | null; likedCount: number | null;
};
export type MemberPage = { items: MemberSummary[]; total: number };
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
