import { ApiError, getFeed, getMyTopics, type FeedCard, type Session } from "@/lib/api";
import { requirePivotApi } from "./services";
import type { HomeCardModel, HomeDataSource, HomeLoadError } from "./home";
import { invalidContentResponse, mapCarouselSlides, publicationMetadata } from "./carousel-data";
import { activeTopicId, isPositiveId, LIST_PAGE_SIZE, MAX_LIST_PAGES, nextPageCursor, optionalImage, requireTitle } from "./service-data";

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
    slides: slides.length ? slides : [{ id, order: 0, description: card.summary ?? "", image: optionalImage(card.imageUrl, title) }],
  };
}

function activeUserId(session: Session | null): number | null {
  return session?.authenticated === true && session.sessionState === "active" && session.onboardingCompleted === true &&
    session.user?.accountType === "user" && isPositiveId(session.user.id) ? session.user.id : null;
}

function loadError(error: unknown): HomeLoadError {
  if (error instanceof ApiError) {
    if (error.status === 401) return { status: "error", reason: "unauthorized", message: "로그인이 만료되었어요. 다시 로그인해 주세요." };
    if (error.status === 403) return { status: "error", reason: "forbidden", message: "이 콘텐츠 피드에 접근할 권한이 없어요. 계정의 활성 관심사를 확인해 주세요." };
    if (error.code === "INVALID_CONTENT_RESPONSE") return { status: "error", reason: "invalid-response", message: "콘텐츠 응답을 확인하지 못했어요. 다시 시도해 주세요." };
    return { status: "error", reason: "server", message: "콘텐츠 서비스 요청에 실패했어요. 잠시 후 다시 시도해 주세요." };
  }
  return { status: "error", reason: "network", message: "네트워크 연결을 확인한 뒤 다시 시도해 주세요." };
}

export function createServiceHomeSource(
  api: { getMyTopics: typeof getMyTopics; getFeed: typeof getFeed } = { getMyTopics, getFeed },
  guard: () => void = requirePivotApi,
  readSession?: () => Session | null,
): HomeDataSource {
  return {
    kind: "service",
    async load({ signal }) {
      guard(); signal.throwIfAborted();
      const initialUser = readSession ? activeUserId(readSession()) : null;
      const checkSession = () => {
        signal.throwIfAborted(); guard();
        if (readSession && (initialUser === null || activeUserId(readSession()) !== initialUser)) {
          throw new ApiError("Active session changed.", 401, "AUTH_REQUIRED");
        }
      };
      try {
        checkSession();
        const topicResponse = await api.getMyTopics({}, { signal });
        checkSession();
        let topicId: number | null;
        try { topicId = activeTopicId(topicResponse); } catch { invalidContentResponse(); }
        const cards: HomeCardModel[] = [];
        if (topicId !== null) {
          let cursor: string | undefined;
          const cursors = new Set<string>();
          const ids = new Set<string>();
          for (let page = 0; page < MAX_LIST_PAGES; page++) {
            const response = await api.getFeed(topicId, { limit: LIST_PAGE_SIZE, cursor }, { signal });
            checkSession();
            if (!response || response.selectedTopicId !== topicId || !Array.isArray(response.sections)) invalidContentResponse();
            for (const section of response.sections) {
              if (!section || !Array.isArray(section.contents)) invalidContentResponse();
              for (const item of section.contents) {
                const card = mapFeedCard(item, topicId);
                if (ids.has(card.id)) continue;
                ids.add(card.id); cards.push(card);
              }
            }
            try { cursor = nextPageCursor(response.nextCursor, cursors, page); } catch { invalidContentResponse(); }
            if (cursor === undefined) break;
          }
        }
        return { status: "ready", home: { origin: "service", cards } };
      } catch (error) {
        signal.throwIfAborted();
        return loadError(error);
      }
    },
  };
}

export const serviceHomeSource = createServiceHomeSource();
export const pendingHomeSource = serviceHomeSource;
