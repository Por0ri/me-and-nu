import { ApiError, apiRequest, getSession } from "./api";
import { decimalId, record } from "./admin-read-contract";
import { initialNoticeQuery, loadAdminNotices, noticeVersion, validNoticeScope, noticeActionsFor, type NoticeAction, type NoticeCapability, type NoticeDraft, type NoticeRecord, type NoticeScope, type NoticeStatus } from "./admin-notices";
import { withRequestDeadline } from "./request-deadline";

export type NoticeRequest = { requestId: string; action: NoticeAction; title?: string; body?: string; scheduledAt?: string | null; expectedStatus?: NoticeStatus; expectedUpdatedAt?: string };
export type NoticeResult = { noticeId: string; status: NoticeStatus; scheduledAt: string | null; publishedAt: string | null; updatedAt: string; auditLogId: string; replayed: boolean };
export type NoticeWriteFailure = { message: string; uncertain: boolean; reload: boolean };
export const noticeActionLabels: Record<NoticeAction, string> = { save_draft: "임시 저장", publish: "게시", schedule: "예약", cancel_schedule: "예약 취소" };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function noticeTextError(title: string, body: string) {
  for (const [value, limit, label] of [[title, 100, "공지 제목"], [body, 5000, "공지 내용"]] as const) {
    if (/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/u.test(value) || /[\ud800-\udfff]/u.test(value)) return "지원하지 않는 문자가 포함되어 있습니다.";
    if (!value.trim()) return `${label}을 입력해 주세요.`;
    if (Array.from(value.trim()).length > limit) return `${label}은 ${limit.toLocaleString("ko-KR")}자 이내로 입력해 주세요.`;
  }
  return null;
}
export function scheduleInstant(date: string, time: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) throw new Error("예약 날짜와 시간을 확인해 주세요.");
  const value = `${date}T${time}:00+09:00`, parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime()) || new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(parsed) !== date) throw new Error("예약 날짜를 확인해 주세요.");
  return value;
}
export function prepareNoticeRequest(item: NoticeRecord | null, action: NoticeAction, draft: NoticeDraft, capabilities: NoticeCapability | undefined, requestId: string, now = Date.now()): NoticeRequest {
  const permission = item?.actions ?? capabilities;
  const available = item ? noticeActionsFor(item.status) : ["save_draft", "publish", "schedule"];
  if (!permission || !validNoticeScope(permission.scope) || permission.disabledReason !== null ||
    !permission.allowedActions.includes(item ? action : "create") || !available.includes(action)) throw new Error("현재 상태에서는 처리할 수 없습니다. 권한과 공지를 다시 확인해 주세요.");
  if (!uuid.test(requestId) || (item && (!decimalId(item.id) || !noticeVersion(item.updatedAt)))) throw new Error("요청 정보를 확인해 주세요.");
  const common = { requestId, action, ...(item ? { expectedStatus: item.status, expectedUpdatedAt: item.updatedAt } : {}) };
  if (action === "cancel_schedule") return common;
  const issue = noticeTextError(draft.title, draft.body); if (issue) throw new Error(issue);
  const scheduledAt = action === "schedule" ? scheduleInstant(draft.date, draft.time) : null;
  if (scheduledAt && Date.parse(scheduledAt) <= now) throw new Error("예약 시간은 현재보다 이후여야 합니다. 한국 시간을 기준으로 선택해 주세요.");
  return { ...common, title: draft.title.trim(), body: draft.body.trim(), scheduledAt };
}
export async function submitNoticeRequest(id: string | null, request: NoticeRequest, scope: NoticeScope): Promise<NoticeResult> {
  if ((id !== null && !decimalId(id)) || !validNoticeScope(scope) || !uuid.test(request.requestId) ||
    (id !== null && (!request.expectedStatus || !noticeVersion(request.expectedUpdatedAt) || !noticeActionsFor(request.expectedStatus).includes(request.action))) ||
    (id === null && request.action === "cancel_schedule") || (request.action !== "cancel_schedule" &&
      (typeof request.title !== "string" || typeof request.body !== "string" || noticeTextError(request.title, request.body) ||
        (request.action === "schedule" ? !noticeVersion(request.scheduledAt) : request.scheduledAt !== null)))) throw new Error("공지 요청을 확인해 주세요.");
  const session = await withRequestDeadline(signal => getSession({ fresh: true, signal }));
  if (!session.authenticated || session.sessionState !== "active" || !session.onboardingCompleted || session.user?.accountType !== "user" || !session.csrfToken) throw new ApiError("", 401, "AUTH_REQUIRED");
  if (!Number.isSafeInteger(session.user.id) || String(session.user.id) !== scope.actorUserId) throw new ApiError("", 409, "NOTICE_SCOPE_CHANGED");
  // Refresh permissions, but never replace the actor/grant or original request.
  // A scheduled time may now be in the past when confirming a committed retry.
  const current = (await loadAdminNotices(initialNoticeQuery)).capabilities;
  if (!current || !validNoticeScope(current.scope) || current.disabledReason !== null || !current.allowedActions.includes("create") ||
    current.scope.actorUserId !== scope.actorUserId || current.scope.adminAccountId !== scope.adminAccountId) throw new ApiError("", 409, "NOTICE_SCOPE_CHANGED");
  const result = await withRequestDeadline(signal => apiRequest<unknown>(id === null ? "/api/v1/admin/notices" : `/api/v1/admin/notices/${id}/actions`, {
    method: "POST", signal, body: JSON.stringify(request), headers: { "X-CSRF-Token": session.csrfToken, "X-Admin-Notice-Actor": scope.actorUserId, "X-Admin-Notice-Grant": scope.adminAccountId },
  }, 200));
  const status = request.action === "publish" ? "published" : request.action === "schedule" ? "scheduled" : "draft";
  if (!record(result) || !decimalId(result.noticeId) || (id !== null && result.noticeId !== id) || result.status !== status || !noticeVersion(result.updatedAt) ||
    !decimalId(result.auditLogId) || typeof result.replayed !== "boolean" || !(result.scheduledAt === null || noticeVersion(result.scheduledAt)) ||
    (status === "published" ? !noticeVersion(result.publishedAt) : result.publishedAt !== null) || (status === "scheduled" && !noticeVersion(result.scheduledAt))) throw new ApiError("", 200, "INVALID_NOTICE_RESPONSE");
  return result as NoticeResult;
}
export function noticeWriteFailure(error: unknown): NoticeWriteFailure {
  const uncertain = { message: "처리 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.", uncertain: true, reload: false };
  if (!(error instanceof ApiError) || ["INVALID_NOTICE_RESPONSE", "INVALID_RESPONSE"].includes(error.code) || error.status >= 500 || [408, 429].includes(error.status)) return uncertain;
  if (error.status === 401) return { message: "다시 로그인한 뒤 처리 권한을 확인해 주세요.", uncertain: false, reload: true };
  if (error.status === 403) return error.code === "CSRF_INVALID" ? { ...uncertain, message: "보안 확인이 만료되었습니다. 같은 요청으로 다시 확인해 주세요." }
    : { message: "공지를 작성하거나 발행할 권한이 없습니다.", uncertain: false, reload: true };
  if (error.status === 409) return { message: error.code === "NOTICE_SCOPE_CHANGED" ? "처리를 시작한 계정 또는 권한이 변경되었습니다." : "공지 상태가 변경되었거나 같은 요청 번호의 내용이 다릅니다. 최신 내용을 다시 확인해 주세요.", uncertain: false, reload: true };
  if (error.status === 404) return { message: "해당 공지를 찾을 수 없습니다.", uncertain: false, reload: true };
  return { message: "입력한 제목·본문·예약 시간을 확인해 주세요.", uncertain: false, reload: false };
}
