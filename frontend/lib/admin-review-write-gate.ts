import { apiBaseUrl } from "@/lib/api";

// Admin review POSTs are enabled only for the approved Checking API origin.
//
// There is deliberately no environment variable, query, storage or header switch: a
// NEXT_PUBLIC value alone cannot approve a new origin. This activation follows the
// deployed scope contract, Main withdrawal protection, real admin OAuth/API verification
// and explicit user approval documented below. Main and other API origins stay disabled.
// Main and Checking share one DB; live validation uses dedicated synthetic content.
// The BE remains the final authority on permission, version and state for every POST.
export type ReviewWriteActivation = { apiOrigin: string; approvedBy: string; approvedOn: string; reference: string };
const ACTIVATION: ReviewWriteActivation | null = {
  apiOrigin: "https://api.checking.me-nu.kr",
  approvedBy: "service owner (explicit Checking UI activation approval)",
  approvedOn: "2026-11-20",
  reference: "docs/ADMIN_REVIEW_UI_ACTIVATION_2026-11-20.md",
};

export type ReviewWriteGate = { enabled: boolean; reason: string | null };

export function reviewWriteGate(apiBase: string = apiBaseUrl, activation: ReviewWriteActivation | null = ACTIVATION): ReviewWriteGate {
  if (!activation) return { enabled: false, reason: "승인·반려 실행은 아직 활성화되지 않았습니다. BE 실환경 준비와 별도 승인 후 코드 변경으로만 켭니다." };
  let origin: string;
  try { origin = new URL(apiBase).origin; } catch { return { enabled: false, reason: "API 주소를 확인할 수 없어 승인·반려를 실행하지 않습니다." }; }
  return origin === new URL(activation.apiOrigin).origin
    ? { enabled: true, reason: null }
    : { enabled: false, reason: "승인되지 않은 API 환경에서는 승인·반려를 실행하지 않습니다." };
}
