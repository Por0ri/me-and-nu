// FE display models only. These are not the final Pivot API or recommendation contract.
export type HomeSlideModel = {
  id: string;
  order: number;
  image?: { src: string; alt: string };
  description: string;
};

export type HomeCardModel = {
  id: string;
  title: string;
  detailContentId: string;
  topicId?: string;
  slides: readonly HomeSlideModel[];
};

export type HomeDetailTarget = { postId: string; contentId: string; topicId?: string };
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

export interface HomeDataSource {
  readonly kind: "demo" | "service";
  load(options: { signal: AbortSignal }): Promise<HomeLoadResult>;
}

export type HomeState =
  | { status: "loading" }
  | { status: "ready"; home: HomeViewModel }
  | { status: "empty" }
  | { status: "error"; message: string; reason?: HomeLoadError["reason"] }
  | { status: "unconfigured"; message: string };

/** Instance-local request lifecycle. Cancellation also guards sources that ignore abort. */
export function createHomeRequestController(source: HomeDataSource, publish: (state: HomeState) => void) {
  let generation = 0;
  let active: AbortController | null = null;
  return {
    async load(): Promise<void> {
      const request = ++generation;
      active?.abort();
      const controller = new AbortController();
      active = controller;
      publish({ status: "loading" });
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
        if (active === controller) active = null;
      }
    },
    cancel() {
      generation++;
      active?.abort();
      active = null;
    },
  };
}
