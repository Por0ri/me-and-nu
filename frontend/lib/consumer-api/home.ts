import {
  mockHomeDataByTopicId,
  mockIntentSectionsByTopicId,
} from "@/mocks/home";
import {
  readMockTopicState,
  setMockHomeIntent,
} from "@/mocks/state";
import { getFeed, getTopics } from "@/lib/api";
import { seedApiContentState } from "@/lib/content-state";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import type {
  GetHomeContentsInput,
  HomeData,
  HomeIntentRequest,
  HomeIntentResponse,
} from "@/types/home";

function requireApiTopicId(value: string): number {
  const id = Number(value);
  if (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(id)) {
    throw new Error("A positive numeric Topic ID is required.");
  }
  return id;
}

async function getApiHomeContents(topicId: string): Promise<HomeData> {
  const numericTopicId = requireApiTopicId(topicId);
  const [catalog, firstPage] = await Promise.all([
    getTopics(),
    getFeed(numericTopicId, { limit: 100 }),
  ]);
  const sections = new Map<string, HomeData["sections"][number]>();
  const seenCursors = new Set<string>();
  let page = firstPage;

  for (;;) {
    if (page.selectedTopicId !== numericTopicId) {
      throw new Error("Feed topic does not match the requested Topic.");
    }
    for (const section of page.sections) {
      const existing = sections.get(section.id);
      const contents = section.contents.map((card) => {
        const contentId = String(card.id);
        seedApiContentState(
          { contentId, topicContext: { topicId } },
          { saved: card.saved, liked: card.myReaction?.liked ?? false },
        );
        return {
          id: contentId,
          title: card.title,
          summary: card.summary ?? "",
          imageUrl: card.imageUrl ?? undefined,
          sourceName: card.sourceName ?? "출처 없음",
          saved: card.saved,
          recommendationReason: card.recommendationReason ?? undefined,
          liked: card.myReaction?.liked ?? false,
        };
      });
      if (existing) {
        existing.contents.push(...contents);
      } else {
        sections.set(section.id, { id: section.id, title: section.title, contents });
      }
    }
    if (!page.nextCursor) break;
    if (seenCursors.has(page.nextCursor)) {
      throw new Error("Feed cursor repeated.");
    }
    seenCursors.add(page.nextCursor);
    page = await getFeed(numericTopicId, { cursor: page.nextCursor, limit: 100 });
  }

  return {
    topics: catalog.topics.map((topic) => ({
      id: String(topic.id), name: topic.name, code: topic.code,
    })),
    selectedTopicId: topicId,
    sections: [...sections.values()],
  };
}

export async function getHomeContents({
  topicId,
}: GetHomeContentsInput): Promise<HomeData> {
  if (isConsumerApiMode) return getApiHomeContents(topicId);
  const homeFixture = mockHomeDataByTopicId[topicId];

  if (!homeFixture) {
    throw new Error(`Unknown Mock topic: ${topicId}`);
  }

  const topicState = readMockTopicState(topicId);

  return {
    topics: structuredClone(homeFixture.topics),
    selectedTopicId: topicId,
    sections: topicState.sections,
    currentIntent: topicState.currentIntent,
  };
}

export async function updateHomeIntent({
  topicId,
  intent,
}: HomeIntentRequest): Promise<HomeIntentResponse> {
  if (isConsumerApiMode) {
    throw new Error("Home intent is not connected to the local API.");
  }
  const updatedSections = mockIntentSectionsByTopicId[topicId];

  if (!updatedSections) {
    throw new Error(`Unknown Mock topic: ${topicId}`);
  }

  const topicState = setMockHomeIntent(topicId, intent, updatedSections);

  return {
    topicId,
    currentIntent: intent,
    sections: topicState.sections,
  };
}
