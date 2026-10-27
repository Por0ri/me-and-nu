import type { DetailDataSource } from "./detail";

// Integration seam only. Endpoint, auth, wire DTO, error codes and mapping await
// the BE contract. No request and no Mock fallback in the Service path.
export const pendingDetailSource: DetailDataSource = {
  kind: "service",
  async load() {
    return { status: "unconfigured", message: "콘텐츠 상세 서비스를 준비하고 있어요. 아직 글을 불러올 수 없어요." };
  },
};
