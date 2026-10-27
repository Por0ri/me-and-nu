import { ApiError, getContent } from "@/lib/api";
import { isServiceDetailTarget, safeDetailUrl, type DetailArticle, type DetailDataSource, type DetailLoadError } from "./detail";
import type { HomeDetailTarget } from "./home";
import { parsePivotSession } from "./session";
import { canUsePivotApi } from "./services";

// Disconnected mode stays explicit and never falls back to Demo.
export const pendingDetailSource: DetailDataSource = {
  kind: "service",
  async load() {
    return { status: "unconfigured", message: "콘텐츠 상세 서비스를 준비하고 있어요. 아직 글을 불러올 수 없어요." };
  },
};

function invalidResponse(): never {
  throw new ApiError("Invalid Detail response.", 200, "INVALID_DETAIL_RESPONSE");
}

/** Validate the deployed wire payload, not the older shared ContentDetail type's required extras. */
export function detailFromResponse(value: unknown, target: HomeDetailTarget): DetailArticle {
  if (!isServiceDetailTarget(target) || !value || typeof value !== "object" || Array.isArray(value)) invalidResponse();
  const data = value as Record<string, unknown>;
  const mode = data.displayMode === undefined ? "link_excerpt" : data.displayMode;
  if (data.contentId !== Number(target.contentId) || data.topicId !== Number(target.topicId)
    || typeof data.title !== "string" || !data.title.trim()
    || typeof data.publisher !== "string" || typeof data.sourceUrl !== "string"
    || (data.publishedAt !== null && typeof data.publishedAt !== "string")
    || typeof data.productionType !== "string" || !["human", "ai", "hybrid"].includes(data.productionType)
    || (mode !== "full_body" && mode !== "link_excerpt")
    || [data.body, data.excerpt, data.imageUrl, data.contentType].some(field => field != null && typeof field !== "string")) invalidResponse();
  if (!data.share || typeof data.share !== "object" || Array.isArray(data.share)) invalidResponse();
  const share = data.share as Record<string, unknown>;
  if (typeof share.url !== "string" || (share.text !== undefined && typeof share.text !== "string")) invalidResponse();
  const body = typeof data.body === "string" ? data.body : "";
  const excerpt = typeof data.excerpt === "string" ? data.excerpt : "";
  // Link/excerpt mode must never reveal a body even if a payload includes one.
  // HTML and Markdown remain escaped plain text; no rich-content renderer is implied.
  const text = mode === "full_body" && body.trim() ? body : excerpt;
  const paragraphs = text.split(/\r?\n/).filter(line => line.trim());
  const imageUrl = safeDetailUrl(data.imageUrl);
  return {
    postId: target.postId, contentId: String(data.contentId), topicId: String(data.topicId), origin: "service",
    title: data.title, displayMode: mode,
    source: { publisher: data.publisher, url: safeDetailUrl(data.sourceUrl) },
    ...(imageUrl ? { image: { src: imageUrl, alt: data.title } } : {}),
    sections: paragraphs.length ? [{ paragraphs }] : [],
  };
}

function loadError(error: unknown): DetailLoadError {
  if (error instanceof ApiError) {
    if (error.status === 401) return { status: "error", reason: "unauthorized", message: "로그인이 만료되었어요. 다시 로그인해 주세요." };
    if (error.status === 403 && error.code === "ONBOARDING_REQUIRED") return { status: "error", reason: "onboarding-required", message: "가입 완료 상태를 확인해야 해요. 로그인 화면에서 세션을 다시 확인해 주세요." };
    if (error.status === 403) return { status: "error", reason: "forbidden", message: "이 콘텐츠에 접근할 권한이 없어요. 로그인 상태를 확인해 주세요." };
    if (error.status === 404 && error.code === "TOPIC_NOT_FOUND") return { status: "error", reason: "topic-not-found", message: "접근할 수 있는 관심사를 찾지 못했어요. Home에서 다시 선택해 주세요." };
    if (error.status === 404) return { status: "error", reason: "content-not-found", message: "콘텐츠를 찾을 수 없거나 공개되지 않은 글이에요." };
    if (error.status === 422) return { status: "error", reason: "invalid-request", message: "콘텐츠 주소를 확인하지 못했어요. Home에서 다시 선택해 주세요." };
    if (error.code === "INVALID_DETAIL_RESPONSE") return { status: "error", reason: "invalid-response", message: "콘텐츠 응답을 확인하지 못했어요. 다시 시도해 주세요." };
    return { status: "error", reason: "server", message: "콘텐츠 서비스 요청에 실패했어요. 잠시 후 다시 시도해 주세요." };
  }
  return { status: "error", reason: "network", message: "네트워크 연결을 확인한 뒤 다시 시도해 주세요." };
}

/** Session and environment are rechecked after every read; cancelled requests stay cancelled. */
export function createServiceDetailSource(readSession: () => unknown): DetailDataSource {
  return {
    kind: "service",
    async load(target, { signal }) {
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
        if (!isServiceDetailTarget(target)) throw new ApiError("Invalid Detail target.", 422, "VALIDATION_ERROR");
        if (!canUsePivotApi()) return pendingDetailSource.load(target, { signal });
        const response = await getContent(Number(target.contentId), Number(target.topicId), { signal });
        requireActiveSession();
        if (!canUsePivotApi()) return pendingDetailSource.load(target, { signal });
        return { status: "ready", article: detailFromResponse(response, target) };
      } catch (error) {
        signal.throwIfAborted();
        return loadError(error);
      }
    },
  };
}
