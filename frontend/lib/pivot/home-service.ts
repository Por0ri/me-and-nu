import { ApiError, getFeed, getMyTopics, type FeedResponse } from "@/lib/api";
import type { HomeCardModel, HomeDataSource, HomeLoadError, HomeViewModel } from "./home";
import { parsePivotSession } from "./session";
import { canUsePivotApi } from "./services";

// Keep disconnected mode explicit; it never reads the API or falls back to Demo.
export const pendingHomeSource: HomeDataSource = {
  kind: "service",
  async load() {
    return { status: "unconfigured", message: "콘텐츠 서비스를 준비하고 있어요. 아직 피드를 불러올 수 없어요." };
  },
};

function invalidResponse(): never {
  throw new ApiError("Invalid Home response.", 200, "INVALID_HOME_RESPONSE");
}

function positiveId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

/** A feed image is one display slide, not an assumed BE slides/recommendation contract. */
export function homeFromFeed(response: FeedResponse, topicId: number): HomeViewModel {
  if (!response || response.selectedTopicId !== topicId || !Array.isArray(response.sections)) invalidResponse();
  const cards: HomeCardModel[] = [];
  const seen = new Set<number>();
  for (const section of response.sections) {
    if (!section || !Array.isArray(section.contents)) invalidResponse();
    for (const content of section.contents) {
      if (!content || content.kind !== "content" || !positiveId(content.id) || content.topicId !== topicId ||
          typeof content.title !== "string" ||
          (content.summary != null && typeof content.summary !== "string") ||
          (content.imageUrl != null && typeof content.imageUrl !== "string")) invalidResponse();
      if (seen.has(content.id)) continue;
      seen.add(content.id);
      const id = String(content.id);
      cards.push({
        id, title: content.title, detailContentId: id,
        slides: [{
          id: `${id}-cover`, order: 1, description: content.summary ?? "",
          ...(content.imageUrl?.trim() ? { image: { src: content.imageUrl, alt: content.title } } : {}),
        }],
      });
    }
  }
  return { origin: "service", cards };
}

function loadError(error: unknown): HomeLoadError {
  if (error instanceof ApiError) {
    if (error.status === 401) return { status: "error", reason: "unauthorized", message: "로그인이 만료되었어요. 다시 로그인해 주세요." };
    if (error.status === 403) return { status: "error", reason: "forbidden", message: "이 콘텐츠 피드에 접근할 권한이 없어요. 계정의 활성 관심사를 확인해 주세요." };
    if (error.code === "INVALID_HOME_RESPONSE") return { status: "error", reason: "invalid-response", message: "콘텐츠 응답을 확인하지 못했어요. 다시 시도해 주세요." };
    return { status: "error", reason: "server", message: "콘텐츠 서비스 요청에 실패했어요. 잠시 후 다시 시도해 주세요." };
  }
  return { status: "error", reason: "network", message: "네트워크 연결을 확인한 뒤 다시 시도해 주세요." };
}

/** The route supplies the restored session; this source never starts login itself. */
export function createServiceHomeSource(readSession: () => unknown): HomeDataSource {
  return {
    kind: "service",
    async load({ signal }) {
      try {
        const initial = parsePivotSession(readSession());
        function requireActiveSession() {
          signal.throwIfAborted();
          const current = parsePivotSession(readSession());
          if (initial?.sessionState !== "active" || current?.sessionState !== "active" || current.user.id !== initial.user.id) {
            throw new ApiError("Active session required.", 401, "UNAUTHENTICATED");
          }
        }
        requireActiveSession();
        if (!canUsePivotApi()) return pendingHomeSource.load({ signal });
        const response = await getMyTopics({}, { signal });
        requireActiveSession();
        if (!response || !Array.isArray(response.topics) || response.topics.some(topic =>
          !topic || !positiveId(topic.id) || typeof topic.status !== "string")) invalidResponse();
        // Home currently has no Topic picker. Use the first active Topic in BE order.
        const topic = response.topics.find(item => item.status === "active" && item.deletedAt == null);
        if (!topic) return { status: "ready", home: { origin: "service", cards: [] } };
        if (!canUsePivotApi()) return pendingHomeSource.load({ signal });
        // Load the first page only; pagination needs a separate Home interaction contract.
        const feed = await getFeed(topic.id, {}, { signal });
        requireActiveSession();
        if (!canUsePivotApi()) return pendingHomeSource.load({ signal });
        return { status: "ready", home: homeFromFeed(feed, topic.id) };
      } catch (error) {
        signal.throwIfAborted();
        return loadError(error);
      }
    },
  };
}
