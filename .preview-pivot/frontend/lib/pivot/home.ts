// FE presentation port only. No recommendation, search, category or backend ID contract.
export type HomeCardModel = {
  id: string;
  title: string;
  summary: string;
  image?: { src: string; alt: string };
};

export type HomeViewModel = {
  origin: "demo" | "service";
  cards: readonly HomeCardModel[];
};

export type HomeLoadResult =
  | { status: "ready"; home: HomeViewModel }
  | { status: "unconfigured"; message: string };

export interface HomeDataSource {
  readonly kind: "demo" | "service";
  load(options: { signal: AbortSignal }): Promise<HomeLoadResult>;
}

export type HomeState =
  | { status: "loading" }
  | { status: "ready"; home: HomeViewModel }
  | { status: "empty" }
  | { status: "error"; message: string }
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
        if (result.status === "unconfigured") {
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
