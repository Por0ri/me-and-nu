import { ApiError, apiBaseUrl, type CarouselBox, type CarouselLayout, type ContentNotice } from "@/lib/api";
import type { HomeSlideModel } from "./home";
import { isPositiveId, optionalImage, safeSourceUrl } from "./service-data";

export type ContentImage = { src: string; alt: string; protected?: true };
export type PublicSource = { url: string; title?: string | null; publisher?: string | null; role: string; citationOrder?: number | null };
export type PublicationMetadata = {
  publishedAt?: string | null;
  productionType?: "human" | "ai" | "hybrid";
  publisher?: string;
  sourceUrl?: string;
  notices?: readonly { code: string; message: string }[];
};

export function invalidContentResponse(): never {
  throw new ApiError("Invalid content response.", 200, "INVALID_CONTENT_RESPONSE");
}

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function finite(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
}
function box(value: unknown): CarouselBox {
  if (!object(value) || !finite(value.x, 0, 1) || !finite(value.y, 0, 1) ||
    !finite(value.width, 0, 1) || value.width === 0 || !finite(value.height, 0, 1) || value.height === 0 ||
    value.x + value.width > 1.000001 || value.y + value.height > 1.000001) invalidContentResponse();
  return { x: value.x, y: value.y, width: value.width, height: value.height };
}
function layout(value: unknown): CarouselLayout {
  if (!object(value) || ![value.template, value.fitMode, value.contrastMode].every(item => typeof item === "string" && item.length > 0 && item.length <= 50) ||
    !["blur", "palette", "gradient"].includes(value.backgroundStyle as string) ||
    !finite(value.imageScale, 0.5, 1.5) || !finite(value.imageOffsetY, -0.3, 0.3) || typeof value.blurBackground !== "boolean") invalidContentResponse();
  return {
    template: value.template as string, fitMode: value.fitMode as string,
    backgroundStyle: value.backgroundStyle as CarouselLayout["backgroundStyle"],
    imageBox: box(value.imageBox), copyBox: box(value.copyBox), textOverlayBox: box(value.textOverlayBox),
    imageScale: value.imageScale, imageOffsetY: value.imageOffsetY,
    contrastMode: value.contrastMode as string, blurBackground: value.blurBackground,
  };
}
function textLines(value: unknown, minimum: number): string[] {
  if (!Array.isArray(value) || value.length < minimum || value.length > 4 ||
    value.some(line => typeof line !== "string" || !line.trim() || line.length > 1000)) invalidContentResponse();
  return [...value];
}

/** Credentials may only be sent to our API for this exact content/topic/page. */
export function protectedSlideImage(value: unknown, contentId: number, topicId: number, page: number, alt: string): ContentImage {
  const expected = `/api/v1/contents/${contentId}/slides/${page}/image?topicId=${topicId}`;
  if (!isPositiveId(contentId) || !isPositiveId(topicId) || !Number.isInteger(page) || page < 1 || page > 10 || value !== expected) invalidContentResponse();
  let base: URL;
  try { base = new URL(apiBaseUrl); } catch { return invalidContentResponse(); }
  if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.pathname !== "/" || base.search || base.hash) invalidContentResponse();
  return { src: `${base.origin}${expected}`, alt, protected: true };
}

export function mapCoverImage(value: string | null | undefined, alt: string, contentId: number, topicId: number): ContentImage | undefined {
  if (typeof value === "string" && value.startsWith("/api/v1/")) {
    return protectedSlideImage(value, contentId, topicId, 1, alt);
  }
  return optionalImage(value, alt);
}

export function mapCarouselSlides(value: unknown, contentId: number, topicId: number): HomeSlideModel[] {
  if (value === undefined || (Array.isArray(value) && value.length === 0)) return [];
  if (!Array.isArray(value) || value.length < 3 || value.length > 10) invalidContentResponse();
  const sorted = [...value].sort((a, b) => Number(a?.page) - Number(b?.page));
  return sorted.map((slide: unknown, index) => {
    if (!object(slide) || slide.page !== index + 1 || typeof slide.role !== "string" || !slide.role.trim() || slide.role.length > 50 ||
      typeof slide.eyebrow !== "string" || slide.eyebrow.length > 300 || slide.width !== 1308 || slide.height !== 1632) invalidContentResponse();
    const titleLines = textLines(slide.titleLines, 1), bodySentences = textLines(slide.bodySentences, 0);
    return {
      id: `${contentId}-slide-${slide.page}`, order: slide.page, page: slide.page,
      role: slide.role, eyebrow: slide.eyebrow, titleLines, bodySentences,
      description: bodySentences.join(" "), layout: layout(slide.layout), width: slide.width, height: slide.height,
      image: protectedSlideImage(slide.imageUrl, contentId, topicId, slide.page, titleLines.join(" ")),
    };
  });
}

export function publicationMetadata(value: { publishedAt?: unknown; productionType?: unknown; sourceUrl?: unknown; sourceName?: unknown; publisher?: unknown; notices?: ContentNotice[] }): PublicationMetadata {
  const result: PublicationMetadata = {};
  if (value.publishedAt !== undefined) {
    if (value.publishedAt !== null && (typeof value.publishedAt !== "string" || !Number.isFinite(Date.parse(value.publishedAt)))) invalidContentResponse();
    result.publishedAt = value.publishedAt as string | null;
  }
  if (value.productionType !== undefined) {
    if (!["human", "ai", "hybrid"].includes(value.productionType as string)) invalidContentResponse();
    result.productionType = value.productionType as PublicationMetadata["productionType"];
  }
  const publisher = value.publisher ?? value.sourceName;
  if (publisher != null) {
    if (typeof publisher !== "string") invalidContentResponse();
    result.publisher = publisher;
  }
  if (value.sourceUrl != null) {
    if (typeof value.sourceUrl !== "string") invalidContentResponse();
    result.sourceUrl = safeSourceUrl(value.sourceUrl);
  }
  if (value.notices !== undefined) {
    if (!Array.isArray(value.notices)) invalidContentResponse();
    result.notices = value.notices.flatMap(notice => {
      if (!object(notice)) invalidContentResponse();
      return typeof notice.code === "string" && typeof notice.message === "string"
        ? [{ code: notice.code, message: notice.message }] : [];
    });
  }
  return result;
}

export function mapContentSources(value: unknown): PublicSource[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) invalidContentResponse();
  const seen = new Set<string>();
  return value.flatMap(source => {
    if (!object(source) || typeof source.url !== "string" || typeof source.role !== "string" ||
      (source.title != null && typeof source.title !== "string") || (source.publisher != null && typeof source.publisher !== "string") ||
      (source.citationOrder != null && !isPositiveId(source.citationOrder))) invalidContentResponse();
    const url = safeSourceUrl(source.url);
    if (!url || seen.has(url)) return [];
    seen.add(url);
    return [{ url, title: source.title as string | null | undefined, publisher: source.publisher as string | null | undefined,
      role: source.role, citationOrder: source.citationOrder as number | null | undefined }];
  }).sort((a, b) => (a.citationOrder ?? Infinity) - (b.citationOrder ?? Infinity));
}
