import { mockMagazines } from "./magazine-mock";
import type { HomeDetailTarget } from "./home";
import type { DemoBookmarkControls, SavedDataSource } from "./saved";

export type DemoSavedSource = SavedDataSource & DemoBookmarkControls & { reset(): void };

// One store per DemoFlow mount. No module-global user state or browser storage.
export function createDemoSavedSource(): DemoSavedSource {
  const initial = mockMagazines.map((article) => article.contentId);
  let ids = [...initial]; let revision = 0;
  const listeners = new Set<() => void>();
  const matches = (target: HomeDetailTarget) => mockMagazines.find((article) => article.postId === target.postId && article.contentId === target.contentId);
  function publish() { revision++; listeners.forEach((listener) => listener()); }
  return {
    kind: "demo",
    async load() {
      return { status: "ready", saved: { origin: "demo", cards: ids.flatMap((id) => {
        const article = mockMagazines.find((item) => item.contentId === id);
        return article ? [structuredClone({ postId: article.postId, contentId: article.contentId, title: article.title, image: article.image })] : [];
      }) } };
    },
    has(target) { return !!matches(target) && ids.includes(target.contentId); },
    toggle(target) {
      if (!matches(target)) return;
      ids = ids.includes(target.contentId) ? ids.filter((id) => id !== target.contentId) : [...ids, target.contentId];
      publish();
    },
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    getSnapshot() { return revision; },
    reset() { ids = [...initial]; publish(); },
  };
}
