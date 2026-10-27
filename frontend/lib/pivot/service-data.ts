import type { MyTopicsResponse } from "@/lib/api";
import type { HomeDetailTarget } from "./home";

export function isPositiveId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

export function serviceContentId(target: HomeDetailTarget): number | null {
  if (!isPositiveId(target.topicId) || typeof target.contentId !== "string" ||
    !/^[1-9]\d*$/.test(target.contentId) || target.postId !== target.contentId) return null;
  const id = Number(target.contentId);
  return isPositiveId(id) ? id : null;
}

/** Use the member's active anime Topic when present; never infer a database ID. */
export function activeTopicId(response: MyTopicsResponse): number | null {
  if (!response || !Array.isArray(response.topics)) throw new Error("Invalid Topic response");
  if (response.topics.some(topic => !topic || !isPositiveId(topic.id) || typeof topic.status !== "string")) throw new Error("Invalid Topic identity");
  const active = response.topics.filter((topic) => topic.status === "active" && topic.deletedAt === null);
  if (active.some((topic) => !isPositiveId(topic.id))) throw new Error("Invalid Topic identity");
  return (active.find((topic) => topic.code === "anime") ?? active[0])?.id ?? null;
}

export function optionalImage(value: string | null | undefined, alt: string) {
  if (!value) return undefined;
  if (typeof value !== "string" || value !== value.trim() || /[\u0000-\u0020\\]/.test(value)) return undefined;
  try {
    const url = new URL(value);
    // Service slide routes require identity checks and credentialed blob loading.
    // They must never fall through to the legacy public-image renderer.
    if (/^\/api\/v1\/contents\/[^/]+\/slides\//.test(url.pathname)) return undefined;
    if ((url.protocol === "https:" || url.protocol === "http:") && !url.username && !url.password) {
      return { src: url.href, alt };
    }
  } catch { /* Missing or invalid media uses the existing image placeholder. */ }
  return undefined;
}

export function safeSourceUrl(value: string): string | undefined {
  return optionalImage(value, "")?.src;
}

export function requireTitle(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new Error("Invalid content title");
  return value;
}

/** Bound a complete list load and reject repeated cursors instead of hanging. */
export const MAX_LIST_PAGES = 10;
export const LIST_PAGE_SIZE = 100;

export function nextPageCursor(value: string | null, seen: Set<string>, page: number): string | undefined {
  if (value === null) return undefined;
  if (typeof value !== "string" || !value || seen.has(value) || page + 1 >= MAX_LIST_PAGES) {
    throw new Error("Invalid or excessive content pagination");
  }
  seen.add(value);
  return value;
}
