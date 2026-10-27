import type { HomeDataSource } from "./home";

// No HTTP calls or Mock fallback until the Pivot Home contract is agreed.
export const pendingHomeSource: HomeDataSource = {
  kind: "service",
  async load() {
    return { status: "unconfigured", message: "콘텐츠 서비스를 준비하고 있어요. 아직 피드를 불러올 수 없어요." };
  },
};
