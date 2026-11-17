import type { CarouselLayout } from "@/lib/api";
import type { ContentImage, PublicationMetadata } from "./carousel-data";
import type { RecentSearchStore } from "./recent-search";

// Display models shared by Demo and the authenticated API adapter.
export type HomeSlideModel = {
  id: string;
  order: number;
  image?: ContentImage;
  description: string;
  page?: number;
  role?: string;
  eyebrow?: string;
  titleLines?: readonly string[];
  bodySentences?: readonly string[];
  layout?: CarouselLayout;
  width?: number;
  height?: number;
};

export type HomeCardModel = PublicationMetadata & {
  id: string;
  title: string;
  detailContentId: string;
  topicId?: number;
  saved?: boolean;
  liked?: boolean;
  preference?: import("@/lib/api").Preference | null;
  recommendationId?: number;
  recommendationReason?: string;
  slides: readonly HomeSlideModel[];
};

export type HomeDetailTarget = { postId: string; contentId: string; topicId?: number; recommendationId?: number; recommendationReason?: string };
export type HomeDetailHandler = (target: HomeDetailTarget) => void;

/** Keep source data immutable; display order is explicit rather than inferred from IDs. */
export function orderedHomeSlides(slides: readonly HomeSlideModel[]): HomeSlideModel[] {
  return [...slides].sort((a, b) => a.order - b.order);
}

export type HomeViewModel = {
  origin: "demo" | "service";
  cards: readonly HomeCardModel[];
};

export type HomeLoadResult =
  | { status: "ready"; home: HomeViewModel }
  | HomeLoadError
  | { status: "unconfigured"; message: string };

export type HomeLoadError = {
  status: "error";
  reason: "unauthorized" | "forbidden" | "network" | "invalid-response" | "server";
  message: string;
};

/** Work search: matching works' official names (even without cards) and their cards. */
export type HomeSearchResult = { works: string[]; cards: HomeCardModel[] };
/** A work suggested under the search box while typing, like onboarding Q1. */
export type HomeWorkSuggestion = { id: number; name: string };

export interface HomeDataSource {
  readonly kind: "demo" | "service";
  load(options: { signal: AbortSignal }): Promise<HomeLoadResult>;
  /** Cards about works matching the query (only workId's when a suggestion was picked), shown alone as
   *  the search result. Demo has no search. */
  search?(query: string, options: { signal: AbortSignal; workId?: number }): Promise<HomeSearchResult>;
  /** Up to three works whose name or alias contains the query, with or without cards. */
  suggest?(query: string, options: { signal: AbortSignal }): Promise<HomeWorkSuggestion[]>;
  /** The member's recent searches in this browser. */
  readonly recent?: RecentSearchStore;
}

export type HomeState =
  | { status: "loading" }
  | { status: "ready"; home: HomeViewModel }
  | { status: "empty" }
  | { status: "error"; message: string; reason?: HomeLoadError["reason"] }
  | { status: "unconfigured"; message: string };

/** Instance-local request lifecycle. Cancellation also guards sources that ignore abort. */
export function createHomeRequestController(source: HomeDataSource, publish: (state: HomeState) => void, timeoutMs = 15_000) {
  let generation = 0;
  let active: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  return {
    async load(): Promise<void> {
      const request = ++generation;
      clearTimeout(timer);
      active?.abort();
      const controller = new AbortController();
      active = controller;
      publish({ status: "loading" });
      timer = setTimeout(() => {
        if (request !== generation || controller.signal.aborted) return;
        controller.abort();
        publish({ status: "error", reason: "network", message: "콘텐츠 준비가 지연되고 있어요. 다시 시도해 주세요." });
      }, timeoutMs);
      try {
        const result = await source.load({ signal: controller.signal });
        if (request !== generation || controller.signal.aborted) return;
        if (result.status !== "ready") {
          publish(result);
        } else {
          // A miswired service source must never present demo fixtures as a live feed.
          if (result.home.origin !== source.kind) throw new Error("Home source origin mismatch.");
          publish(result.home.cards.length > 0 ? { status: "ready", home: result.home } : { status: "empty" });
        }
      } catch {
        if (request === generation && !controller.signal.aborted) {
          publish({ status: "error", message: "콘텐츠를 불러오지 못했어요. 다시 시도해 주세요." });
        }
      } finally {
        if (active === controller) { clearTimeout(timer); active = null; }
      }
    },
    cancel() {
      generation++;
      clearTimeout(timer);
      active?.abort();
      active = null;
    },
  };
}
