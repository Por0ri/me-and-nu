/** V1 browser client. The server owns the HttpOnly cookie; only CSRF lives in memory. */
export type AccountType = "consumer" | "creator" | "admin";
export type Preference = "positive" | "negative";
export type DevUser = { id: number; email: string; nickname: string; accountType: AccountType };
export type RegisterInput = { email: string; password: string; nickname: string; role: "consumer" | "creator" };
export type LoginInput = { email: string; password: string };
export type LoginResponse = { message: string; user: DevUser };
export type Session = {
  authenticated: boolean;
  sessionState: "anonymous" | "onboarding_pending" | "restoration_pending" | "active";
  user: { id: number; accountType: AccountType } | null;
  onboardingCompleted: boolean;
  csrfToken: string;
};
export type PolicyType = string;
export type Policy = {
  type: PolicyType;
  policyVersion: string;
  required: boolean;
  text: string;
};
export type PoliciesResponse = {
  consentItems: Policy[];
  aiNotice: string;
  chatRetention: { days: number | null; status: string };
};
export type ConsentInput = { type: PolicyType; policyVersion: string; agreed: boolean };
export type ConsentHistoryResponse = {
  items: Array<{
    type: string; policyVersion: string; agreed: boolean;
    agreedAt: string | null; withdrawnAt: string | null;
  }>;
};
export type ConsentPatchResponse = {
  items: Array<{ type: string; agreed: boolean; updatedAt: string }>;
};
export type Topic = { id: number; code: string; name: string };
export type TopicsResponse = { topics: Topic[] };
export type Subtopic = { subtopicId: number; topicId: number; name: string };
export type SubtopicsResponse = { items: Subtopic[]; nextCursor: string | null };
type OnboardingBase = {
  birthDate: string;
  consents: ConsentInput[];
  nickname: string;
  profileImageId?: null;
};
export type OnboardingInput = OnboardingBase & (
  | { accountType: "consumer"; topicId: number; subtopicIds: number[] }
  | { accountType: "creator"; topicId?: never; subtopicIds?: never }
);
export type OnboardingResponse = {
  user: { id: number; accountType: AccountType };
  onboardingCompleted: boolean;
  activeTopic: (Topic & { subtopicIds: number[] }) | null;
};
export type Profile = {
  userId: number;
  nickname: string;
  birthDate: string | null;
  email: string | null;
  profileImageId: number | null;
  profileImageUrl: string | null;
  accountType: AccountType;
};
export type MyTopic = Topic & { subtopicIds: number[]; status: string; deletedAt: string | null };
export type MyTopicsResponse = { topics: MyTopic[] };
export type MySubtopic = { subtopicId: number; name: string; order: number; subscribedAt: string };
export type MySubtopicsResponse = { items: MySubtopic[]; orderingBasis: string };
export type MyReaction = { liked: boolean; preference: Preference | null; bookmarked?: boolean };
export type FeedCard = {
  kind: string; id: number; topicId: number; recommendationId: number | null;
  title: string; productionType: "human" | "ai" | "hybrid";
  summary: string | null; imageUrl: string | null;
  sourceName: string; sourceUrl: string; publishedAt: string | null;
  saved: boolean; savedItemId: number | null; recommendationReason: string | null;
  subtopicNames?: string[];
  seen: boolean; isPromotional: boolean; notices: ContentNotice[]; myReaction: MyReaction;
};
export type ContentNotice = { code?: string; message?: string; type?: string };
export type FeedResponse = {
  selectedTopicId: number;
  sections: Array<{ id: string; type: string; title: string; subtopicId: number | null; contents: FeedCard[] }>;
  nextCursor: string | null;
  emptyReason: string | null;
};
export type ContentDetail = {
  contentId: number; topicId: number; title: string; sourceUrl: string;
  publisher: string; publishedAt: string | null; displayMode: string;
  productionType: "human" | "ai" | "hybrid";
  excerpt: string | null; body: string | null; contentType: string | null;
  imageUrl: string | null;
  subtopicIds: number[];
  subtopicNames?: string[];
  practicalInfo: { startsAt: string | null; endsAt: string | null; ageLimit: string | null; preparation: string | null; price: string | null };
  notices: ContentNotice[];
  isPromotional: boolean;
  share: { url: string; text: string };
  myReaction: MyReaction & { bookmarked: boolean };
  channel: { channelId: number; name: string; topicId: number } | null;
};
export type PreferenceResponse = { contentId: number; topicId: number; value: Preference };
export type BookmarkResponse = {
  savedItemId: number; contentId: number; topicId: number; tags: string[];
  resurfaceEnabled: boolean; savedAt: string;
};
export type SavedBookmark = {
  savedItemId: number; contentId: number; title: string;
  summary: string | null; imageUrl: string | null; sourceName: string | null;
  topicId: number; tags: string[]; resurfaceEnabled: boolean;
  productionType?: string | null; publishedAt?: string | null; subtopicNames?: string[];
};
export type BookmarksResponse = { items: SavedBookmark[]; nextCursor: string | null };

