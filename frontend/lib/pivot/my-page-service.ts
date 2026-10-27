import type { MyPageDataSource } from "./my-page";

// Connect an adapter here only after authentication and the BE contract are agreed.
// No V1.1 assumption, network call, Consumer session reuse, or Mock fallback.
export const pendingMyPageSource: MyPageDataSource = {
  kind: "service",
  async load() { return { status: "unconfigured", message: "My Page의 프로필·인증·설정 연결을 준비하고 있어요." }; },
};
