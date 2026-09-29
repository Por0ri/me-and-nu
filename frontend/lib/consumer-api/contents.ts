import {
  mockContentDetailsByTopicId,
  mockContentsByTopicId,
} from "@/mocks/contents";
import { mockTopics } from "@/mocks/topics";
import {
  readMockTopicState,
  setMockFeedbackState,
  setMockSavedState,
} from "@/mocks/state";
import type {
  ContentDetail,
  ContentFeedbackResult,
  GetContentInput,
  GetSavedContentsInput,
  SaveContentInput,
  SavedContentsData,
  SaveResult,
  SetContentFeedbackInput,
} from "@/types/content";

/** FE Mock의 데이터 없음 구분이며 실제 HTTP 오류 Contract가 아니다. */
export class ContentNotFoundError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ContentNotFoundError";
  }
}

function requireMockContent({
  contentId,
  topicContext,
}: GetContentInput): ContentDetail {
  const topicContents = mockContentDetailsByTopicId[topicContext.topicId];
  const content = topicContents?.find((item) => item.id === contentId);

  if (!content) {
    throw new ContentNotFoundError(
      `Unknown Mock content in ${topicContext.topicId}: ${contentId}`,
    );
  }

  return content;
}

export async function getContent(
  input: GetContentInput,
): Promise<ContentDetail> {
  const content = requireMockContent(input);
  const topicState = readMockTopicState(input.topicContext.topicId);

  return {
    ...structuredClone(content),
    saved: topicState.savedByContentId[input.contentId] ?? content.saved,
    feedback: topicState.feedbackByContentId[input.contentId] ?? null,
  };
}

export async function setContentFeedback(
  input: SetContentFeedbackInput,
): Promise<ContentFeedbackResult> {
  requireMockContent(input);
  setMockFeedbackState(
    input.topicContext.topicId,
    input.contentId,
    input.feedback,
  );

  return {
    contentId: input.contentId,
    feedback: input.feedback,
    topicContext: structuredClone(input.topicContext),
  };
}

async function updateSavedState(
  input: SaveContentInput,
  saved: boolean,
): Promise<SaveResult> {
  requireMockContent(input);
  setMockSavedState(input.topicContext.topicId, input.contentId, saved);

  return {
    contentId: input.contentId,
    saved,
    topicContext: structuredClone(input.topicContext),
  };
}

export async function saveContent(
  input: SaveContentInput,
): Promise<SaveResult> {
  return updateSavedState(input, true);
}

export async function getSavedContents({
  topicId,
}: GetSavedContentsInput): Promise<SavedContentsData> {
  const topic = mockTopics.find((item) => item.id === topicId);

  if (!topic) {
    throw new Error(`Unknown Mock topic: ${topicId}`);
  }

  const topicState = readMockTopicState(topicId);
  // Home 노출 여부가 아니라 전체 Fixture와 저장 Map을 기준으로 조회한다.
  const contents = mockContentsByTopicId[topicId]
    .filter((content) => topicState.savedByContentId[content.id] === true)
    .map((content) => ({ ...content, saved: true }));

  return structuredClone({ topic, contents });
}

export async function unsaveContent(
  input: SaveContentInput,
): Promise<SaveResult> {
  return updateSavedState(input, false);
}
