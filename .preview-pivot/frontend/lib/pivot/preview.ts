import type { Policy } from "@/lib/api";

// Figma visual fixtures. These are never returned by the real API adapter.
export const previewPolicies: Policy[] = [
  { type: "terms", policyVersion: "figma-preview-only", required: true, text: "디자인 확인용 예시입니다. 실제 서비스 이용약관이 아닙니다." },
  { type: "privacy", policyVersion: "figma-preview-only", required: true, text: "디자인 확인용 예시입니다. 실제 개인정보 처리 정책이 아닙니다." },
];
