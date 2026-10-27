import type { HomeDetailTarget, HomeSlideModel } from "./home";
import type { ContentImage, PublicationMetadata, PublicSource } from "./carousel-data";

// FE reading model, deliberately independent of any future server DTO.
export type DetailArticle = HomeDetailTarget & PublicationMetadata & {
  origin: "demo" | "service";
  work?: string;
  title: string;
  bookmarked?: boolean;
  displayMode?: "full_body" | "link_excerpt";
  sourceUrl?: string;
  image?: ContentImage;
  slides?: readonly HomeSlideModel[];
  sources?: readonly PublicSource[];
  sections: readonly { heading?: string; paragraphs: readonly string[] }[];
};
export type DetailLoadResult =
  | { status: "ready"; article: DetailArticle }
  | { status: "not-found" }
  | { status: "empty" }
  | { status: "unconfigured"; message: string };
export interface DetailDataSource {
  readonly kind: "demo" | "service";
  load(target: HomeDetailTarget, options: { signal: AbortSignal }): Promise<DetailLoadResult>;
}
export type DetailState = DetailLoadResult | { status: "loading" } | { status: "error"; message: string };

export function isDetailTarget(target: HomeDetailTarget): boolean {
  return [target.postId, target.contentId].every((id) => typeof id === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(id)) &&
    (target.topicId === undefined || (Number.isSafeInteger(target.topicId) && target.topicId > 0));
}

export function createDetailRequestController(source: DetailDataSource, publish: (state: DetailState) => void) {
  let generation = 0;
  let active: AbortController | null = null;
  return {
    async load(target: HomeDetailTarget): Promise<void> {
      const request = ++generation;
      active?.abort();
      const controller = new AbortController();
      active = controller;
      publish({ status: "loading" });
      try {
        if (!isDetailTarget(target)) { publish({ status: "not-found" }); return; }
        const result = await source.load(target, { signal: controller.signal });
        if (request !== generation || controller.signal.aborted) return;
        if (result.status === "ready") {
          const article = result.article;
          if (article.origin !== source.kind || article.postId !== target.postId || article.contentId !== target.contentId || article.topicId !== target.topicId) {
            throw new Error("Detail identity or origin mismatch");
          }
          const hasText = article.sections.some((section) => section.paragraphs.some((text) => text.trim()));
          const hasSourceExcerpt = article.origin === "service" && article.displayMode === "link_excerpt" && !!article.sourceUrl;
          publish(hasText || hasSourceExcerpt ? result : { status: "empty" });
        } else publish(result);
      } catch {
        if (request === generation && !controller.signal.aborted) publish({ status: "error", message: "글을 불러오지 못했어요. 다시 시도해 주세요." });
      } finally {
        if (active === controller) active = null;
      }
    },
    cancel() { generation++; active?.abort(); active = null; },
  };
}
