import {
  readMockTopicState,
  setMockLikedState,
  subscribeMockContentState,
} from "@/mocks/state";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import type { GetContentInput } from "@/types/content";

type ApiReactionState = { saved?: boolean; liked?: boolean };
const apiReactionStates = new Map<string, ApiReactionState>();
const apiListeners = new Set<() => void>();

function stateKey(input: GetContentInput): string {
  return `${input.topicContext.topicId}:${input.contentId}`;
}

function notifyApiState() {
  apiListeners.forEach((listener) => listener());
}

export function clearApiContentState(): void {
  if (!isConsumerApiMode || apiReactionStates.size === 0) return;
  apiReactionStates.clear();
  notifyApiState();
}

export function subscribeContentState(listener: () => void): () => void {
  if (!isConsumerApiMode) return subscribeMockContentState(listener);
  apiListeners.add(listener);
  return () => apiListeners.delete(listener);
}

/** Cache only states received from the server or confirmed by a successful write. */
export function seedApiContentState(
  input: GetContentInput,
  state: ApiReactionState,
): void {
  if (!isConsumerApiMode) return;
  const key = stateKey(input);
  const previous = apiReactionStates.get(key) ?? {};
  const next = { ...previous, ...state };
  if (previous.saved === next.saved && previous.liked === next.liked) return;
  apiReactionStates.set(key, next);
  notifyApiState();
}

export function replaceApiSavedStatesForTopic(
  topicId: string,
  savedContentIds: ReadonlySet<string>,
): void {
  if (!isConsumerApiMode) return;
  let changed = false;
  for (const [key, state] of apiReactionStates) {
    if (!key.startsWith(`${topicId}:`)) continue;
    const contentId = key.slice(topicId.length + 1);
    const saved = savedContentIds.has(contentId);
    if (state.saved !== saved) {
      apiReactionStates.set(key, { ...state, saved });
      changed = true;
    }
  }
  for (const contentId of savedContentIds) {
    const key = `${topicId}:${contentId}`;
    if (!apiReactionStates.has(key)) {
      apiReactionStates.set(key, { saved: true });
      changed = true;
    }
  }
  if (changed) notifyApiState();
}

export function readContentSavedState(input: GetContentInput, fallback = false): boolean {
  if (isConsumerApiMode) return apiReactionStates.get(stateKey(input))?.saved ?? fallback;
  return readMockTopicState(input.topicContext.topicId)
    .savedByContentId[input.contentId] ?? fallback;
}

export function readContentLikedState(input: GetContentInput, fallback = false): boolean {
  if (isConsumerApiMode) return apiReactionStates.get(stateKey(input))?.liked ?? fallback;
  return readMockTopicState(input.topicContext.topicId)
    .likedByContentId[input.contentId] ?? fallback;
}

export function toggleContentLikedState(input: GetContentInput): void {
  if (isConsumerApiMode) {
    throw new Error("API likes must be confirmed by the server.");
  }
  setMockLikedState(
    input.topicContext.topicId,
    input.contentId,
    !readContentLikedState(input),
  );
}
