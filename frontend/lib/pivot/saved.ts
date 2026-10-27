import { isDetailTarget } from "./detail";
import type { HomeDetailTarget } from "./home";

// FE list model; no bookmark API/DTO or persistence contract is assumed.
export type SavedCard = HomeDetailTarget & { title: string; image?: { src: string; alt: string } };
export type SavedList = { origin: "demo" | "service"; cards: readonly SavedCard[] };
export type SavedLoadResult = { status: "ready"; saved: SavedList } | { status: "unconfigured"; message: string };
export type SavedState = SavedLoadResult | { status: "loading" } | { status: "empty" } | { status: "error"; message: string };
export interface SavedDataSource {
  readonly kind: "demo" | "service";
  load(options: { signal: AbortSignal }): Promise<SavedLoadResult>;
  subscribe?(listener: () => void): () => void;
}
// Only the explicitly injected Demo store can enable bookmark buttons.
export interface DemoBookmarkControls {
  readonly kind: "demo";
  has(target: HomeDetailTarget): boolean;
  toggle(target: HomeDetailTarget): void;
  subscribe(listener: () => void): () => void;
  getSnapshot(): number;
}

export function demoBookmarkNotice(wasSaved: boolean): string {
  return `Demo 저장 목록${wasSaved ? "에서 제외" : "에 추가"}했어요. 새로고침하면 초기화되며 서버에는 저장하지 않아요.`;
}

export function createSavedRequestController(source: SavedDataSource, publish: (state: SavedState) => void) {
  let active: AbortController | undefined;
  return {
    async load() {
      active?.abort(); const request = new AbortController(); active = request;
      publish({ status: "loading" });
      try {
        const result = await source.load({ signal: request.signal });
        if (request.signal.aborted) return;
        if (result.status === "unconfigured") { publish(result); return; }
        const list = result.saved;
        const ids = new Set<string>();
        if (list.origin !== source.kind || !Array.isArray(list.cards) || list.cards.some((card) => {
          if (!card || typeof card.postId !== "string" || typeof card.contentId !== "string" || !isDetailTarget(card)
            || typeof card.title !== "string" || !card.title.trim() || ids.has(card.contentId)) return true;
          ids.add(card.contentId); return false;
        })) throw new Error("Saved source or identity mismatch");
        publish(list.cards.length ? result : { status: "empty" });
      } catch {
        if (!request.signal.aborted) publish({ status: "error", message: "저장 목록을 불러오지 못했어요. 다시 시도해주세요." });
      }
    },
    cancel() { active?.abort(); },
  };
}
