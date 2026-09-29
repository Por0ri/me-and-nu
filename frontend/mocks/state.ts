import { mockContentDetailsByTopicId } from "@/mocks/contents";
import { mockHomeDataByTopicId } from "@/mocks/home";
import type { ContentFeedback } from "@/types/content";
import type { HomeSection } from "@/types/home";

export type MockTopicRuntimeState = {
  currentIntent?: string;
  sections: HomeSection[];
  savedByContentId: Record<string, boolean>;
  feedbackByContentId: Record<string, ContentFeedback | null>;
};

function clone<T>(value: T): T {
  return structuredClone(value);
}

function createInitialTopicState(): Record<string, MockTopicRuntimeState> {
  return Object.fromEntries(
    Object.entries(mockHomeDataByTopicId).map(([topicId, homeData]) => [
      topicId,
      {
        currentIntent: homeData.currentIntent,
        sections: clone(homeData.sections),
        savedByContentId: Object.fromEntries(
          homeData.sections.flatMap((section) =>
            section.contents.map((content) => [content.id, content.saved]),
          ),
        ),
        feedbackByContentId: Object.fromEntries(
          (mockContentDetailsByTopicId[topicId] ?? []).map((content) => [
            content.id,
            content.feedback,
          ]),
        ),
      },
    ]),
  );
}

/**
 * 개발 중 Topic 상태 분리를 확인하기 위한 process-local Mock 상태다.
 * 사용자별 영속 저장소나 실제 Backend 상태로 사용하지 않는다.
 */
let topicStateById = createInitialTopicState();

function requireTopicState(topicId: string): MockTopicRuntimeState {
  const topicState = topicStateById[topicId];

  if (!topicState) {
    throw new Error(`Unknown Mock topic: ${topicId}`);
  }

  return topicState;
}

function applySavedState(
  sections: HomeSection[],
  savedByContentId: Record<string, boolean>,
): HomeSection[] {
  return sections.map((section) => ({
    ...section,
    contents: section.contents.map((content) => ({
      ...content,
      saved: savedByContentId[content.id] ?? content.saved,
    })),
  }));
}

export function readMockTopicState(topicId: string): MockTopicRuntimeState {
  return clone(requireTopicState(topicId));
}

export function setMockHomeIntent(
  topicId: string,
  currentIntent: string,
  sections: HomeSection[],
): MockTopicRuntimeState {
  const topicState = requireTopicState(topicId);

  topicState.currentIntent = currentIntent;
  topicState.sections = applySavedState(
    clone(sections),
    topicState.savedByContentId,
  );

  return clone(topicState);
}

export function setMockSavedState(
  topicId: string,
  contentId: string,
  saved: boolean,
): MockTopicRuntimeState {
  const topicState = requireTopicState(topicId);

  if (!(contentId in topicState.savedByContentId)) {
    throw new Error(`Unknown Mock content in ${topicId}: ${contentId}`);
  }

  topicState.savedByContentId[contentId] = saved;
  topicState.sections = applySavedState(
    topicState.sections,
    topicState.savedByContentId,
  );

  return clone(topicState);
}

/** 최신 선택만 보관한다. Save / Home / 장기 Preference는 변경하지 않는다. */
export function setMockFeedbackState(
  topicId: string,
  contentId: string,
  feedback: ContentFeedback,
): MockTopicRuntimeState {
  const topicState = requireTopicState(topicId);

  if (!Object.hasOwn(topicState.feedbackByContentId, contentId)) {
    throw new Error(`Unknown Mock content in ${topicId}: ${contentId}`);
  }

  if (feedback !== "O" && feedback !== "X") {
    throw new Error("Mock feedback must be O or X.");
  }

  if (topicState.feedbackByContentId[contentId] !== feedback) {
    topicState.feedbackByContentId[contentId] = feedback;
  }

  return clone(topicState);
}

export function resetMockRuntimeState(): void {
  topicStateById = createInitialTopicState();
}
