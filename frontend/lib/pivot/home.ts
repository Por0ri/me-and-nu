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

export type HomePageLoader = (options: { signal: AbortSignal }) => Promise<HomeLoadResult>;
export type HomeLoadResult =
  | { status: "ready"; home: HomeViewModel; nextPage?: HomePageLoader }
  | HomeLoadError
  | { status: "unconfigured"; message: string };

export type HomeLoadError = {
  status: "error";
  reason: "unauthorized" | "forbidden" | "scope-changed" | "network" | "invalid-response" | "server";
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
  | { status: "ready"; home: HomeViewModel; pagination?: { status: "idle" | "loading" | "error"; message?: string } }
  | { status: "empty" }
  | { status: "error"; message: string; reason?: HomeLoadError["reason"] }
  | { status: "unconfigured"; message: string };

/** Instance-local request lifecycle. Cancellation also guards sources that ignore abort. */
export function createHomeRequestController(source: HomeDataSource, publish: (state: HomeState) => void, timeoutMs = 15_000) {
  let generation = 0;
  let active: AbortController | null = null;
  let appending = false;
  let state: HomeState = { status: "loading" };
  let nextPage: HomePageLoader | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const update = (next: HomeState) => { state = next; publish(next); };
  function cancel() {
    generation++;
    clearTimeout(timer);
    active?.abort(); active = null; appending = false;
  }
  async function requestPage(load: HomePageLoader, previous?: HomeViewModel): Promise<void> {
    cancel();
    const request = generation;
    const controller = new AbortController();
    active = controller; appending = !!previous;
    if (!previous) nextPage = undefined;
    update(previous ? { status: "ready", home: previous, pagination: { status: "loading" } } : { status: "loading" });
    function fail(error: HomeLoadError) {
      // Old-account or old-Topic cards must not survive an ownership failure.
      if (previous && !["unauthorized", "forbidden", "scope-changed"].includes(error.reason)) {
        update({ status: "ready", home: previous, pagination: { status: "error", message: error.message } });
      } else { nextPage = undefined; update(error); }
    }
    timer = setTimeout(() => {
      if (request !== generation || controller.signal.aborted) return;
      controller.abort(); active = null; appending = false;
      fail({ status: "error", reason: "network", message: "콘텐츠 준비가 지연되고 있어요. 다시 시도해 주세요." });
    }, timeoutMs);
    try {
      const result = await load({ signal: controller.signal });
      if (request !== generation || controller.signal.aborted) return;
      if (result.status === "error") { fail(result); return; }
      if (result.status !== "ready") { nextPage = undefined; update(result); return; }
      if (result.home.origin !== source.kind) throw new Error("Home source origin mismatch.");
      const cards = [...(previous?.cards ?? [])];
      const ids = new Set(cards.map(card => `${card.topicId}:${card.id}`));
      for (const card of result.home.cards) {
        const key = `${card.topicId}:${card.id}`;
        if (!ids.has(key)) { ids.add(key); cards.push(card); }
      }
      nextPage = result.nextPage;
      update(cards.length || nextPage ? { status: "ready", home: { origin: source.kind, cards },
        ...(nextPage ? { pagination: { status: "idle" as const } } : {}) } : { status: "empty" });
    } catch {
      if (request === generation && !controller.signal.aborted) {
        fail({ status: "error", reason: "network", message: "콘텐츠를 불러오지 못했어요. 다시 시도해 주세요." });
      }
    } finally {
      if (active === controller) { clearTimeout(timer); active = null; appending = false; }
    }
  }
  return {
    load: () => requestPage(options => source.load(options)),
    async loadMore() {
      if (active || !nextPage || state.status !== "ready") return;
      await requestPage(nextPage, state.home);
    },
    cancelMore() {
      if (!appending) return;
      cancel();
      if (state.status === "ready") update({ ...state, pagination: { status: "idle" } });
    },
    /** 지금 보이는 피드(불러 둔 카드 + 다음 페이지를 받을 함수). TALK 에 다녀오는 동안 기억해 둔다 (home-feed-memory.ts) */
    resumable(): { home: HomeViewModel; nextPage?: HomePageLoader } | null {
      return state.status === "ready" ? { home: state.home, ...(nextPage ? { nextPage } : {}) } : null;
    },
    /** 기억해 둔 피드를 다시 받지 않고 그대로 펼친다. 받던 중이던 다음 페이지는 다시 받을 수 있게 둔다 */
    resume(kept: { home: HomeViewModel; nextPage?: HomePageLoader }) {
      cancel();
      nextPage = kept.nextPage;
      update(kept.nextPage ? { status: "ready", home: kept.home, pagination: { status: "idle" } } : { status: "ready", home: kept.home });
    },
    cancel,
  };
}
