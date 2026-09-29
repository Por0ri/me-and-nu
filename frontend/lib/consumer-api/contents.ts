import {
  mockContentDetailsByTopicId,
  mockContentsByTopicId,
} from "@/mocks/contents";
import { mockTopics } from "@/mocks/topics";
import {
  ApiError,
  bookmarkContent,
  clearPreference,
  getBookmarks,
  getContent as getApiContent,
  getTopics,
  likeContent,
  setPreference,
  unbookmarkContent,
  unlikeContent,
} from "@/lib/api";
import {
  readContentLikedState,
  replaceApiSavedStatesForTopic,
  seedApiContentState,
  toggleContentLikedState,
} from "@/lib/content-state";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
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

function requireApiId(value: string, name: string): number {
  const id = Number(value);
  if (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(id)) {
    throw new ContentNotFoundError(`Invalid ${name}: ${value}`);
  }
  return id;
}

function apiIds(input: GetContentInput) {
  return {
    contentId: requireApiId(input.contentId, "content ID"),
    topicId: requireApiId(input.topicContext.topicId, "Topic ID"),
  };
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
  if (isConsumerApiMode) {
    const { contentId, topicId } = apiIds(input);
    try {
      const detail = await getApiContent(contentId, topicId);
      if (detail.contentId !== contentId || detail.topicId !== topicId) {
        throw new Error("Content detail does not match the requested context.");
      }
      const saved = detail.myReaction?.bookmarked ?? false;
      const liked = detail.myReaction?.liked ?? false;
      seedApiContentState(input, { saved, liked });
      return {
        id: input.contentId,
        title: detail.title,
        summary: detail.excerpt ?? "",
        imageUrl: detail.imageUrl ?? undefined,
        sourceName: detail.publisher ?? "출처 없음",
        saved,
        liked,
        body: detail.displayMode === "full_body" ? detail.body ?? undefined : undefined,
        sources: detail.sourceUrl
          ? [{ id: `source-${contentId}`, name: detail.publisher ?? "출처 없음", url: detail.sourceUrl }]
          : [],
        topicContext: { topicId: input.topicContext.topicId },
        feedback: detail.myReaction?.preference === "positive"
          ? "O"
          : detail.myReaction?.preference === "negative"
            ? "X"
            : null,
      };
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) {
        throw new ContentNotFoundError(cause.message);
      }
      throw cause;
    }
  }
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
  if (isConsumerApiMode) {
    const { contentId, topicId } = apiIds(input);
    if (input.currentFeedback === input.feedback) {
      await clearPreference(contentId, topicId);
      return { contentId: input.contentId, topicContext: input.topicContext, feedback: null };
    }
    const response = await setPreference(
      contentId, topicId, input.feedback === "O" ? "positive" : "negative",
    );
    if (response.contentId !== contentId || response.topicId !== topicId) {
      throw new Error("Preference response does not match the requested content.");
    }
    return {
      contentId: input.contentId,
      topicContext: input.topicContext,
      feedback: response.value === "positive" ? "O" : "X",
    };
  }
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
  if (isConsumerApiMode) {
    const { contentId, topicId } = apiIds(input);
    if (saved) {
      const response = await bookmarkContent(contentId, topicId);
      if (response.contentId !== contentId || response.topicId !== topicId) {
        throw new Error("Bookmark response does not match the requested content.");
      }
    } else {
      await unbookmarkContent(contentId, topicId);
    }
    seedApiContentState(input, { saved });
    return { contentId: input.contentId, saved, topicContext: input.topicContext };
  }
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
  if (isConsumerApiMode) {
    const numericTopicId = requireApiId(topicId, "Topic ID");
    const [catalog, firstPage] = await Promise.all([
      getTopics(),
      getBookmarks({ topicId: numericTopicId, limit: 100 }),
    ]);
    const topic = catalog.topics.find((item) => item.id === numericTopicId);
    if (!topic) throw new ContentNotFoundError(`Unknown Topic: ${topicId}`);
    const seenCursors = new Set<string>();
    const contents: SavedContentsData["contents"] = [];
    let page = firstPage;
    for (;;) {
      for (const item of page.items) {
        if (item.topicId !== numericTopicId) {
          throw new Error("Bookmark topic does not match the requested Topic.");
        }
        contents.push({
          id: String(item.contentId),
          title: item.title,
          summary: item.summary ?? "",
          imageUrl: item.imageUrl ?? undefined,
          sourceName: item.sourceName ?? "출처 없음",
          saved: true,
        });
      }
      if (!page.nextCursor) break;
      if (seenCursors.has(page.nextCursor)) throw new Error("Bookmark cursor repeated.");
      seenCursors.add(page.nextCursor);
      page = await getBookmarks({ topicId: numericTopicId, cursor: page.nextCursor, limit: 100 });
    }
    replaceApiSavedStatesForTopic(topicId, new Set(contents.map((item) => item.id)));
    return { topic: { id: topicId, name: topic.name, code: topic.code }, contents };
  }
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

export async function setContentLike(
  input: GetContentInput,
  liked: boolean,
): Promise<boolean> {
  if (isConsumerApiMode) {
    const { contentId, topicId } = apiIds(input);
    if (liked) {
      const response = await likeContent(contentId, topicId);
      if (response.contentId !== contentId || !response.liked) {
        throw new Error("Like response does not match the requested content.");
      }
    } else {
      await unlikeContent(contentId, topicId);
    }
    seedApiContentState(input, { liked });
    return liked;
  }
  if (readContentLikedState(input) !== liked) toggleContentLikedState(input);
  return liked;
}
