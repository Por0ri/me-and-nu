import type { Policy } from "@/lib/api";
import type { ConsentDraft } from "./consent";

export type DemoStep = "splash" | "login" | "consent" | "onboarding" | "loading" | "home";
export type DemoState = { step: DemoStep; consent: ConsentDraft | null };

// Explicitly non-binding demo fixtures, separate from the dynamic policy API.
export const demoPolicies: Policy[] = [
  { type: "terms", policyVersion: "demo-only", required: true, text: "시연용 약관 예시입니다. 실제 서비스 이용약관이 아니며 선택값을 서버에 제출하지 않습니다." },
  { type: "privacy", policyVersion: "demo-only", required: true, text: "시연용 개인정보 정책 예시입니다. 실제 개인정보 처리 동의가 아니며 정보를 수집하지 않습니다." },
];
