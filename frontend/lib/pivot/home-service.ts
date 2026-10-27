import { getFeed, getMyTopics, type FeedCard } from "@/lib/api";
import { requirePivotApi } from "./services";
import type { HomeCardModel, HomeDataSource } from "./home";
import { activeTopicId, isPositiveId, LIST_PAGE_SIZE, MAX_LIST_PAGES, nextPageCursor, optionalImage, requireTitle } from "./service-data";

export function mapFeedCard(card: FeedCard, topicId: number): HomeCardModel {
  if (!card || !isPositiveId(card.id) || card.topicId !== topicId || typeof card.saved !== "boolean") {
    throw new Error("Invalid feed identity or bookmark state");
  }
  const id = String(card.id);
  const title = requireTitle(card.title);
  return {
    id, detailContentId: id, topicId, title, saved: card.saved,
    slides: [{ id, order: 0, description: card.summary ?? "", image: optionalImage(card.imageUrl, title) }],
  };
}

export function createServiceHomeSource(
  api: { getMyTopics: typeof getMyTopics; getFeed: typeof getFeed } = { getMyTopics, getFeed },
  guard: () => void = requirePivotApi,
): HomeDataSource {
  return {
    kind: "service",
    async load({ signal }) {
      guard(); signal.throwIfAborted();
      const topicId = activeTopicId(await api.getMyTopics({}, { signal }));
      signal.throwIfAborted();
      const cards: HomeCardModel[] = [];
      if (topicId !== null) {
        let cursor: string | undefined;
        const cursors = new Set<string>();
        const ids = new Set<string>();
        for (let page = 0; page < MAX_LIST_PAGES; page++) {
          const response = await api.getFeed(topicId, { limit: LIST_PAGE_SIZE, cursor }, { signal });
          signal.throwIfAborted();
          if (!response || response.selectedTopicId !== topicId || !Array.isArray(response.sections)) throw new Error("Invalid feed response");
          for (const section of response.sections) {
            if (!section || !Array.isArray(section.contents)) throw new Error("Invalid feed section");
            for (const item of section.contents) {
              const card = mapFeedCard(item, topicId);
              if (ids.has(card.id)) continue;
              ids.add(card.id); cards.push(card);
            }
          }
          cursor = nextPageCursor(response.nextCursor, cursors, page);
          if (cursor === undefined) break;
        }
      }
      return { status: "ready", home: { origin: "service", cards } };
    },
  };
}

export const serviceHomeSource = createServiceHomeSource();
export const pendingHomeSource = serviceHomeSource;
