import { bookmarkContent, unbookmarkContent } from "@/lib/api";
import type { HomeDetailTarget } from "./home";
import type { DemoBookmarkControls } from "./saved";
import { requirePivotApi } from "./services";

export interface ServiceBookmarkControls {
  readonly kind: "service";
  has(target: HomeDetailTarget, serverValue?: boolean): boolean;
  isPending(target: HomeDetailTarget): boolean;
  toggle(target: HomeDetailTarget, serverValue?: boolean): Promise<boolean>;
  subscribe(listener: () => void): () => void;
  subscribeChanges(listener: () => void): () => void;
  getSnapshot(): number;
}
export type BookmarkControls = DemoBookmarkControls | ServiceBookmarkControls;

function identity(target: HomeDetailTarget) {
  const contentId = Number(target.contentId);
  if (!/^[1-9]\d*$/.test(target.contentId) || !Number.isSafeInteger(contentId)
    || !Number.isSafeInteger(target.topicId) || (target.topicId ?? 0) <= 0) {
    throw new Error("콘텐츠의 관심 분야를 확인할 수 없어요. 홈에서 다시 열어 주세요.");
  }
  return { contentId, topicId: target.topicId!, key: `${target.topicId}:${contentId}` };
}

/** One authenticated screen owns this store. Only successful writes replace server values. */
export function createServiceBookmarkControls(api = { bookmarkContent, unbookmarkContent }, allow = requirePivotApi): ServiceBookmarkControls {
  const saved = new Map<string, boolean>();
  const pending = new Map<string, Promise<boolean>>();
  const listeners = new Set<() => void>();
  const changes = new Set<() => void>();
  let revision = 0;
  function emit() { revision++; listeners.forEach((listener) => listener()); }
  return {
    kind: "service",
    has(target, serverValue = false) { return saved.get(identity(target).key) ?? serverValue; },
    isPending(target) { return pending.has(identity(target).key); },
    toggle(target, serverValue = false) {
      allow();
      const { key, contentId, topicId } = identity(target);
      const existing = pending.get(key);
      if (existing) return existing;
      const wasSaved = saved.get(key) ?? serverValue;
      // Start on the next microtask so the lock is visible before the request begins.
      const request = Promise.resolve().then(async () => {
        if (wasSaved) await api.unbookmarkContent(contentId, topicId);
        else {
          const result = await api.bookmarkContent(contentId, topicId);
          if (result.contentId !== contentId || result.topicId !== topicId) {
            throw new Error("저장 결과를 확인할 수 없어요. 다시 시도해 주세요.");
          }
        }
        saved.set(key, !wasSaved);
        changes.forEach((listener) => listener());
        return !wasSaved;
      }).finally(() => { pending.delete(key); emit(); });
      pending.set(key, request);
      emit();
      return request;
    },
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    subscribeChanges(listener) { changes.add(listener); return () => { changes.delete(listener); }; },
    getSnapshot() { return revision; },
  };
}

export function bookmarkValue(controls: BookmarkControls, target: HomeDetailTarget, serverValue = false): boolean {
  return controls.kind === "service" ? controls.has(target, serverValue) : controls.has(target);
}

export function bookmarkPending(controls: BookmarkControls | undefined, target: HomeDetailTarget): boolean {
  return controls?.kind === "service" && controls.isPending(target);
}
