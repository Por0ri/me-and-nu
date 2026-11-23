import { ApiError, getFeed, getMyTopics, searchFeed, searchWorks, type FeedCard, type MyTopicsResponse, type Session } from "@/lib/api";
import { requirePivotApi } from "./services";
import type { HomeCardModel, HomeDataSource, HomeLoadError, HomeLoadResult } from "./home";
import { createRecentSearchStore } from "./recent-search";
import { invalidContentResponse, mapCarouselSlides, publicationMetadata } from "./carousel-data";
import { activeTopicId, isPositiveId, LIST_PAGE_SIZE, optionalImage, requireTitle } from "./service-data";

export function mapFeedCard(card: FeedCard, topicId: number): HomeCardModel {
  if (!card || card.kind !== "content" || !isPositiveId(card.id) || card.topicId !== topicId || typeof card.saved !== "boolean" ||
    typeof card.title !== "string" || !card.title.trim() || (card.summary != null && typeof card.summary !== "string") ||
    (card.imageUrl != null && typeof card.imageUrl !== "string")) {
    invalidContentResponse();
  }
  const id = String(card.id);
  const title = requireTitle(card.title);
  const slides = mapCarouselSlides(card.slides, card.id, topicId);
  return {
    ...publicationMetadata(card),
    id, detailContentId: id, topicId, title, saved: card.saved,
    liked: card.myReaction?.liked === true, preference: card.myReaction?.preference ?? null,
    recommendationId: isPositiveId(card.recommendationId) ? card.recommendationId : undefined,
    recommendationReason: typeof card.recommendationReason === "string" ? card.recommendationReason : undefined,
    slides: slides.length ? slides : [{ id, order: 0, description: card.summary ?? "", image: optionalImage(card.imageUrl, title) }],
  };
}

function activeUserId(session: Session | null): number | null {
  return session?.authenticated === true && session.sessionState === "active" && session.onboardingCompleted === true &&
    session.user?.accountType === "user" && isPositiveId(session.user.id) ? session.user.id : null;
}

function topicInterests(response: MyTopicsResponse, topicId: number) {
  return [...(response.topics.find(topic => topic.id === topicId)?.subtopicIds ?? [])].sort((a, b) => a - b).join(",");
}

function loadError(error: unknown): HomeLoadError {
  if (error instanceof ApiError) {
    if (error.code === "FEED_SCOPE_CHANGED") return { status: "error", reason: "scope-changed", message: "관심사가 변경됐어요. 피드를 다시 불러와 주세요." };
    if (error.status === 401) return { status: "error", reason: "unauthorized", message: "로그인이 만료되었어요. 다시 로그인해 주세요." };
    if (error.status === 403) return { status: "error", reason: "forbidden", message: "이 콘텐츠 피드에 접근할 권한이 없어요. 계정의 활성 관심사를 확인해 주세요." };
    if (error.code === "INVALID_CONTENT_RESPONSE") return { status: "error", reason: "invalid-response", message: "콘텐츠 응답을 확인하지 못했어요. 다시 시도해 주세요." };
    return { status: "error", reason: "server", message: "콘텐츠 서비스 요청에 실패했어요. 잠시 후 다시 시도해 주세요." };
  }
  return { status: "error", reason: "network", message: "네트워크 연결을 확인한 뒤 다시 시도해 주세요." };
}

