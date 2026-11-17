import type { AdminContentDetail } from "./admin-content";

export type ReviewAction = "approve" | "reject";

// Presentation eligibility only; the server also checks Draft, deletion,
// version, a fresh write grant, CSRF and transactional audit persistence.
export function canPrepareReview(content: Pick<AdminContentDetail, "status" | "judgmentStatus">): boolean {
  return content.judgmentStatus === "needs_review" && ["active", "pending_review"].includes(content.status);
}

export function reviewRationaleError(value: string): string | null {
  if (/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/u.test(value) || /[\ud800-\udfff]/u.test(value)) return "지원하지 않는 문자가 포함되어 있습니다.";
  if (!value.trim()) return "조치 사유를 입력해 주세요.";
  if (Array.from(value.trim()).length > 2000) return "조치 사유는 2,000자 이내로 입력해 주세요.";
  return null;
}
