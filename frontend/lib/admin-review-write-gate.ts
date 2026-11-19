import { apiBaseUrl } from "@/lib/api";

// Admin review POSTs from the UI are OFF in every build and environment.
//
// There is deliberately no environment variable, query, storage or header switch: a
// NEXT_PUBLIC value alone can never enable writes. Activation is a reviewed code change
// that replaces ACTIVATION below, and only after the BE confirms on the target server:
//   1. migration 47c1a8d6b302 applied to the target DB,
//   2. PR #89 deployed to that API server,
//   3. real viewer/operator/admin grants issued by the BE,
//   4. dedicated review content that is not live service content (Main and Checking
//      currently share one DB, so no existing service content may be used),
//   5. an approved live E2E plan.
// Even when activated, writes go only to that exact API origin. The BE remains the final
// authority on permission, version and state for every POST.
export type ReviewWriteActivation = { apiOrigin: string; approvedBy: string; approvedOn: string; reference: string };
const ACTIVATION: ReviewWriteActivation | null = null;

export type ReviewWriteGate = { enabled: boolean; reason: string | null };

export function reviewWriteGate(apiBase: string = apiBaseUrl, activation: ReviewWriteActivation | null = ACTIVATION): ReviewWriteGate {
  if (!activation) return { enabled: false, reason: "승인·반려 실행은 아직 활성화되지 않았습니다. BE 실환경 준비와 별도 승인 후 코드 변경으로만 켭니다." };
  let origin: string;
  try { origin = new URL(apiBase).origin; } catch { return { enabled: false, reason: "API 주소를 확인할 수 없어 승인·반려를 실행하지 않습니다." }; }
  return origin === new URL(activation.apiOrigin).origin
    ? { enabled: true, reason: null }
    : { enabled: false, reason: "승인되지 않은 API 환경에서는 승인·반려를 실행하지 않습니다." };
}