export function createServiceHomeSource(
  api: { getMyTopics: typeof getMyTopics; getFeed: typeof getFeed; searchFeed?: typeof searchFeed; searchWorks?: typeof searchWorks } = { getMyTopics, getFeed, searchFeed, searchWorks },
  guard: () => void = requirePivotApi,
  readSession?: () => Session | null,
): HomeDataSource {
  // The search APIs belong to the member's active Topic; null means no searchable Topic.
  async function searchTopic(signal: AbortSignal): Promise<number | null> {
    guard(); signal.throwIfAborted();
    const topicResponse = await api.getMyTopics({}, { signal });
    try { return activeTopicId(topicResponse); } catch { invalidContentResponse(); }
  }
  return {
    kind: "service",
    recent: createRecentSearchStore(() => readSession ? activeUserId(readSession()) : null),
    async load({ signal }) {
      guard(); signal.throwIfAborted();
      const owner = readSession?.() ?? null;
      const initialUser = activeUserId(owner);
      const checkSession = (requestSignal: AbortSignal) => {
        requestSignal.throwIfAborted(); guard();
        const current = readSession?.();
        if (readSession && (initialUser === null || activeUserId(current ?? null) !== initialUser || current?.csrfToken !== owner?.csrfToken)) {
          throw new ApiError("Active session changed.", 401, "AUTH_REQUIRED");
        }
      };
      const requestError = (error: unknown, requestSignal: AbortSignal) => {
        requestSignal.throwIfAborted();
        try { checkSession(requestSignal); } catch (scopeError) { return loadError(scopeError); }
        return loadError(error);
      };
      try {
        checkSession(signal);
        const topicResponse = await api.getMyTopics({}, { signal });
        checkSession(signal);
        let topicId: number | null;
        try { topicId = activeTopicId(topicResponse); } catch { invalidContentResponse(); }
        if (topicId === null) return { status: "ready", home: { origin: "service", cards: [] } };
        const selectedTopic = topicId;
        const selectedInterests = topicInterests(topicResponse, selectedTopic);
        // A continuation owns its Topic, session and cursor history. Failed/cancelled
        // attempts do not advance it, so retrying never skips an unseen page.
        async function readPage(cursor: string | undefined, visited: ReadonlySet<string>, requestSignal: AbortSignal): Promise<HomeLoadResult> {
          try {
            checkSession(requestSignal);
            if (cursor !== undefined) {
              const currentTopics = await api.getMyTopics({}, { signal: requestSignal });
              checkSession(requestSignal);
              let currentTopic: number | null;
              try { currentTopic = activeTopicId(currentTopics); } catch { invalidContentResponse(); }
              if (currentTopic !== selectedTopic || topicInterests(currentTopics, selectedTopic) !== selectedInterests) {
                throw new ApiError("Feed interests changed.", 409, "FEED_SCOPE_CHANGED");
              }
            }
            const response = await api.getFeed(selectedTopic, { limit: LIST_PAGE_SIZE, cursor }, { signal: requestSignal });
            checkSession(requestSignal);
            if (!response || response.selectedTopicId !== selectedTopic || !Array.isArray(response.sections)) invalidContentResponse();
            const seen = new Set(visited);
            if (cursor !== undefined) seen.add(cursor);
            const next = response.nextCursor;
            if (next !== null && (typeof next !== "string" || !next.trim() || seen.has(next))) invalidContentResponse();
            const cards: HomeCardModel[] = [], ids = new Set<string>();
            for (const section of response.sections) {
              if (!section || !Array.isArray(section.contents)) invalidContentResponse();
              for (const item of section.contents) {
                const card = mapFeedCard(item, selectedTopic);
                if (!ids.has(card.id)) { ids.add(card.id); cards.push(card); }
              }
            }
            return { status: "ready", home: { origin: "service", cards },
              ...(next === null ? {} : { nextPage: ({ signal: nextSignal }: { signal: AbortSignal }) => readPage(next, seen, nextSignal) }) };
          } catch (error) {
            return requestError(error, requestSignal);
          }
        }
        return await readPage(undefined, new Set(), signal);
      } catch (error) {
        return requestError(error, signal);
      }
    },
    async search(q, { signal, workId }) {
      guard(); signal.throwIfAborted();
      if (!api.searchFeed) return { works: [], cards: [] };
      const id = await searchTopic(signal);
      if (id === null) return { works: [], cards: [] };
      const response = await api.searchFeed(id, q, { signal }, workId);
      signal.throwIfAborted();
      if (!response || response.selectedTopicId !== id || !Array.isArray(response.contents) ||
        !Array.isArray(response.works) || response.works.some((name) => typeof name !== "string")) invalidContentResponse();
      return { works: response.works, cards: response.contents.map((item) => mapFeedCard(item, id)) };
    },
    async suggest(q, { signal }) {
      guard(); signal.throwIfAborted();
      if (!api.searchWorks) return [];
      const id = await searchTopic(signal);
      if (id === null) return [];
      const response = await api.searchWorks(id, q, { signal });
      signal.throwIfAborted();
      if (!response || response.selectedTopicId !== id || !Array.isArray(response.works) || response.works.some((work) =>
        !isPositiveId(work?.entityId) || typeof work.name !== "string" || !work.name.trim())) invalidContentResponse();
      return response.works.map((work) => ({ id: work.entityId, name: work.name }));
    },
  };
}

export const serviceHomeSource = createServiceHomeSource();
export const pendingHomeSource = serviceHomeSource;
