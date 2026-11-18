import { apiRequest, ApiError } from "@/lib/api";
import { contentVersion, type AdminContentDetail } from "./admin-content";

export type ReviewAction = "approve" | "reject";

// Mirrors app/schemas/admin_content_review.py. The BE route is unregistered and its
// write provider denies by default. No admin screen may call submitAdminContentReview
// until BE connects the permission/audit DB and registers the route.
export type AdminContentReviewRequest = {
  requestId: string; action: ReviewAction; rationale: string;
  expectedStatus: "active" | "pending_review"; expectedJudgmentStatus: "needs_review";
  // AdminContentDetail.updatedAt verbatim. Date conversion would drop microseconds, and
  // the server's replay fingerprint keeps the offset text, so never reformat it.
  expectedUpdatedAt: string;
};
export type AdminContentReviewResult = {
  contentId: number; draftId: number; action: ReviewAction;
  status: "active" | "hidden"; judgmentStatus: "confirmed" | "failed"; draftStatus: "published" | "discarded";
  publishedAt: string | null; updatedAt: string;
  // admin_action_log row committed with the transition. replayed=true: an earlier commit of this requestId.
  auditLogId: number; replayed: boolean;
};
export type ReviewFailure = {
  status: "login-required" | "forbidden" | "not-found" | "conflict" | "invalid" | "unavailable" | "retry";
  message: string;
  // Resend the identical request object (same requestId and payload). Nothing was
  // written, or a commit is unconfirmed and the server will replay it.
  retrySameRequest: boolean;
  // Re-read the detail first; any later attempt needs a new request and requestId.
  reload: boolean;
};

const transitions = {
  approve: { status: "active", judgmentStatus: "confirmed", draftStatus: "published" },
  reject: { status: "hidden", judgmentStatus: "failed", draftStatus: "discarded" },
} as const;
const requestIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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

// Build once per confirmed intent and keep the object for retries (requestId = crypto.randomUUID()).
export function prepareReviewRequest(
  content: Pick<AdminContentDetail, "status" | "judgmentStatus" | "updatedAt">,
  action: ReviewAction, rationale: string, requestId: string,
): AdminContentReviewRequest {
  if (!canPrepareReview(content) || !contentVersion(content.updatedAt)) throw new Error("검수 가능한 상태가 아닙니다. 콘텐츠를 다시 조회해 주세요.");
  if (action !== "approve" && action !== "reject") throw new Error("검수 조치를 확인해 주세요.");
  const issue = reviewRationaleError(rationale);
  if (issue) throw new Error(issue);
  if (!requestIdPattern.test(requestId)) throw new Error("요청 ID를 확인해 주세요.");
  return {
    requestId, action, rationale: rationale.trim(),
    expectedStatus: content.status as AdminContentReviewRequest["expectedStatus"],
    expectedJudgmentStatus: "needs_review", expectedUpdatedAt: content.updatedAt,
  };
}

function integer(value: unknown): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value >= 1; }
function invalidResult(): never { throw new ApiError("검수 처리 결과를 확인하지 못했습니다.", 200, "INVALID_ADMIN_REVIEW_RESPONSE"); }

export async function submitAdminContentReview(
  contentId: number, request: AdminContentReviewRequest, signal?: AbortSignal,
): Promise<AdminContentReviewResult> {
  if (!integer(contentId) || (request.action !== "approve" && request.action !== "reject") ||
    !requestIdPattern.test(request.requestId) || !contentVersion(request.expectedUpdatedAt)) {
    throw new Error("검수 요청을 확인해 주세요.");
  }
  // Exactly the server DTO fields (extra keys are rejected), in their original values.
  const body = JSON.stringify({
    requestId: request.requestId, action: request.action, rationale: request.rationale,
    expectedStatus: request.expectedStatus, expectedJudgmentStatus: request.expectedJudgmentStatus,
    expectedUpdatedAt: request.expectedUpdatedAt,
  });
  const value = await apiRequest<unknown>(`/api/v1/admin/contents/${contentId}/review`, { method: "POST", body, signal }, 200);
  const result = value as Record<string, unknown> | null;
  const expected = transitions[request.action];
  if (!result || typeof result !== "object" || Array.isArray(result) || result.contentId !== contentId || !integer(result.draftId) ||
    result.action !== request.action || result.status !== expected.status || result.judgmentStatus !== expected.judgmentStatus ||
    result.draftStatus !== expected.draftStatus || !contentVersion(result.updatedAt) ||
    !(request.action === "approve" ? contentVersion(result.publishedAt) : result.publishedAt === null || contentVersion(result.publishedAt)) ||
    !integer(result.auditLogId) || typeof result.replayed !== "boolean") invalidResult();
  return result as AdminContentReviewResult;
}

export function reviewFailure(error: unknown): ReviewFailure {
  const failure = (status: ReviewFailure["status"], message: string, retrySameRequest = false, reload = false): ReviewFailure =>
    ({ status, message, retrySameRequest, reload });
  const retry = failure("retry", "처리 결과를 확인하지 못했습니다. 같은 요청으로 다시 시도해 주세요.", true);
  if (!(error instanceof ApiError)) return retry; // Network loss or abort: the commit may have happened.
  if (error.code === "INVALID_ADMIN_REVIEW_RESPONSE" || error.code === "INVALID_RESPONSE") return retry;
  if (error.status === 401) return failure("login-required", "로그인이 필요합니다. 다시 로그인한 뒤 콘텐츠를 다시 조회해 주세요.", false, true);
  if (error.status === 403) return error.code === "CSRF_INVALID"
    ? failure("retry", "보안 확인이 만료되었습니다. 같은 요청으로 다시 시도해 주세요.", true)
    : failure("forbidden", "콘텐츠 검수 권한이 없습니다.", false, true);
  if (error.status === 404) return error.code === "CONTENT_NOT_FOUND"
    ? failure("not-found", "콘텐츠를 찾을 수 없습니다.", false, true)
    : failure("unavailable", "검수 실행이 아직 연결되지 않았습니다.");
  if (error.status === 409) return error.code === "REVIEW_REQUEST_CONFLICT"
    ? failure("conflict", "같은 요청 ID로 다른 조치가 요청되었습니다. 콘텐츠를 다시 조회한 뒤 새로 요청해 주세요.", false, true)
    : failure("conflict", "콘텐츠 상태가 변경되었습니다. 다시 조회한 뒤 확인해 주세요.", false, true);
  if (error.status >= 500 || error.status === 408 || error.status === 429) {
    return failure("retry", "검수 저장을 완료하지 못했습니다. 같은 요청으로 다시 시도해 주세요.", true);
  }
  return failure("invalid", "검수 요청 내용을 확인해 주세요.");
}
