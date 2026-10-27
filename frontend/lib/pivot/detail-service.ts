import { ApiError, getContent, type ContentDetail } from "@/lib/api";
import { requirePivotApi } from "./services";
import type { DetailArticle, DetailDataSource } from "./detail";
import type { HomeDetailTarget } from "./home";
import { optionalImage, requireTitle, safeSourceUrl, serviceContentId } from "./service-data";
import { mapCarouselSlides, mapContentSources, publicationMetadata } from "./carousel-data";

export function mapContentDetail(content: ContentDetail, target: HomeDetailTarget): DetailArticle {
  if (!content || content.contentId !== serviceContentId(target) || content.topicId !== target.topicId ||
    typeof content.myReaction?.bookmarked !== "boolean") throw new Error("Invalid detail identity or bookmark state");
  if (content.displayMode !== "full_body" && content.displayMode !== "link_excerpt") throw new Error("Unsupported display mode");
  const title = requireTitle(content.title);
  const text = content.displayMode === "full_body" ? content.body : content.excerpt;
  if (text !== null && typeof text !== "string") throw new Error("Invalid article text");
  const paragraphs = (text ?? "").split(/\r?\n\s*\r?\n/).map((value) => value.trim()).filter(Boolean);
  const slides = mapCarouselSlides(content.slides, content.contentId, content.topicId);
  return {
    ...target, ...publicationMetadata(content), origin: "service", title,
    image: slides[0]?.image ?? optionalImage(content.imageUrl, title),
    slides, sources: mapContentSources(content.sources),
    sections: paragraphs.length ? [{ paragraphs }] : [],
    bookmarked: content.myReaction.bookmarked, displayMode: content.displayMode,
    sourceUrl: safeSourceUrl(content.sourceUrl),
  };
}

export function createServiceDetailSource(
  api: { getContent: typeof getContent } = { getContent },
  guard: () => void = requirePivotApi,
): DetailDataSource {
  return {
    kind: "service",
    async load(target, { signal }) {
      guard(); signal.throwIfAborted();
      const contentId = serviceContentId(target);
      if (contentId === null) return { status: "not-found" };
      try {
        const content = await api.getContent(contentId, target.topicId!, { signal });
        signal.throwIfAborted();
        return { status: "ready", article: mapContentDetail(content, target) };
      } catch (error) {
        if (error instanceof ApiError && error.status === 404 && (error.code === "CONTENT_NOT_FOUND" || error.code === "TOPIC_NOT_FOUND")) {
          return { status: "not-found" };
        }
        throw error;
      }
    },
  };
}

export const serviceDetailSource = createServiceDetailSource();
export const pendingDetailSource = serviceDetailSource;
