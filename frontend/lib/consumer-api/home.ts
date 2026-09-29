import {
  mockHomeDataByTopicId,
  mockIntentSectionsByTopicId,
} from "@/mocks/home";
import {
  readMockTopicState,
  setMockHomeIntent,
} from "@/mocks/state";
import type {
  GetHomeContentsInput,
  HomeData,
  HomeIntentRequest,
  HomeIntentResponse,
} from "@/types/home";

export async function getHomeContents({
  topicId,
}: GetHomeContentsInput): Promise<HomeData> {
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
