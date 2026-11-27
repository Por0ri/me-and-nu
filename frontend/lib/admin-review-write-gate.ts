import { apiBaseUrl } from "@/lib/api";

// Admin review POSTs are enabled only for explicitly approved API origins.
// Public environment values select an API; they cannot approve another origin.
// Main promotion keeps Checking available until operational cutover is verified.
// The BE remains the authority on permission, actor/grant scope, version and state.
export type ReviewWriteActivation = { apiOrigin: string; approvedBy: string; approvedOn: string; reference: string };
const ACTIVATIONS: readonly ReviewWriteActivation[] = [
  {
    apiOrigin: "https://api.checking.me-nu.kr",
    approvedBy: "service owner (explicit Checking UI activation approval)",
    approvedOn: "2026-11-20",
    reference: "docs/ADMIN_REVIEW_UI_ACTIVATION_2026-11-20.md",
  },
  {
    apiOrigin: "https://api.me-nu.kr",
    approvedBy: "service owner (Main functional promotion approval)",
    approvedOn: "2026-11-24",
    reference: "docs/MAIN_ADMIN_REVIEW_PROMOTION_2026-11-24.md",
  },
];

export type ReviewWriteGate = { enabled: boolean; reason: string | null };

function apiOrigin(value: string): string | null {
  try {
    const url = new URL(value);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return null;
    return url.origin;
  } catch {
    return null;
  }
}

// The explicit override is used by isolated fixtures; null always disables writes.
export function reviewWriteGate(apiBase: string = apiBaseUrl, activation?: ReviewWriteActivation | null): ReviewWriteGate {
  if (activation === null) return { enabled: false, reason: "승인·반려 실행은 아직 활성화되지 않았습니다. BE 실환경 준비와 별도 승인 후 코드 변경으로만 켭니다." };
  const origin = apiOrigin(apiBase);
  if (!origin) return { enabled: false, reason: "API 주소를 확인할 수 없어 승인·반려를 실행하지 않습니다." };
  const approvals = activation === undefined ? ACTIVATIONS : [activation];
  return approvals.some(approval => origin === apiOrigin(approval.apiOrigin))
    ? { enabled: true, reason: null }
    : { enabled: false, reason: "승인되지 않은 API 환경에서는 승인·반려를 실행하지 않습니다." };
}
