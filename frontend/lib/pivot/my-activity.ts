import type { SavedCard } from "./saved";
import { serviceContentId } from "./service-data";

export type ActivityTab = "likes" | "recent-views";
export type ActivityCard = SavedCard & { summary?: string; sourceName?: string };
export type ActivityPage = { origin: "service"; cards: readonly ActivityCard[]; nextCursor: string | null };
export type ActivityState =
  | { status: "loading" }
  | { status: "error" | "unconfigured"; message: string }
  | { status: "ready"; page: ActivityPage; loadingMore: boolean; moreError?: string };
export interface ActivityDataSource {
  readonly kind: "service";
  assertCurrent(): void;
  load(tab: ActivityTab, options: { cursor?: string; signal: AbortSignal }): Promise<ActivityPage>;
}
export const activityTab = (value: string | null): ActivityTab => value === "recent-views" ? value : "likes";
const failure = "활동 목록을 불러오지 못했어요. 다시 시도해주세요.";

/** One cursor chain per tab/session. A failed page never discards already loaded cards. */
export function createActivityRequestController(source: ActivityDataSource, tab: ActivityTab, publish: (state: ActivityState) => void) {
  let active: AbortController | undefined;
  let page: ActivityPage | undefined;
  const cursors = new Set<string>();
  async function request(more: boolean) {
    if (more && (active || !page?.nextCursor)) return;
    active?.abort();
    const current = new AbortController(); active = current;
    if (!more) { page = undefined; cursors.clear(); }
    const previous = page;
    publish(previous ? { status: "ready", page: previous, loadingMore: true } : { status: "loading" });
    try {
      source.assertCurrent();
      const result = await source.load(tab, { cursor: previous?.nextCursor ?? undefined, signal: current.signal });
      current.signal.throwIfAborted(); source.assertCurrent();
      if (result.origin !== "service" || !Array.isArray(result.cards)
        || result.cards.some(card => !card || serviceContentId(card) === null || typeof card.title !== "string" || !card.title.trim())
        || (result.nextCursor !== null && (typeof result.nextCursor !== "string" || !result.nextCursor.trim()
          || result.nextCursor === previous?.nextCursor || cursors.has(result.nextCursor)))) throw new Error("Invalid activity page");
      const cards = new Map((previous?.cards ?? []).map(card => [`${card.topicId}:${card.contentId}`, card]));
      for (const card of result.cards) {
        const id = `${card.topicId}:${card.contentId}`;
        if (!cards.has(id)) cards.set(id, card);
      }
      if (previous?.nextCursor) cursors.add(previous.nextCursor);
      page = { origin: "service", cards: [...cards.values()], nextCursor: result.nextCursor };
      publish({ status: "ready", page, loadingMore: false });
    } catch {
      if (current.signal.aborted) return;
      // A changed owner must also clear cards retained before a failed pagination request.
      try { source.assertCurrent(); } catch { page = undefined; publish({ status: "loading" }); return; }
      publish(previous ? { status: "ready", page: previous, loadingMore: false, moreError: failure }
        : { status: "error", message: failure });
    } finally {
      if (active === current) active = undefined;
    }
  }
  return { load: () => request(false), loadMore: () => request(true), cancel() { active?.abort(); active = undefined; } };
}
