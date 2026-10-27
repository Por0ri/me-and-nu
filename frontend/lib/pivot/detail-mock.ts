import type { DetailDataSource } from "./detail";
import { mockMagazines } from "./magazine-mock";

export const mockDetailSource: DetailDataSource = {
  kind: "demo",
  async load(target) {
    const article = mockMagazines.find((item) => item.contentId === target.contentId && item.postId === target.postId);
    return article ? { status: "ready", article: structuredClone(article) } : { status: "not-found" };
  },
};

// Explicit Demo QA cases only; selected by the server-gated Demo route.
const emptyDetailSource: DetailDataSource = { kind: "demo", async load() { return { status: "empty" }; } };
const errorDetailSource: DetailDataSource = { kind: "demo", async load() { throw new Error("Demo QA error"); } };
export function mockDetailSourceForPreview(scenario?: string): DetailDataSource {
  return scenario === "empty" ? emptyDetailSource : scenario === "error" ? errorDetailSource : mockDetailSource;
}
