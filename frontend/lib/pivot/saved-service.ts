import { getBookmarks, getMyTopics, type SavedBookmark } from "@/lib/api";
import { requirePivotApi } from "./services";
import type { SavedCard, SavedDataSource } from "./saved";
import { activeTopicId, isPositiveId, LIST_PAGE_SIZE, MAX_LIST_PAGES, nextPageCursor, optionalImage, requireTitle } from "./service-data";

export function mapSavedCard(item: SavedBookmark, topicId: number): SavedCard {
  if (!item || !isPositiveId(item.contentId) || item.topicId !== topicId) throw new Error("Invalid saved identity");
  const title = requireTitle(item.title);
  return { postId: String(item.contentId), contentId: String(item.contentId), topicId, title, image: optionalImage(item.imageUrl, title) };
}

export function createServiceSavedSource(
  api: { getMyTopics: typeof getMyTopics; getBookmarks: typeof getBookmarks } = { getMyTopics, getBookmarks },
  guard: () => void = requirePivotApi,
): SavedDataSource {
  return {
    kind: "service",
    async load({ signal }) {
      guard(); signal.throwIfAborted();
      const topicId = activeTopicId(await api.getMyTopics({}, { signal }));
      signal.throwIfAborted();
      const cards: SavedCard[] = [];
      if (topicId !== null) {
        let cursor: string | undefined;
        const cursors = new Set<string>();
        const ids = new Set<string>();
        for (let page = 0; page < MAX_LIST_PAGES; page++) {
          const response = await api.getBookmarks({ topicId, limit: LIST_PAGE_SIZE, cursor }, { signal });
          signal.throwIfAborted();
          if (!response || !Array.isArray(response.items)) throw new Error("Invalid saved response");
          for (const item of response.items) {
            const card = mapSavedCard(item, topicId);
            if (ids.has(card.contentId)) continue;
            ids.add(card.contentId); cards.push(card);
          }
          cursor = nextPageCursor(response.nextCursor, cursors, page);
          if (cursor === undefined) break;
        }
      }
      return { status: "ready", saved: { origin: "service", cards } };
    },
  };
}

export const serviceSavedSource = createServiceSavedSource();
export const pendingSavedSource = serviceSavedSource;
