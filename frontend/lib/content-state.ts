import {
  readMockTopicState,
  setMockLikedState,
  subscribeMockContentState,
} from "@/mocks/state";
import type { GetContentInput } from "@/types/content";

// FE Memory Mock access only; existing API signatures and Content types stay intact.
export const subscribeContentState = subscribeMockContentState;

export function readContentSavedState(input: GetContentInput): boolean {
  return readMockTopicState(input.topicContext.topicId)
    .savedByContentId[input.contentId] ?? false;
}

export function readContentLikedState(input: GetContentInput): boolean {
  return readMockTopicState(input.topicContext.topicId)
    .likedByContentId[input.contentId] ?? false;
}

export function toggleContentLikedState(input: GetContentInput): void {
  setMockLikedState(
    input.topicContext.topicId,
    input.contentId,
    !readContentLikedState(input),
  );
}
