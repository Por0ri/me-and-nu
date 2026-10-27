import type { ConsentDraft } from "./consent";
import type { UiSelection } from "./onboarding";

// Adapter port only. Wire the agreed BE DTO here; never map these UI IDs to topicId/subtopicIds.
export interface OnboardingSubmissionService {
  readonly availability: "unconfigured" | "configured";
  submitSelection(selection: UiSelection, consent: ConsentDraft): Promise<void>;
}
export const pendingOnboardingService: OnboardingSubmissionService = {
  availability: "unconfigured",
  async submitSelection() { throw new Error("새 온보딩 요청·응답 계약이 아직 연결되지 않았어요. 가입과 서버 저장은 진행되지 않았어요."); },
};
