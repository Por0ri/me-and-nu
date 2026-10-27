import type { HomeDetailTarget } from "./home";

// FE reading model. Service DTOs are validated and mapped at the source boundary.
export type DetailArticle = HomeDetailTarget & {
  origin: "demo" | "service";
  work?: string;
  title: string;
  image?: { src: string; alt: string };
  displayMode?: "full_body" | "link_excerpt";
  source?: { publisher: string; url?: string };
  sections: readonly { heading?: string; paragraphs: readonly string[] }[];
};
export type DetailLoadResult =
  | { status: "ready"; article: DetailArticle }
  | { status: "not-found" }
  | { status: "empty" }
  | DetailLoadError
  | { status: "unconfigured"; message: string };
export type DetailLoadError = {
  status: "error";
  reason?: "unauthorized" | "onboarding-required" | "forbidden" | "content-not-found" | "topic-not-found"
    | "invalid-request" | "invalid-response" | "server" | "network";
  message: string;
};
export interface DetailDataSource {
  readonly kind: "demo" | "service";
  load(target: HomeDetailTarget, options: { signal: AbortSignal }): Promise<DetailLoadResult>;
}
export type DetailState = DetailLoadResult | { status: "loading" };

/** Only absolute HTTP(S) URLs without credentials or ambiguous whitespace are navigable. */
export function safeDetailUrl(value: unknown): string | undefined {
  if (typeof value !== "string" || !/^https?:\/\//i.test(value) || /[\u0000-\u0020\\]/.test(value)) return;
  try {
    const url = new URL(value);
    if ((url.protocol === "https:" || url.protocol === "http:") && !url.username && !url.password) return url.href;
  } catch { /* Missing/unsafe URLs remain absent, never a generated source. */ }
}

export function isDetailTarget(target: HomeDetailTarget): boolean {
  return [target.postId, target.contentId, ...(target.topicId === undefined ? [] : [target.topicId])]
    .every((id) => typeof id === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(id));
}

/** Service IDs must survive conversion to the numeric API contract without coercion or rounding. */
export function isServiceDetailTarget(target: HomeDetailTarget): boolean {
  return isDetailTarget(target) && [target.contentId, target.topicId].every((id) =>
    typeof id === "string" && /^[1-9][0-9]*$/.test(id) && Number.isSafeInteger(Number(id)));
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
        if (!(source.kind === "service" ? isServiceDetailTarget(target) : isDetailTarget(target))) {
          publish({ status: "not-found" }); return;
        }
        const result = await source.load(target, { signal: controller.signal });
        if (request !== generation || controller.signal.aborted) return;
        if (result.status === "ready") {
          const article = result.article;
          if (article.origin !== source.kind || article.postId !== target.postId || article.contentId !== target.contentId || article.topicId !== target.topicId) {
            throw new Error("Detail identity or origin mismatch");
          }
          const readable = article.sections.some((section) => section.paragraphs.some((text) => text.trim()));
          // A Service article can still offer its title and original source without a body.
          publish(readable || (article.origin === "service" && article.title.trim()) ? result : { status: "empty" });
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
