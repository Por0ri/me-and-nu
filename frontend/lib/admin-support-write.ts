import { ApiError, apiRequest, getSession } from "./api";
import { decimalId, record, timestamp } from "./admin-read-contract";
import { loadAdminReport, supportActionsFor, validSupportScope, type ReportRecord, type ReportSummary, type SupportAction, type SupportScope } from "./admin-reports";
import { withRequestDeadline } from "./request-deadline";

export type SupportRequest = { requestId: string; expectedStatus: "received" | "reviewing"; expectedUpdatedAt: string } &
  ({ action: "start_review" | "resolve" | "reject"; rationale: string } | { action: "answer"; answer: string });
export type SupportResult = { recordId: string; recordType: "report" | "inquiry"; action: SupportAction;
  rawStatus: "reviewing" | "resolved" | "rejected" | "answered"; updatedAt: string; auditLogId: string; replayed: boolean };
export type SupportFailure = { status: "login-required" | "forbidden" | "not-found" | "conflict" | "invalid" | "retry";
  message: string; retrySameRequest: boolean; reload: boolean };
export const supportLabels: Record<SupportAction, string> = { start_review: "검토 시작", resolve: "해결", reject: "반려", answer: "답변 등록" };
const requestIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const version = (value: unknown): value is string => timestamp(value) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value);
const transitions = { start_review: "reviewing", resolve: "resolved", reject: "rejected", answer: "answered" } as const;

export function supportTextError(value: string, action: SupportAction): string | null {
  const label = action === "answer" ? "답변" : "처리 사유", limit = action === "answer" ? 5000 : 2000;
  if (/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/u.test(value) || /[\ud800-\udfff]/u.test(value)) return "지원하지 않는 문자가 포함되어 있습니다.";
  if (!value.trim()) return `${label}를 입력해 주세요.`;
  if (Array.from(value.trim()).length > limit) return `${label}는 ${limit.toLocaleString("ko-KR")}자 이내로 입력해 주세요.`;
  return null;
}
export function supportAvailability(report: ReportRecord) {
  const actions = report.actions;
  if (!actions || actions.disabledReason === "permission_denied") return { allowed: [] as SupportAction[], message: "현재 계정은 조회 전용입니다." };
  if (actions.disabledReason === "terminal_state") return { allowed: [] as SupportAction[], message: "처리가 완료된 항목입니다." };
  if (!validSupportScope(actions.scope)) return { allowed: [] as SupportAction[], message: "처리 권한을 확인하지 못했습니다. 다시 조회해 주세요." };
  return { allowed: actions.allowedActions.filter(action => supportActionsFor(report.recordType, report.rawStatus).includes(action)), message: null };
}
export function prepareSupportRequest(report: ReportRecord, action: SupportAction, text: string, requestId: string): SupportRequest {
  if (!supportAvailability(report).allowed.includes(action) || !version(report.updatedAt)) throw new Error("현재 상태에서는 처리할 수 없습니다. 다시 조회해 주세요.");
  const issue = supportTextError(text, action);
  if (issue) throw new Error(issue);
  if (!requestIdPattern.test(requestId)) throw new Error("요청 번호를 확인해 주세요.");
  const common = { requestId, expectedStatus: report.rawStatus as "received" | "reviewing", expectedUpdatedAt: report.updatedAt };
  return action === "answer" ? { ...common, action, answer: text.trim() } : { ...common, action, rationale: text.trim() };
}

