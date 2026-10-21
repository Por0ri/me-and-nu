import type { SavedDataSource } from "./saved";

// Add a Service adapter only after auth, listing and bookmark contracts are agreed.
// A failed/unconfigured Service never imports or falls back to Demo data.
export const pendingSavedSource: SavedDataSource = {
  kind: "service",
  async load() { return { status: "unconfigured", message: "저장한 콘텐츠 화면을 준비하고 있어요." }; },
};
