import { ApiError, type ReportInput, type ReportResponse } from "@/lib/api";

export const REPORT_LIMIT = 500;
export const reportReasons = [
  { value: "wrong_info", label: "잘못된 정보" },
  { value: "spoiler", label: "스포일러" },
  { value: "rights", label: "저작권·권리 침해" },
  { value: "inappropriate", label: "불쾌하거나 부적절한 내용" },
  { value: "other", label: "기타" },
] as const;
export type ReportDraft = { reasonCode: ReportInput["reasonCode"] | null; detail: string };
export type ReportStatus = "editing" | "submitting" | "received" | "error" | "duplicate" | "missing" | "unavailable" | "uncertain";
export type ReportState = ReportDraft & { status: ReportStatus; message: string };
export type ReportSource = {
  kind: "service";
  assertCurrent(): void;
  submit(input: ReportInput, signal: AbortSignal): Promise<ReportResponse>;
};
export class ReportSessionChanged extends Error {}
export class ReportPreflightFailed extends Error {}
export class ReportBlocked extends Error {
  constructor(public readonly status: "duplicate" | "uncertain") { super(status); }
}
export const reportUncertainMessage = "신고 접수 결과를 확인할 수 없어요. 이미 접수되었을 수 있어 중복 전송을 막았어요. 잠시 후 문의 및 신고에서 확인해주세요.";
export function reportLength(detail: string) { return [...detail.trim()].length; }
export function reportValidation(draft: ReportDraft) {
  if (!reportReasons.some(reason => reason.value === draft.reasonCode)) return "신고 사유를 선택해주세요.";
  return reportLength(draft.detail) > REPORT_LIMIT ? "상세 내용은 500자까지 입력할 수 있어요." : "";
}
export function initialReportState(): ReportState { return { reasonCode: null, detail: "", status: "editing", message: "" }; }
export function reportLocked(status: ReportStatus) { return !["editing", "error"].includes(status); }
export function validReportReceipt(result: ReportResponse) {
  return result?.status === "received" && Number.isSafeInteger(result.reportId) && result.reportId > 0;
}

export function createReportController(source: ReportSource | undefined, demo: boolean, targetId: number, publish: (state: ReportState) => void) {
  let state = initialReportState(), disposed = false;
  let active: AbortController | null = null;
  function set(next: ReportState) { state = next; if (!disposed) publish(state); }
  function unavailable(message: string) { set({ ...initialReportState(), status: "unavailable", message }); }
  return {
    change(draft: Partial<ReportDraft>) {
      if (!disposed && !reportLocked(state.status)) set({ ...state, ...draft, status: "editing", message: "" });
    },
    async submit() {
      if (disposed || active || reportLocked(state.status)) return;
      if (demo || source?.kind !== "service") {
        set({ ...state, status: "error", message: demo ? "Demo에서는 실제 신고를 접수하지 않아요. 로그인 후 이용해주세요." : "신고 서버에 연결되지 않았어요." }); return;
      }
      const invalid = reportValidation(state);
      if (invalid) { set({ ...state, status: "error", message: invalid }); return; }
      if (!Number.isSafeInteger(targetId) || targetId <= 0) { unavailable("신고할 콘텐츠를 확인할 수 없어요."); return; }
      const request = new AbortController();
      active = request;
      set({ ...state, status: "submitting", message: "" });
      try {
        source.assertCurrent();
        const detail = state.detail.trim();
        const result = await source.submit({ targetType: "content", targetId, reasonCode: state.reasonCode!, ...(detail ? { detail } : {}) }, request.signal);
        request.signal.throwIfAborted(); source.assertCurrent();
        if (!validReportReceipt(result)) throw new ApiError("Invalid report response", 201, "INVALID_RESPONSE");
        set({ ...state, status: "received", message: "" });
      } catch (error) {
        if (disposed || request.signal.aborted) return;
        if (error instanceof ReportSessionChanged || error instanceof ApiError && error.status === 401) {
          unavailable("로그인 상태가 변경되었거나 만료되었어요. 다시 로그인해주세요."); return;
        }
        try { source.assertCurrent(); } catch { unavailable("로그인 상태가 변경되었어요. 다시 로그인해주세요."); return; }
        let status: ReportStatus = "error", message: string;
        if (error instanceof ReportBlocked) {
          status = error.status; message = status === "duplicate" ? "이미 신고한 콘텐츠예요. 같은 콘텐츠는 한 번만 신고할 수 있어요." : reportUncertainMessage;
        } else if (error instanceof ReportPreflightFailed) message = "로그인 상태를 확인하지 못해 신고를 보내지 않았어요. 다시 시도해주세요.";
        else if (!(error instanceof ApiError) || error.status >= 500 || error.status === 408 || error.code === "INVALID_RESPONSE") {
          status = "uncertain"; message = reportUncertainMessage;
        } else if (error.status === 409) { status = "duplicate"; message = "이미 신고한 콘텐츠예요. 같은 콘텐츠는 한 번만 신고할 수 있어요."; }
        else if (error.status === 404) { status = "missing"; message = "콘텐츠가 삭제되었거나 공개되지 않아 신고할 수 없어요."; }
        else if (error.code === "CSRF_INVALID") message = "보안 토큰이 만료되었어요. 다시 시도해주세요.";
        else if (error.status === 403) { status = "unavailable"; message = "신고할 권한이 없어요. 로그인 및 가입 완료 상태를 확인해주세요."; }
        else message = "신고를 접수하지 못했어요. 입력 내용을 확인하고 다시 시도해주세요.";
        set({ ...state, status, message });
      } finally { if (active === request) active = null; }
    },
    cancel() { disposed = true; active?.abort(); active = null; state = initialReportState(); },
  };
}