export async function submitSupportAction(item: Pick<ReportSummary, "id" | "recordId" | "recordType">, request: SupportRequest, scope: SupportScope): Promise<SupportResult> {
  if (!decimalId(item.recordId) || !validSupportScope(scope) || !requestIdPattern.test(request.requestId) || !version(request.expectedUpdatedAt) ||
    item.id !== `${item.recordType === "report" ? "R" : "I"}-${item.recordId}` ||
    !supportActionsFor(item.recordType, request.expectedStatus).includes(request.action) ||
    supportTextError(request.action === "answer" ? request.answer : request.rationale, request.action)) throw new Error("처리 요청을 확인해 주세요.");
  // Fetch a fresh session on every attempt, including an identical replay. Never
  // replace the actor/grant captured when the operator confirmed the action.
  const session = await withRequestDeadline(signal => getSession({ fresh: true, signal }));
  if (!session.authenticated || session.sessionState !== "active" || !session.onboardingCompleted || !session.csrfToken ||
    session.user?.accountType !== "user") throw new ApiError("로그인이 필요합니다.", 401, "AUTH_REQUIRED");
  if (!Number.isSafeInteger(session.user.id) || String(session.user.id) !== scope.actorUserId) throw new ApiError("처리를 시작한 계정이 변경되었습니다.", 409, "SUPPORT_SCOPE_CHANGED");
  const latest = await loadAdminReport(item);
  if (!validSupportScope(latest.actions.scope) || latest.actions.scope.actorUserId !== scope.actorUserId ||
    latest.actions.scope.adminAccountId !== scope.adminAccountId || latest.actions.disabledReason === "permission_denied") {
    throw new ApiError("처리를 시작한 권한이 변경되었습니다.", 409, "SUPPORT_SCOPE_CHANGED");
  }
  // Keep the timestamp string (including microseconds) and requestId unchanged.
  // A terminal latest state still permits replay of an already committed request.
  const common = { requestId: request.requestId, expectedStatus: request.expectedStatus, expectedUpdatedAt: request.expectedUpdatedAt };
  const body = request.action === "answer" ? { ...common, answer: request.answer } : { ...common, action: request.action, rationale: request.rationale };
  const path = request.action === "answer" ? `/api/v1/admin/inquiries/${item.recordId}/answer` : `/api/v1/admin/reports/${item.recordId}/actions`;
  const result = await withRequestDeadline(signal => apiRequest<unknown>(path, { method: "POST", signal, body: JSON.stringify(body), headers: {
    "X-CSRF-Token": session.csrfToken, "X-Admin-Support-Actor": scope.actorUserId, "X-Admin-Support-Grant": scope.adminAccountId,
  } }, 200));
  if (!record(result) || result.recordId !== item.recordId || result.recordType !== item.recordType || result.action !== request.action ||
    result.rawStatus !== transitions[request.action] || !version(result.updatedAt) || !decimalId(result.auditLogId) || typeof result.replayed !== "boolean") {
    throw new ApiError("처리 결과를 확인하지 못했습니다.", 200, "INVALID_ADMIN_SUPPORT_RESPONSE");
  }
  return result as SupportResult;
}
export function supportWriteFailure(error: unknown): SupportFailure {
  const failure = (status: SupportFailure["status"], message: string, retrySameRequest = false, reload = false): SupportFailure => ({ status, message, retrySameRequest, reload });
  const uncertain = failure("retry", "처리 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.", true);
  if (!(error instanceof ApiError) || ["INVALID_ADMIN_SUPPORT_RESPONSE", "INVALID_RESPONSE"].includes(error.code)) return uncertain;
  if (error.status === 401) return failure("login-required", "다시 로그인한 뒤 처리 권한을 확인해 주세요.", false, true);
  if (error.status === 403) return error.code === "CSRF_INVALID" ? failure("retry", "보안 확인이 만료되었습니다. 같은 요청으로 다시 확인해 주세요.", true)
    : failure("forbidden", "신고·문의를 처리할 권한이 없습니다.", false, true);
  if (error.status === 404) return failure("not-found", "해당 항목을 더 이상 처리할 수 없습니다.", false, true);
  if (error.status === 409) return failure("conflict", error.code === "SUPPORT_SCOPE_CHANGED" ? "처리를 시작한 계정 또는 권한이 변경되었습니다."
    : error.code === "SUPPORT_REQUEST_CONFLICT" ? "같은 요청 번호로 다른 내용이 요청되었습니다. 최신 상태를 확인해 주세요." : "다른 관리자가 먼저 처리했거나 상태가 변경되었습니다. 최신 내용을 확인해 주세요.", false, true);
  if (error.status >= 500 || error.status === 408 || error.status === 429) return uncertain;
  return failure("invalid", "입력한 처리 내용을 확인해 주세요.");
}