export type ApiFieldError = { field: string; code: string; message: string };
type ErrorPayload = { code?: string; message?: string; fields?: ApiFieldError[]; requestId?: string };
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
    public readonly fields?: ApiFieldError[],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const apiBaseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const API_UNAUTHORIZED_EVENT = "api:unauthorized";
let csrfToken: string | null = null;

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  return search.size ? `?${search}` : "";
}

async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD" && !csrfToken) await getSession();
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (method !== "GET" && method !== "HEAD" && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init, headers, credentials: "include", cache: "no-store",
  });
  if (response.status === 204) return undefined as T;
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = payload as ErrorPayload | null;
    if (response.status === 401 || error?.code === "CSRF_INVALID") csrfToken = null;
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event(API_UNAUTHORIZED_EVENT));
    }
    throw new ApiError(
      error?.message ?? `요청에 실패했습니다. (HTTP ${response.status})`,
      response.status, error?.code ?? "UNKNOWN_ERROR", error?.fields,
    );
  }
  return payload as T;
}

export async function getSession(): Promise<Session> {
  const session = await apiRequest<Session>("/api/v1/auth/session");
  csrfToken = session.csrfToken;
  return session;
}
export function register(input: RegisterInput): Promise<DevUser> {
  return apiRequest("/api/v1/dev/auth/register", { method: "POST", body: JSON.stringify(input) });
}
export async function login(input: LoginInput): Promise<LoginResponse> {
  const result = await apiRequest<LoginResponse>("/api/v1/dev/auth/login", { method: "POST", body: JSON.stringify(input) });
  csrfToken = null; // Login rotates the HttpOnly session cookie.
  await getSession();
  return result;
}
export async function logout(): Promise<void> {
  await apiRequest<void>("/api/v1/auth/logout", { method: "POST" });
  csrfToken = null;
}
export function getPolicies(): Promise<PoliciesResponse> { return apiRequest("/api/v1/policies"); }
export function getConsents(): Promise<ConsentHistoryResponse> {
  return apiRequest("/api/v1/users/me/consents");
}
export function updateConsents(items: ConsentInput[]): Promise<ConsentPatchResponse> {
  return apiRequest("/api/v1/users/me/consents", { method: "PATCH", body: JSON.stringify({ items }) });
}
export function getTopics(options: { q?: string } = {}): Promise<TopicsResponse> {
  return apiRequest(`/api/v1/topics${query(options)}`);
}
export function getSubtopics(
  topicId: number,
  options: { q?: string; cursor?: string; limit?: number } = {},
): Promise<SubtopicsResponse> {
  return apiRequest(`/api/v1/topics/${topicId}/subtopics${query(options)}`);
}
export function getSubtopic(subtopicId: number): Promise<Subtopic> {
  return apiRequest(`/api/v1/subtopics/${subtopicId}`);
}
export async function onboard(input: OnboardingInput): Promise<OnboardingResponse> {
  const result = await apiRequest<OnboardingResponse>("/api/v1/onboarding", { method: "POST", body: JSON.stringify(input) });
  csrfToken = null; // Onboarding rotates the cookie; the next write reloads its CSRF token.
  return result;
}
export function getProfile(): Promise<Profile> { return apiRequest("/api/v1/users/me"); }
export function updateProfile(input: { nickname: string; profileImageId?: null } | { nickname?: string; profileImageId: null }): Promise<Profile> {
  return apiRequest("/api/v1/users/me", { method: "PATCH", body: JSON.stringify(input) });
}
export function getMyTopics(options: { includeDeleted?: boolean } = {}): Promise<MyTopicsResponse> {
  return apiRequest(`/api/v1/me/topics${query(options)}`);
}
export function addMyTopic(topicId: number, subtopicIds: number[]): Promise<{ topic: MyTopic }> {
  return apiRequest("/api/v1/me/topics", { method: "POST", body: JSON.stringify({ topicId, subtopicIds }) });
}
export function getMySubtopics(topicId: number): Promise<MySubtopicsResponse> {
  return apiRequest(`/api/v1/me/topics/${topicId}/subtopics`);
}
export function subscribeSubtopic(topicId: number, subtopicId: number): Promise<{ topicId: number; subtopicId: number; subscribed: boolean }> {
  return apiRequest(`/api/v1/me/topics/${topicId}/subtopics/${subtopicId}`, { method: "PUT" });
}
export function unsubscribeSubtopic(topicId: number, subtopicId: number): Promise<void> {
  return apiRequest<void>(`/api/v1/me/topics/${topicId}/subtopics/${subtopicId}`, { method: "DELETE" });
}
export function getFeed(
  topicId: number, options: { subtopicId?: number; cursor?: string; limit?: number } = {},
): Promise<FeedResponse> {
  return apiRequest(`/api/v1/topics/${topicId}/feed${query(options)}`);
}
export function getContent(contentId: number, topicId: number): Promise<ContentDetail> {
  return apiRequest(`/api/v1/contents/${contentId}${query({ topicId })}`);
}
export function setPreference(contentId: number, topicId: number, value: Preference): Promise<PreferenceResponse> {
  return apiRequest(`/api/v1/contents/${contentId}/preference`, { method: "PUT", body: JSON.stringify({ topicId, value }) });
}
export function clearPreference(contentId: number, topicId: number): Promise<void> {
  return apiRequest<void>(`/api/v1/contents/${contentId}/preference${query({ topicId })}`, { method: "DELETE" });
}
export function likeContent(contentId: number, topicId: number): Promise<{ contentId: number; liked: boolean }> {
  return apiRequest(`/api/v1/contents/${contentId}/like`, { method: "PUT", body: JSON.stringify({ topicId }) });
}
export function unlikeContent(contentId: number, topicId: number): Promise<void> {
  return apiRequest<void>(`/api/v1/contents/${contentId}/like${query({ topicId })}`, { method: "DELETE" });
}
export function bookmarkContent(contentId: number, topicId: number): Promise<BookmarkResponse> {
  return apiRequest(`/api/v1/contents/${contentId}/bookmark`, { method: "PUT", body: JSON.stringify({ topicId }) });
}
export function unbookmarkContent(contentId: number, topicId: number): Promise<void> {
  return apiRequest<void>(`/api/v1/contents/${contentId}/bookmark${query({ topicId })}`, { method: "DELETE" });
}
export function getBookmarks(
  options: { q?: string; topicId?: number; cursor?: string; limit?: number } = {},
): Promise<BookmarksResponse> {
  return apiRequest(`/api/v1/users/me/bookmarks${query(options)}`);
}
