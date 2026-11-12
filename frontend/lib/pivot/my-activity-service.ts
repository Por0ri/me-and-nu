import { getMyLikes, getMyRecentViews, type LikedContent, type ViewedContent, type Session } from "@/lib/api";
import { isActiveSession, requirePivotApi } from "./services";
import { isPositiveId, requireTitle } from "./service-data";
import { mapCoverImage } from "./carousel-data";
import type { ActivityCard, ActivityDataSource, ActivityTab } from "./my-activity";

export function mapActivityCard(item: LikedContent | ViewedContent, tab: ActivityTab): ActivityCard {
  if (!item || !isPositiveId(item.contentId) || !isPositiveId(item.topicId)) throw new Error("Invalid activity identity");
  const title = requireTitle(item.title);
  const timestamp = tab === "likes" ? (item as LikedContent).likedAt : (item as ViewedContent).viewedAt;
  if (typeof timestamp !== "string" || !Number.isFinite(Date.parse(timestamp))) throw new Error("Invalid activity timestamp");
  return {
    postId: String(item.contentId), contentId: String(item.contentId), topicId: item.topicId, title,
    image: mapCoverImage(item.imageUrl, title, item.contentId, item.topicId),
    summary: typeof item.summary === "string" ? item.summary : undefined,
    sourceName: typeof item.sourceName === "string" ? item.sourceName : undefined,
    // Preserve HOME's page-one editorial copy; source and taxonomy are not labels.
    eyebrow: typeof item.eyebrow === "string" && item.eyebrow.trim() ? item.eyebrow : undefined,
  };
}

export function createServiceActivitySource(
  readSession: () => Session | null,
  api = { getMyLikes, getMyRecentViews },
  guard: () => void = requirePivotApi,
): ActivityDataSource {
  const owner = readSession(), userId = owner?.user?.id;
  function assertCurrent() {
    if (!isActiveSession(owner) || readSession() !== owner || owner?.user?.id !== userId) throw new Error("Activity session changed");
  }
  return {
    kind: "service", assertCurrent,
    async load(tab, { cursor, signal }) {
      guard(); signal.throwIfAborted(); assertCurrent();
      // MY activity spans Topics. Preserve each card's actual Topic for Detail.
      const response = await (tab === "likes" ? api.getMyLikes : api.getMyRecentViews)({ cursor, limit: 20 }, { signal });
      signal.throwIfAborted(); assertCurrent();
      if (!response || !Array.isArray(response.items)) throw new Error("Invalid activity response");
      return { origin: "service", cards: response.items.map(item => mapActivityCard(item, tab)), nextCursor: response.nextCursor };
    },
  };
}
